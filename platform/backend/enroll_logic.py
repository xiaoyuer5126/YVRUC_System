"""报名核心逻辑：冲突检测、名额账本、候补流转。

《设计方案》阶段二/阶段三的关键机制：
  · 时段冲突：只比场次的时间区间，重叠即拦截（跨活动，含候补中的场次——
    排了队就等于预留了这段时间）；
  · 名额账本：已确认 + 临近退选占位（seat_held），后者不释放给任何人；
  · 候补按 joined_at 排队，正常退选释放名额 → 队首转正，2 小时确认窗口。

调用容量相关函数前必须持有 locks.shift_lock(shift_id)，见 locks.py。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session as OrmSession

from . import config, credit
from .integrations import wecom
from .models import Activity, Enrollment, Shift, Position, User, now

ACTIVE = ("confirmed", "waitlisted", "pending_confirm")


def overlaps(a1, a2, b1, b2) -> bool:
    """两个时间区间 [start,end) 是否重叠。"""
    return a1 < b2 and b1 < a2


def time_conflicts(db: OrmSession, user: User,
                   target: Shift) -> list[dict]:
    """该用户有效报名中与 target 组时间段重叠的记录（跨活动）。"""
    rows = (db.query(Enrollment, Shift, Activity)
            .join(Shift, Enrollment.shift_id == Shift.id)
            .join(Activity, Enrollment.activity_id == Activity.id)
            .filter(Enrollment.user_id == user.id,
                    Enrollment.status.in_(ACTIVE),
                    Enrollment.shift_id != target.id).all())
    out = []
    for enr, g, act in rows:
        if overlaps(g.start_time, g.end_time, target.start_time, target.end_time):
            out.append({
                "enrollment_id": enr.id, "activity_id": act.id,
                "activity_title": act.title, "shift_name": g.name,
                "time": g.span_text(), "status": enr.status,
                "waiting": enr.status != "confirmed",
            })
    return out


def conflict_message(clashes: list[dict]) -> str:
    parts = []
    for c in clashes[:3]:
        tail = "（候补中）" if c["waiting"] else ""
        parts.append(f"「{c['activity_title']}」{c['shift_name']}"
                     f"（{c['time']}）{tail}")
    return "；".join(parts)


# ======================= 名额账本 =======================

def shift_confirmed_count(db: OrmSession, shift_id: str) -> int:
    """正式报名人数（用于展示「已确认 N 人」）。"""
    return db.query(Enrollment).filter(
        Enrollment.shift_id == shift_id,
        Enrollment.status == "confirmed").count()


def shift_occupied_count(db: OrmSession, shift_id: str) -> int:
    """场次已消耗的名额 = 正式报名 + 临近退选占位。"""
    return db.query(Enrollment).filter(
        Enrollment.shift_id == shift_id,
        or_(Enrollment.status == "confirmed",
            Enrollment.seat_held.is_(True))).count()


def position_confirmed_count(db: OrmSession, position_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.position_id == position_id,
        Enrollment.status == "confirmed").count()


def position_occupied_count(db: OrmSession, position_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.position_id == position_id,
        or_(Enrollment.status == "confirmed",
            Enrollment.seat_held.is_(True))).count()


def has_room(db: OrmSession, shift: Shift, position=None) -> bool:
    """还有没有正式名额。

    名额记在哪一层取决于场次模式：
      assign  报名时还没有岗位可选，名额记在场次上；
      pick    名额记在所选岗位上，场次不设总数。
    """
    if shift.needs_assign():
        return shift_occupied_count(db, shift.id) < shift.capacity
    if position is None:
        return False
    return position_occupied_count(db, position.id) < position.quota


# ======================= 岗位硬性条件 =======================

def unmet_requirements(user: User, position: Position) -> list[str]:
    """志愿者不满足该岗位的哪些硬性条件。空 = 可以报。

    条件是 {标签: 值或值列表}，标签对应志愿者档案里的字段。
    报名时直接拦截，一键分配时同样作为硬约束。
    """
    fields = {"性别": user.gender or "", "学院": user.college or "",
              "年级": str(user.grade or "")}
    unmet = []
    for tag, value in (position.requirement_tags or {}).items():
        mine = fields.get(tag)
        if mine is None:      # 未知标签不拦人，否则一个错别字能把所有人挡在外面
            continue
        allowed = value if isinstance(value, list) else [value]
        allowed = [str(v) for v in allowed if str(v).strip()]
        if allowed and mine not in allowed:
            unmet.append(f"{tag}需为 {'/'.join(allowed)}（您是 {mine or '未填写'}）")
    return unmet


# ======================= 组长 =======================

def elect_leader(db: OrmSession, position: Position) -> Enrollment | None:
    """按报名先后从有意愿的人里选出组长。已有组长则不动。

    组长退选、被调剂走、或岗位刚需要组长时都走这里；一个都没有就返回 None，
    名单页会显红标提示老师手动指定。
    """
    if not position.needs_leader:
        return None
    rows = (db.query(Enrollment)
            .filter(Enrollment.position_id == position.id,
                    Enrollment.status == "confirmed")
            .order_by(Enrollment.joined_at).all())
    if any(r.is_leader for r in rows):
        return next(r for r in rows if r.is_leader)
    head = next((r for r in rows if r.leader_willing), None)
    if head is None:
        return None
    head.is_leader = True
    user = db.get(User, head.user_id)
    activity = db.get(Activity, head.activity_id)
    wecom.send_user(db, user, "leader_appointed", activity=activity.title,
                    position=position.name,
                    position_time=position.time_text(),
                    position_loc=position.location or "待定")
    return head


def refill_leader(db: OrmSession, position_id: str | None) -> None:
    """组长空缺时补位。退选、调岗、取消组长身份之后都要调一次。"""
    if not position_id:
        return
    position = db.get(Position, position_id)
    if position is not None:
        elect_leader(db, position)


# ======================= 候补队列 =======================

def waitlist_rows(db: OrmSession, shift_id: str) -> list[Enrollment]:
    """候补队列：waitlisted + pending_confirm，按排队时间先后。"""
    return (db.query(Enrollment)
            .filter(Enrollment.shift_id == shift_id,
                    Enrollment.status.in_(("waitlisted", "pending_confirm")))
            .order_by(Enrollment.joined_at).all())


def waitlist_position(db: OrmSession, enr: Enrollment) -> int:
    rows = waitlist_rows(db, enr.shift_id)
    for i, row in enumerate(rows):
        if row.id == enr.id:
            return i + 1
    return 0


def promote_head(db: OrmSession, shift: Shift,
                 position: Position | None = None) -> Enrollment | None:
    """有名额释放时，把候补队首转为「待确认」，开 2 小时确认窗并通知。

    只在确实空出名额时才提升：临近退选是占位不释放的，队列原地不动。
    pick 模式下队列是按岗位分的，只提升该岗位的队首。
    """
    if not has_room(db, shift, position):
        return None
    rows = waitlist_rows(db, shift.id)
    if position is not None:
        rows = [r for r in rows if r.position_id == position.id]
    head = next((r for r in rows if r.status == "waitlisted"), None)
    if head is None:
        return None
    head.status = "pending_confirm"
    head.promoted_at = now()
    head.confirm_deadline = now() + timedelta(hours=config.PROMOTE_CONFIRM_HOURS)
    user = db.get(User, head.user_id)
    activity = db.get(Activity, head.activity_id)
    wecom.send_user(db, user, "waitlist_promoted",
                    activity=activity.title, shift=shift.name,
                    deadline=f"{head.confirm_deadline:%m-%d %H:%M}")
    return head


def confirm_promotion(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """确认候补转正：成为正式报名。

    有岗位的场次自动挑剩余名额最多的岗位——岗位之间互斥且无独立时间，
    不需要再做时段过滤。
    """
    shift = db.get(Shift, enr.shift_id)
    enr.status = "confirmed"
    enr.confirm_deadline = None
    credit.reset_abandon_if_confirmed(user)

    # assign 模式候补时还没有岗位：挑一个还满足其硬性条件、剩余名额最多的
    if not enr.position_id and shift.positions:
        ok = [p for p in shift.positions if not unmet_requirements(user, p)]
        if ok:
            best = max(ok, key=lambda p: p.quota - position_occupied_count(db, p.id))
            enr.position_id = best.id
            enr.assigned_at = now()
            enr.assign_forced = False

    if enr.position_id:
        position = db.get(Position, enr.position_id)
        activity = db.get(Activity, enr.activity_id)
        wecom.send_user(db, user, "assignment", activity=activity.title,
                        shift=shift.name, position=position.name,
                        position_time=position.time_text(),
                        position_loc=position.location or "待定")
        elect_leader(db, position)
    return enr


def expire_stale_promotions(db: OrmSession) -> int:
    """扫掉超时未确认的转正：记放弃 → 计数 → 顺延下一位。

    由 main.py 的后台协程每分钟调用；学生打开「我的报名」时也会顺手触发一次，
    让页面上看到的状态和实际一致。
    """
    from .locks import shift_lock

    expired = (db.query(Enrollment)
               .filter(Enrollment.status == "pending_confirm",
                       Enrollment.confirm_deadline < now()).all())
    if not expired:
        return 0

    n = 0
    touched: set[str] = set()
    for enr in expired:
        with shift_lock(enr.shift_id):
            fresh = db.get(Enrollment, enr.id)
            db.refresh(fresh)
            if fresh.status != "pending_confirm" or fresh.confirm_deadline >= now():
                continue      # 用户在这一瞬间点了确认
            credit.register_abandon(db, db.get(User, fresh.user_id))
            fresh.status = "cancelled"
            fresh.cancel_type = "expired"
            db.commit()
            touched.add(fresh.shift_id)
            n += 1
    for sid in touched:
        with shift_lock(sid):
            promote_all(db, db.get(Shift, sid))
            db.commit()
    return n


def promote_all(db: OrmSession, shift: Shift) -> int:
    """把该场次所有空出名额的队列各提升一位。

    pick 模式下队列按岗位分，得逐个岗位看；assign 模式只有一条队。
    """
    if shift.needs_assign():
        return 1 if promote_head(db, shift) else 0
    n = 0
    for position in shift.positions:
        if promote_head(db, shift, position):
            n += 1
    return n
