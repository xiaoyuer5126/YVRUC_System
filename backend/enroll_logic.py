"""报名核心逻辑：冲突检测、名额账本、候补流转。

《设计方案》阶段二/阶段三的关键机制：
  · 时段冲突：只比场次的时间区间，重叠即拦截（跨活动，含候补中的场次——
    排了队就等于预留了这段时间）；
  · 名额账本：已确认 + 临近退选占位（seat_held），后者不释放给任何人；
  · 候补按 joined_at 排队，正常退选释放名额 → 队首转正，2 小时确认窗口。

调用容量相关函数前必须持有 locks.shift_lock(shift_id)，见 locks.py。
"""
from __future__ import annotations

from datetime import datetime, timedelta

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

def group_positions(db: OrmSession, position: Position) -> list[Position]:
    """与该岗位同组的全部岗位（含自己）。

    组名相同即同组；没填组名的岗位自成一组。分组是为了让相邻岗位共用一名
    组长——「东门片区」的引导、检录、医疗点现场本来就是一个人在协调。
    """
    if not (position.group_name or "").strip():
        return [position]
    return (db.query(Position)
            .filter(Position.shift_id == position.shift_id,
                    Position.group_name == position.group_name)
            .order_by(Position.sort).all())


def group_needs_leader(positions: list[Position]) -> bool:
    """组里只要有一个岗位标了需要组长，这个组就要有组长。"""
    return any(p.needs_leader for p in positions)


def elect_leader(db: OrmSession, position: Position) -> Enrollment | None:
    """按报名先后从有意愿的人里选出**本组**组长。已有组长则不动。

    组长退选、被调剂走、或岗位刚需要组长时都走这里；一个都没有就返回 None，
    名单页会显红标提示老师手动指定。组长本人照常占一个岗位名额——
    他也是来服务的，不是纯协调。
    """
    positions = group_positions(db, position)
    if not group_needs_leader(positions):
        return None
    ids = [p.id for p in positions]
    rows = (db.query(Enrollment)
            .filter(Enrollment.position_id.in_(ids),
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
    own = db.get(Position, head.position_id)
    wecom.send_user(db, user, "leader_appointed", activity=activity.title,
                    position=leader_scope_label(positions, own),
                    position_time=own.time_text(),
                    position_loc=own.location or "待定")
    return head


def leader_scope_label(positions: list[Position], own: Position) -> str:
    """通知里怎么称呼组长的管辖范围。"""
    if len(positions) <= 1:
        return own.name
    names = "、".join(p.name for p in positions)
    return f"{positions[0].group_name}（{names}）"


def group_leader(db: OrmSession, position: Position) -> Enrollment | None:
    ids = [p.id for p in group_positions(db, position)]
    return (db.query(Enrollment)
            .filter(Enrollment.position_id.in_(ids),
                    Enrollment.is_leader.is_(True),
                    Enrollment.status == "confirmed").first())


def refill_leader(db: OrmSession, position_id: str | None) -> None:
    """组长空缺时补位。退选、调岗、取消组长身份之后都要调一次。"""
    if not position_id:
        return
    position = db.get(Position, position_id)
    if position is not None:
        elect_leader(db, position)


# ======================= 人员变动流水 =======================

def record_change(db: OrmSession, user: User, enr: Enrollment, action: str,
                  detail: str = "") -> None:
    """记一条人员变动。

    报名与退选此前完全不留痕——审计只记管理者操作，所以活动前一天少了三个人，
    老师可能到现场才发现。这里用同一套审计设施记学生自己的动作，
    actor 就是学生本人；不对外公开（其他志愿者不该看到谁退了）。
    """
    from .audit import log as audit_log

    shift = db.get(Shift, enr.shift_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    where = f"{shift.name}·{position.name}" if position else shift.name
    audit_log(db, actor=user, action=action, target_type="enrollment",
              target_id=enr.id, target_label=user.name,
              activity_id=enr.activity_id,
              summary=f"{user.name}（{user.student_id}）{ROSTER_ACTIONS[action]}"
                      f"「{where}」" + (f"：{detail}" if detail else ""))


ROSTER_ACTIONS = {
    "enroll": "报名", "waitlist": "加入候补", "cancel": "退选",
    "quit_queue": "退出候补", "abandon": "放弃候补转正",
    "promote": "候补转正",
}


# ======================= 候补队列 =======================

def waitlist_rows(db: OrmSession, shift_id: str) -> list[Enrollment]:
    """候补队列：waitlisted + pending_confirm，按排队时间先后。"""
    return (db.query(Enrollment)
            .filter(Enrollment.shift_id == shift_id,
                    Enrollment.status.in_(("waitlisted", "pending_confirm")))
            .order_by(Enrollment.joined_at).all())


def waitlist_position(db: OrmSession, enr: Enrollment) -> int:
    """排在第几位。

    pick 模式的队列是按岗位分的，只能和同岗位的人比——按整个场次排名的话，
    排在「引导岗」第一位的人会因为「检录岗」前面有两个人而被告知「第 3 位」，
    白白以为自己没希望。
    """
    rows = waitlist_rows(db, enr.shift_id)
    if enr.position_id:
        rows = [r for r in rows if r.position_id == enr.position_id]
    for i, row in enumerate(rows):
        if row.id == enr.id:
            return i + 1
    return 0


def auto_promote_deadline(db: OrmSession, shift: Shift) -> datetime:
    """自动候补转正的截止点：报名截止时间与「活动开始前 24 小时」取更晚的那个。

    过了这个点还自动转正是坑人：转正的人只剩不到一天准备，而他此时再退选
    就要按临近退选扣分。之后要不要补人由负责老师看着办，手动转。
    """
    act = db.get(Activity, shift.activity_id)
    cutoff = shift.start_time - timedelta(hours=config.LATE_CANCEL_HOURS)
    if act is not None and act.reg_end and act.reg_end > cutoff:
        cutoff = act.reg_end
    # 再晚也不能晚于活动开始——报名截止时间填到活动之后时兜住
    return min(cutoff, shift.start_time)


def promote_head(db: OrmSession, shift: Shift,
                 position: Position | None = None) -> Enrollment | None:
    """有名额释放时，把候补队首转为「待确认」，开 2 小时确认窗并通知。

    只在确实空出名额时才提升：临近退选是占位不释放的，队列原地不动。
    pick 模式下队列是按岗位分的，只提升该岗位的队首。
    过了自动转正截止点就不再自动提升，改由管理者手动转。
    """
    if now() > auto_promote_deadline(db, shift):
        return None
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


def promote_directly(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """管理者手动把候补转为正式（不走 2 小时确认窗）。

    自动转正截止之后才用得上：那时活动近在眼前，再给两小时确认窗
    只会让名额继续空着。所以直接落实，并把结果通知本人。
    """
    enr.status = "confirmed"
    enr.confirm_deadline = None
    enr.promoted_at = now()
    credit.reset_abandon_if_confirmed(user)
    shift = db.get(Shift, enr.shift_id)
    _settle_position(db, enr, user, shift)
    return enr


def _settle_position(db: OrmSession, enr: Enrollment, user: User,
                     shift: Shift) -> None:
    """转正后落实岗位并通知：assign 模式候补时没选岗位，这里挑一个。"""
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


def confirm_promotion(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """确认候补转正：成为正式报名。

    有岗位的场次自动挑剩余名额最多的岗位——岗位之间互斥且无独立时间，
    不需要再做时段过滤。
    """
    shift = db.get(Shift, enr.shift_id)
    enr.status = "confirmed"
    enr.confirm_deadline = None
    credit.reset_abandon_if_confirmed(user)
    _settle_position(db, enr, user, shift)
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
    过了自动转正截止点整个不动（见 auto_promote_deadline）。
    """
    if shift.needs_assign():
        return 1 if promote_head(db, shift) else 0
    n = 0
    for position in shift.positions:
        if promote_head(db, shift, position):
            n += 1
    return n
