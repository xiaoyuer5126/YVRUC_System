"""报名核心逻辑：冲突检测、名额账本、候补流转。

《设计方案》阶段二/阶段三的关键机制：
  · 时段冲突：只比一级岗位的时间区间，重叠即拦截（跨活动，含候补中的场次——
    排了队就等于预留了这段时间）；
  · 名额账本：已确认 + 临近退选占位（seat_held），后者不释放给任何人；
  · 候补按 joined_at 排队，正常退选释放名额 → 队首转正，2 小时确认窗口。

调用容量相关函数前必须持有 locks.group_lock(group_id)，见 locks.py。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session as OrmSession

from . import config, credit
from .integrations import wecom
from .models import Activity, Enrollment, PositionGroup, Station, User, now

ACTIVE = ("confirmed", "waitlisted", "pending_confirm")


def overlaps(a1, a2, b1, b2) -> bool:
    """两个时间区间 [start,end) 是否重叠。"""
    return a1 < b2 and b1 < a2


def time_conflicts(db: OrmSession, user: User,
                   target: PositionGroup) -> list[dict]:
    """该用户有效报名中与 target 组时间段重叠的记录（跨活动）。"""
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
                "waiting": enr.status != "confirmed",
            })
    return out


def conflict_message(clashes: list[dict]) -> str:
    parts = []
    for c in clashes[:3]:
        tail = "（候补中）" if c["waiting"] else ""
        parts.append(f"「{c['activity_title']}」{c['group_name']}"
                     f"（{c['time']}）{tail}")
    return "；".join(parts)


# ======================= 名额账本 =======================

def group_confirmed_count(db: OrmSession, group_id: str) -> int:
    """正式报名人数（用于展示「已确认 N 人」）。"""
    return db.query(Enrollment).filter(
        Enrollment.group_id == group_id,
        Enrollment.status == "confirmed").count()


def group_held_count(db: OrmSession, group_id: str) -> int:
    """临近退选后不再释放的占位名额。"""
    return db.query(Enrollment).filter(
        Enrollment.group_id == group_id,
        Enrollment.seat_held.is_(True)).count()


def group_occupied_count(db: OrmSession, group_id: str) -> int:
    """已消耗的名额 = 正式报名 + 占位不释放。判断容量一律用它。"""
    return db.query(Enrollment).filter(
        Enrollment.group_id == group_id,
        or_(Enrollment.status == "confirmed",
            Enrollment.seat_held.is_(True))).count()


def station_confirmed_count(db: OrmSession, station_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.station_id == station_id,
        Enrollment.status == "confirmed").count()


def has_room(db: OrmSession, group: PositionGroup, station=None) -> bool:
    """还有没有正式名额：场次容量 +（选定工位时）该工位名额。"""
    if group_occupied_count(db, group.id) >= group.capacity:
        return False
    if station is not None and station_confirmed_count(db, station.id) >= station.quota:
        return False
    return True


# ======================= 候补队列 =======================

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


def promote_head(db: OrmSession, group: PositionGroup) -> Enrollment | None:
    """有名额释放时，把候补队首转为「待确认」，开 2 小时确认窗并通知。

    只在确实空出名额时才提升：临近退选是占位不释放的，队列原地不动。
    """
    if not has_room(db, group):
        return None
    head = next((r for r in waitlist_rows(db, group.id)
                 if r.status == "waitlisted"), None)
    if head is None:
        return None
    head.status = "pending_confirm"
    head.promoted_at = now()
    head.confirm_deadline = now() + timedelta(hours=config.PROMOTE_CONFIRM_HOURS)
    user = db.get(User, head.user_id)
    activity = db.get(Activity, head.activity_id)
    wecom.send_user(db, user, "waitlist_promoted",
                    activity=activity.title, group=group.name,
                    deadline=f"{head.confirm_deadline:%m-%d %H:%M}")
    return head


def confirm_promotion(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """确认候补转正：成为正式报名。

    有二级工位的场次自动挑剩余名额最多的工位——工位之间互斥且无独立时间，
    不需要再做时段过滤。
    """
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

    if enr.station_id:
        station = db.get(Station, enr.station_id)
        activity = db.get(Activity, enr.activity_id)
        wecom.send_user(db, user, "assignment", activity=activity.title,
                        group=group.name, station=station.name,
                        station_time=station.time_text(),
                        station_loc=station.location or "待定")
    return enr


def expire_stale_promotions(db: OrmSession) -> int:
    """扫掉超时未确认的转正：记放弃 → 计数 → 顺延下一位。

    由 main.py 的后台协程每分钟调用；学生打开「我的报名」时也会顺手触发一次，
    让页面上看到的状态和实际一致。
    """
    from .locks import group_lock

    expired = (db.query(Enrollment)
               .filter(Enrollment.status == "pending_confirm",
                       Enrollment.confirm_deadline < now()).all())
    if not expired:
        return 0

    n = 0
    touched: set[str] = set()
    for enr in expired:
        with group_lock(enr.group_id):
            fresh = db.get(Enrollment, enr.id)
            db.refresh(fresh)
            if fresh.status != "pending_confirm" or fresh.confirm_deadline >= now():
                continue      # 用户在这一瞬间点了确认
            credit.register_abandon(db, db.get(User, fresh.user_id))
            fresh.status = "cancelled"
            fresh.cancel_type = "expired"
            db.commit()
            touched.add(fresh.group_id)
            n += 1
    for gid in touched:
        with group_lock(gid):
            promote_head(db, db.get(PositionGroup, gid))
            db.commit()
    return n
