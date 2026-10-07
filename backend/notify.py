"""按人群批量发通知。

两个调用方：管理端的变更/自定义通知，和定时任务里的签到提醒、编辑态超时解冻。
放在这里而不是路由里，是为了让定时任务不必反向 import 路由模块。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from .integrations import msgcenter
from .models import (AFFECTED_FIELD, Activity, Attachment, AuditLog,
                     CheckinRecord, Enrollment, Shift, Position, User)

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
        # 带上活动 id：站内和企业微信里点这条通知都直接进活动页
        # （前端按 data.activity_id 跳，外发链接见 msgcenter._link_for）
        msgcenter.send_user(db, u, template, data={"activity_id": act.id}, **kw)
    return len(users)


# 改了也不单独告诉志愿者的字段：条件要求是给报名把关用的，已经在岗的人
# 不受影响，存的又是一串结构化数据，原样念给人听只会让人看不懂
_QUIET_FIELDS = {"requirement_tags"}
_WHERE = {"group": "场次", "position": "岗位"}
# 一条通知里最多写几项改动。编辑期间改几十处是常事，整摞念完没人看得下去，
# 剩下的让他到活动页的变更记录里看
_MAX_LINES = 8


def _where(act: Activity, row: AuditLog) -> str:
    if row.target_type == "activity":
        return "活动信息"
    name = row.target_label.removeprefix(f"{act.title}·")
    return f"{_WHERE.get(row.target_type, '')}「{name}」"


def _change_line(act: Activity, row: AuditLog) -> str | None:
    """一条留痕在通知里的那一句：有字段明细就逐项写「旧→新」，新建的用摘要。
    只动了不念的字段、或者根本没动（「无实质变化」）的不写。"""
    # 删除另外成句，不用留痕的摘要：那句是给管理者看的，带着「（强制，含 N
    # 条报名记录）」这类对内口径，照原文发给志愿者不合适
    if row.action == "delete":
        return _where(act, row) + "已取消"
    shown = [c for c in row.changes or []
             if not str(c.get("field", "")).startswith("_")
             and c.get("field") not in _QUIET_FIELDS]
    if not shown:
        return None if row.action == "update" else row.summary
    return _where(act, row) + "：" + "；".join(
        f"{c['label']}：{c['old'] or '（空）'}→{c['new'] or '（空）'}" for c in shown)


def _audience(db: OrmSession, row: AuditLog, everyone: set[str]) -> set[str]:
    """这条改动该告诉谁：岗位的改动告诉这个岗位的人，场次的改动告诉这一场
    的人，活动信息的改动告诉全活动的报名者。

    岗位、场次删掉之后库里已经找不到它名下的报名，留痕上记着的受影响的人
    （AFFECTED_FIELD）也算进来，不然恰恰是被取消报名的人收不到通知。
    """
    out = {uid_ for c in row.changes or [] if c.get("field") == AFFECTED_FIELD
           for uid_ in str(c.get("new", "")).split(",") if uid_}
    if row.target_type == "group":
        col, key = Enrollment.shift_id, row.target_id
    elif row.target_type == "position":
        col, key = Enrollment.position_id, row.target_id
    elif row.target_type == "material":
        # 学习材料只挂在岗位上
        att = db.get(Attachment, row.target_id)
        if att is None:
            return out
        col, key = Enrollment.position_id, att.owner_id
    else:
        return out | everyone
    return out | {r[0] for r in db.query(Enrollment.user_id)
                  .filter(col == key, Enrollment.status.in_(ENROLLED)).all()}


def notify_edit_changes(db: OrmSession, act: Activity, since: datetime) -> int:
    """编辑态结束（保存并发布、超时自动解冻）时，把这段时间的改动按人汇总，
    每人一条「活动变更提醒」，只写跟他有关的改动，逐项带上前后值。

    编辑期间每改一处就发一条的话，调一次岗位志愿者能收到十几条，内容还
    互相覆盖，看到最后也拼不出现在到底是什么安排。
    依据是 since 之后对志愿者可见的留痕：它本来就是「你报名之后发生了什么
    变化」那张表，通知和活动页上的变更记录说的是同一件事。
    不提交，由调用方决定事务边界。返回通知了几个人。
    """
    rows = (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id,
                    AuditLog.created_at >= since,
                    AuditLog.visible.is_(True))
            .order_by(AuditLog.created_at).all())
    everyone = {r[0] for r in db.query(Enrollment.user_id)
                .filter(Enrollment.activity_id == act.id,
                        Enrollment.status.in_(ENROLLED)).distinct().all()}
    lines: dict[str, list[str]] = {}
    for row in rows:
        line = _change_line(act, row)
        if line is None:
            continue
        for uid_ in _audience(db, row, everyone):
            mine = lines.setdefault(uid_, [])
            if line not in mine:
                mine.append(line)
    n = 0
    for uid_, mine in lines.items():
        u = db.get(User, uid_)
        if u is None:
            continue
        shown, rest = mine[:_MAX_LINES], len(mine) - _MAX_LINES
        if rest > 0:
            shown.append(f"……另有 {rest} 项改动，详见活动页的变更记录")
        msgcenter.send_user(db, u, "activity_changed",
                            data={"activity_id": act.id},
                            activity=act.title, change_summary="\n".join(shown))
        n += 1
    return n


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
           data: dict | None = None) -> None:
    """给一个人发一条通知。用在没必要做成模板的一次性事务上。"""
    msgcenter.send_user(db, user, "custom", data=data,
                    title=title, content=content)


def notify_managers(db: OrmSession, activity_id: str, title: str,
                    content: str, *, duty: str = "", tab: str = "") -> int:
    """通知该活动的管理者。

    duty 指明这条消息属于哪一摊事（见 ActivityManager.DUTIES）：分了工的人
    只收自己那摊，没分工的人什么都收。一场大活动十几个管理者，每条变动都
    群发的结果就是所有人都把通知关掉。
    tab 是点开后直接打开的活动管理页签（见 msgcenter.MANAGE_TABS）。
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
            # 点开进活动管理页：这些消息说的都是管理者要在那儿办的事
            data = {"activity_id": activity_id, "route": "manage"}
            if tab:
                data["tab"] = tab
            msgcenter.send_user(db, u, "custom", data=data,
                            title=title, content=content)
            n += 1
    return n


def notify_locked_dropout(db: OrmSession, enr, user: User) -> None:
    """名单锁定后的减员立刻告诉管理者：候补不会自动顶上，得他来决定补不补。

    只在锁定后推：报名期每天几十条变动，逐条推等于让老师关掉通知。
    正文带当前人数，老师一眼知道缺不缺人。
    """
    from .enroll_logic import shift_enrolled_count
    from .models import Activity, Position, Shift

    shift = db.get(Shift, enr.shift_id)
    act = db.get(Activity, enr.activity_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    where = f"{shift.name}·{position.name}" if position else shift.name
    enrolled = shift_enrolled_count(db, shift.id)
    capacity = shift.total_capacity()
    gap = max(0, capacity - enrolled)
    notify_managers(
        db, act.id,
        f"[锁定后减员] {act.title}",
        f"{user.name}（{user.student_id}）退出了「{where}」，因名单已锁定，"
        f"停止候补自动转正，如需补人，请在志愿者管理页面手动转正。\n"
        f"该场次当前 {enrolled}/{capacity} 人"
        + (f"，缺 {gap} 人。" if gap else "，人数已满。"),
        duty="roster", tab="roster")
