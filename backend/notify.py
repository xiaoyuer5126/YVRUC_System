"""按人群批量发通知。

两个调用方：管理端的变更/自定义通知，和定时任务里的签到提醒。
放在这里而不是路由里，是为了让定时任务不必反向 import 路由模块。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .integrations import wecom
from .models import Activity, CheckinRecord, Enrollment, Shift, Position, User

ENROLLED = ("confirmed", "waitlisted", "pending_confirm")


def _users_of(db: OrmSession, user_ids) -> list[User]:
    """两步查询：先 DISTINCT user_id 再取实体。

    整行去重会把 JSON 列（User.roles）带进相等比较，PostgreSQL 的 json
    类型没有等号运算符，会直接报错。
    """
    out = []
    for (uid_,) in user_ids:
        u = db.get(User, uid_)
        if u is not None:
            out.append(u)
    return out


def notify_enrolled(db: OrmSession, act: Activity, template: str, **kw) -> int:
    """通知该活动所有有效报名者（含候补）。不提交，由调用方决定事务边界。"""
    ids = (db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status.in_(ENROLLED))
           .distinct().all())
    users = _users_of(db, ids)
    for u in users:
        wecom.send_user(db, u, template, **kw)
    return len(users)


def notify_checkin(db: OrmSession, g: Shift, *, skip_checked_in: bool = True,
                   position_ids: list[str] | None = None) -> int:
    """给某场次的正式志愿者发签到提醒，地点按各自岗位给到人。

    position_ids 限定范围：现场催场往往是「东门那三个人没到」，
    整场次群发会打扰到已经在岗的其他岗位。
    """
    act = db.get(Activity, g.activity_id)
    q = (db.query(Enrollment)
         .filter(Enrollment.shift_id == g.id,
                 Enrollment.status == "confirmed"))
    if position_ids:
        q = q.filter(Enrollment.position_id.in_(position_ids))
    rows = q.all()
    checked: set[str] = set()
    if skip_checked_in:
        checked = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == g.id).all()}
    n = 0
    for enr in rows:
        if enr.user_id in checked:
            continue
        u = db.get(User, enr.user_id)
        if u is None:
            continue
        position = db.get(Position, enr.position_id) if enr.position_id else None
        loc = (position.location if position and position.location
               else act.location or "活动集合点")
        wecom.send_user(db, u, "checkin_reminder", activity=act.title,
                        group=g.name, loc=loc)
        n += 1
    return n


def notify_managers(db: OrmSession, activity_id: str, title: str,
                    content: str) -> int:
    """通知该活动的全部管理者。"""
    from .models import ActivityManager

    ids = [r[0] for r in db.query(ActivityManager.user_id)
           .filter(ActivityManager.activity_id == activity_id).all()]
    n = 0
    for uid_ in ids:
        u = db.get(User, uid_)
        if u is not None:
            wecom.send_user(db, u, "custom", title=title, content=content)
            n += 1
    return n


def notify_late_dropout(db: OrmSession, enr, user: User) -> None:
    """临近活动的减员立刻告诉管理者。

    只在距开始不足 LATE_CANCEL_HOURS 时推：报名期每天几十条变动，
    逐条推等于让老师关掉通知。正文带当前人数，老师一眼知道缺不缺人。
    """
    from . import config
    from .enroll_logic import shift_confirmed_count
    from .models import Activity, Position, Shift, now

    shift = db.get(Shift, enr.shift_id)
    hours_left = (shift.start_time - now()).total_seconds() / 3600
    if hours_left < 0 or hours_left >= config.LATE_CANCEL_HOURS:
        return
    act = db.get(Activity, enr.activity_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    where = f"{shift.name}·{position.name}" if position else shift.name
    confirmed = shift_confirmed_count(db, shift.id)
    capacity = shift.total_capacity()
    gap = max(0, capacity - confirmed)
    notify_managers(
        db, act.id,
        f"[临近减员] {act.title}",
        f"{user.name}（{user.student_id}）退出了「{where}」，"
        f"距开始不足 {config.LATE_CANCEL_HOURS} 小时。\n"
        f"该场次当前 {confirmed}/{capacity} 人"
        + (f"，缺 {gap} 人。" if gap else "，人数已满。"))
