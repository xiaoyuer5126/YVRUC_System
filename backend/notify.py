"""按人群批量发通知。

两个调用方：管理端的变更/自定义通知，和定时任务里的签到提醒。
放在这里而不是路由里，是为了让定时任务不必反向 import 路由模块。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .integrations import msgcenter
from .models import Activity, CheckinRecord, Enrollment, Shift, Position, User

ENROLLED = ("enrolled", "waitlisted", "pending_confirm")


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
        msgcenter.send_user(db, u, template, **kw)
    return len(users)


def notify_checkin(db: OrmSession, g: Shift, *, skip_checked_in: bool = True,
                   position_ids: list[str] | None = None,
                   need_ack: bool = False,
                   batch_id: str | None = None) -> int:
    """给某场次的正式志愿者发签到提醒，地点按各自岗位给到人。

    position_ids 限定范围：现场催场往往是「东门那三个人没到」，
    整场次群发会打扰到已经在岗的其他岗位。
    need_ack=True 时要求本人点「我已知悉」，管理端按 batch_id 看谁还没回。
    """
    act = db.get(Activity, g.activity_id)
    q = (db.query(Enrollment)
         .filter(Enrollment.shift_id == g.id,
                 Enrollment.status == "enrolled",
                 # 请了假的人不再催他签到：他已经说过来不了了
                 Enrollment.excused.is_(False)))
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
        # 培训/彩排在自己的地点，不在岗位上；地点按「场次 → 岗位 → 活动」取
        loc = (g.location or (position.location if position else "")
               or act.location or "活动集合点")
        if g.is_attached():
            label = g.kind_label()          # 「培训」「彩排」，不是「待分配」
        else:
            label = position.name if position else "待分配"
        msgcenter.send_user(db, u, "checkin_reminder", activity=act.title,
                        shift=g.name, position=label,
                        loc=loc,
                        # activity_id 是「提醒未签收」判权限的依据，不能省
                        data={"activity_id": act.id},
                        need_ack=need_ack, batch_id=batch_id)
        n += 1
    return n


def notify(db: OrmSession, user: User, title: str, content: str, *,
           data: dict | None = None, need_ack: bool = False) -> None:
    """给一个人发一条通知。用在没必要做成模板的一次性事务上。"""
    msgcenter.send_user(db, user, "custom", data=data, need_ack=need_ack,
                    title=title, content=content)


def notify_managers(db: OrmSession, activity_id: str, title: str,
                    content: str, *, duty: str = "") -> int:
    """通知该活动的管理者。

    duty 指明这条消息属于哪一摊事（见 ActivityManager.DUTIES）：分了工的人
    只收自己那摊，没分工的人什么都收。一场大活动十几个管理者，每条变动都
    群发的结果就是所有人都把通知关掉。
    """
    from .models import ActivityManager

    rows = (db.query(ActivityManager)
            .filter(ActivityManager.activity_id == activity_id).all())
    n = 0
    for row in rows:
        if duty and row.duties and duty not in row.duties:
            continue
        u = db.get(User, row.user_id)
        if u is not None:
            # 带上活动 id：这些消息说的都是某一场活动的事，点开就该进得去
            msgcenter.send_user(db, u, "custom", data={"activity_id": activity_id},
                            title=title, content=content)
            n += 1
    return n


def notify_late_dropout(db: OrmSession, enr, user: User) -> None:
    """临近活动的减员立刻告诉管理者。

    只在距开始不足 LATE_CANCEL_HOURS 时推：报名期每天几十条变动，
    逐条推等于让老师关掉通知。正文带当前人数，老师一眼知道缺不缺人。
    """
    from . import config
    from .enroll_logic import shift_enrolled_count
    from .models import Activity, Position, Shift, now

    shift = db.get(Shift, enr.shift_id)
    hours_left = (shift.start_time - now()).total_seconds() / 3600
    if hours_left < 0 or hours_left >= config.LATE_CANCEL_HOURS:
        return
    act = db.get(Activity, enr.activity_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    where = f"{shift.name}·{position.name}" if position else shift.name
    enrolled = shift_enrolled_count(db, shift.id)
    capacity = shift.total_capacity()
    gap = max(0, capacity - enrolled)
    notify_managers(
        db, act.id,
        f"[临近减员] {act.title}",
        f"{user.name}（{user.student_id}）退出了「{where}」，"
        f"距开始不足 {config.LATE_CANCEL_HOURS} 小时。\n"
        f"该场次当前 {enrolled}/{capacity} 人"
        + (f"，缺 {gap} 人。" if gap else "，人数已满。"),
        duty="roster")
