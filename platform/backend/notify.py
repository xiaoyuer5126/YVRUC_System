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


def notify_checkin(db: OrmSession, g: Shift, *,
                   skip_checked_in: bool = True) -> int:
    """给某场次的已确认志愿者发签到提醒，地点按各自岗位给到人。"""
    act = db.get(Activity, g.activity_id)
    rows = (db.query(Enrollment)
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.status == "confirmed").all())
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
