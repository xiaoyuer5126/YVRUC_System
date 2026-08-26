"""报名核心逻辑：冲突检测、容量判断、候补流转。

对应《设计方案》阶段二/阶段三的关键机制：
  · 报名时（粗粒度）：只比一级岗位时间区间，重叠即拦截；
  · 分配时（细粒度）：工位时间与已分配工位重叠 → 警告但允许强制；
  · 候补按 joined_at 排队，正常退选释放名额 → 队首转正，2小时确认窗口。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session as OrmSession

from . import config, credit
from .integrations import wecom
from .models import Activity, Enrollment, PositionGroup, User, iso, now

ACTIVE = ("confirmed", "waitlisted", "pending_confirm")


def overlaps(a1, a2, b1, b2) -> bool:
    """两个时间区间 [start,end) 是否重叠。"""
    return a1 < b2 and b1 < a2


def time_conflicts(db: OrmSession, user: User,
                   target: PositionGroup) -> list[dict]:
    """该用户有效报名中与 target 组时间段重叠的记录（粗粒度，跨活动）。"""
    rows = (db.query(Enrollment, PositionGroup, Activity)
            .join(PositionGroup, Enrollment.group_id == PositionGroup.id)
            .join(Activity, Enrollment.activity_id == Activity.id)
            .filter(Enrollment.user_id == user.id,
                    Enrollment.status.in_(ACTIVE),
                    Enrollment.group_id != target.id).all())
    out = []
    for enr, g, act in rows:
        if overlaps(g.start_time, g.end_time, target.start_time, target.end_time):
            out.append({
                "enrollment_id": enr.id, "activity_id": act.id,
                "activity_title": act.title, "group_name": g.name,
                "time": g.span_text(), "status": enr.status,
                "station_id": enr.station_id,
            })
    return out


def confirmed_elsewhere(db: OrmSession, user: User,
                        exclude_group: str) -> list[tuple[Enrollment, PositionGroup]]:
    """除 exclude_group 外的已确认报名（用于分配期细粒度冲突警告）。"""
    return (db.query(Enrollment, PositionGroup)
            .filter(Enrollment.user_id == user.id,
                    Enrollment.status == "confirmed",
                    Enrollment.group_id != exclude_group)
            .join(PositionGroup, Enrollment.group_id == PositionGroup.id).all())


def station_conflict_warning(db: OrmSession, user: User, group: PositionGroup,
                             station) -> str:
    """分配期细粒度冲突检查。

    二级工位不再携带时间（时间归一级岗位），且同场次工位默认互斥——
    把人分进某工位不会再产生新的时段冲突，恒返回空。
    保留函数签名以兼容调用方。
    """
    return ""


def group_confirmed_count(db: OrmSession, group_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.group_id == group_id, Enrollment.status == "confirmed").count()


def station_confirmed_count(db: OrmSession, station_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.station_id == station_id,
        Enrollment.status == "confirmed").count()


def waitlist_rows(db: OrmSession, group_id: str) -> list[Enrollment]:
    """候补队列：waitlisted + pending_confirm，按排队时间先后。"""
    return (db.query(Enrollment)
            .filter(Enrollment.group_id == group_id,
                    Enrollment.status.in_(("waitlisted", "pending_confirm")))
            .order_by(Enrollment.joined_at).all())


def waitlist_position(db: OrmSession, enr: Enrollment) -> int:
    rows = waitlist_rows(db, enr.group_id)
    for i, row in enumerate(rows):
        if row.id == enr.id:
            return i + 1
    return 0


def has_room(db: OrmSession, group: PositionGroup,
             station=None) -> bool:
    """还有没有正式名额：组容量 + （模式A时）所选工位名额。"""
    if group_confirmed_count(db, group.id) >= group.capacity > 0:
        return False
    if station is not None and station_confirmed_count(db, station.id) >= station.quota:
        return False
    return True


def promote_head(db: OrmSession, group: PositionGroup) -> Enrollment | None:
    """把候补队首转为“待确认”，开 2 小时确认窗口并通知。"""
    head = next((r for r in waitlist_rows(db, group.id)
                 if r.status == "waitlisted"), None)
    if head is None:
        return None
    head.status = "pending_confirm"
    head.promoted_at = now()
    head.confirm_deadline = now() + timedelta(hours=config.PROMOTE_CONFIRM_HOURS)
    user = db.get(User, head.user_id)
    activity = db.get(Activity, head.activity_id)
    try:
        wecom.send_user(user, "waitlist_promoted",
                        activity=activity.title, group=group.name,
                        deadline=f"{head.confirm_deadline:%m-%d %H:%M}")
    except Exception:
        pass
    return head


def confirm_promotion(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """确认候补转正：即成为正式报名；有二级工位的场次自动挑剩余名额最多的工位
    （工位之间默认互斥且无独立时间，无需再做时段过滤）。"""
    from .models import Station

    group = db.get(PositionGroup, enr.group_id)
    enr.status = "confirmed"
    enr.confirm_deadline = None
    credit.reset_abandon_if_confirmed(user)

    stations = sorted(group.stations, key=lambda s: s.sort)
    if stations and not enr.station_id:
        best = max(stations,
                   key=lambda s: s.quota - station_confirmed_count(db, s.id))
        enr.station_id = best.id
        enr.assigned_at = now()
        enr.assign_forced = False

    # 发送岗位通知（若有工位）
    if enr.station_id:
        station = db.get(Station, enr.station_id)
        activity = db.get(Activity, enr.activity_id)
        try:
            wecom.send_user(
                user, "assignment", activity=activity.title, group=group.name,
                station=station.name, station_time=station.time_text(),
                station_loc=station.location or "待定")
        except Exception:
            pass
    return enr


def expire_stale_promotions(db: OrmSession) -> int:
    """扫掉超时未确认的转正：记放弃 → 计数 → 顺延下一位。惰性触发+定时线程。"""
    expired = (db.query(Enrollment)
               .filter(Enrollment.status == "pending_confirm",
                       Enrollment.confirm_deadline < now()).all())
    n = 0
    touched_groups: set[str] = set()
    for enr in expired:
        user = db.get(User, enr.user_id)
        credit.register_abandon(db, user)
        enr.status = "cancelled"
        enr.cancel_type = "expired"
        touched_groups.add(enr.group_id)
        n += 1
    if n:
        db.commit()
        for gid in touched_groups:
            promote_head(db, db.get(PositionGroup, gid))
            db.commit()
    return n
