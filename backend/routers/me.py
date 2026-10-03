"""学生端「我的」：报名记录、消息通知、志愿时长。"""
from __future__ import annotations

import re
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from .. import config
from .. import hours as hours_flow
from ..database import db_session
from ..deps import current_user
from ..integrations import msgcenter
from ..models import (Activity, CheckinRecord, Enrollment,
                      HoursAppeal, HoursRecord, Notification, Shift, Position,
                      User, iso, now, uid)
from ..notify import notify_managers
from ..serializers import iso_of

router = APIRouter(prefix="/api/me", tags=["me"])

ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CARD_CHECK = "10X98765432"


def valid_id_card(value: str) -> bool:
    """18 位居民身份证号校验（含末位校验码）。

    在校学生均为 18 位号，不放行 15 位旧号——填错一位就是一条对不上人的记录，
    而这个号是志愿服务记录对到人身上的依据。
    """
    if not re.fullmatch(r"\d{17}[\dX]", value):
        return False
    total = sum(int(c) * w for c, w in zip(value[:17], ID_CARD_WEIGHTS))
    return value[17] == ID_CARD_CHECK[total % 11]


def _checkin_state(db: OrmSession, enr: Enrollment, g: Shift) -> dict:
    """这一行现在能不能签，只回状态、**不回码**。

    码要本人到现场扫出来——接口把码发给每个报名的人，「我的报名」里点一下
    就能签，动态码防代签那套就白做了。这里只回答「按钮要不要亮、亮的是签到
    还是签退、点了之后调扫码还是调定位」。

    method：有坐标锚点就是 geo（点到场按钮、平台比距离），否则是 scan
    （调起扫一扫）。两条路都不能凭这个字段直接签成——见 routers/checkin.py。
    """
    from .checkin import geo_anchor, resolve_task

    task = resolve_task(db, g.id, enr.position_id)
    if task is None:
        return {"state": "none", "method": "scan", "open": False}
    method = "geo" if geo_anchor(db, task) else "scan"
    t = now()
    # 和扫码那边同一个时段（老数据留空的一端按默认补上），按钮亮不亮才跟
    # 扫了能不能成对得上。签退也在这段里：过了「to」就不能自己签退了
    start, end = task.window(g)
    if t < start:
        state = "early"
    elif t > end:
        state = "expired"
    else:
        state = "active"
    return {"state": state, "method": method, "open": state == "active",
            "from": iso_of(start), "to": iso_of(end)}


def _enrollment_row(db: OrmSession, enr: Enrollment) -> dict:
    act = db.get(Activity, enr.activity_id)
    g = db.get(Shift, enr.shift_id)
    st = db.get(Position, enr.position_id) if enr.position_id else None
    rec = db.query(CheckinRecord).filter(
        CheckinRecord.user_id == enr.user_id,
        CheckinRecord.shift_id == enr.shift_id).first()
    ck = (_checkin_state(db, enr, g) if enr.status == "enrolled" else None)
    # 刚签完到的这段时间里点了「签退」也是白点：服务端十分钟内不认（防手滑，
    # 见 config.CHECKOUT_MIN_GAP_MINUTES），扫完码只会被挡回来。把「几点之后
    # 才能签退」一并给出，前端点下去就能当场说明白，不用先支使人去扫一遍。
    if ck is not None and rec is not None and rec.checkout_at is None:
        ck["checkout_from"] = iso(rec.checkin_at + timedelta(
            minutes=config.CHECKOUT_MIN_GAP_MINUTES))
    return {
        "id": enr.id,
        "activity": {"id": act.id, "title": act.title,
                     "recruit_mode": act.recruit_mode,
                     "status": act.status, "location": act.location},
        "shift": {"id": g.id, "name": g.name, "time_text": g.span_text(),
                  "start_time": iso_of(g.start_time),
                  "end_time": iso_of(g.end_time),
                  "location": g.location,
                  # 培训/彩排随服务场次的报名产生，学生不能单独退，只能请假
                  "kind": g.kind, "kind_label": g.kind_label(),
                  "attached": g.is_attached(),
                  "hours_mode": g.hours_mode},
        "position": ({"id": st.id, "name": st.name, "time_text": st.time_text(),
                     "location": st.location} if st else None),
        "status": enr.status, "cancel_type": enr.cancel_type,
        "excused": enr.excused, "excuse_reason": enr.excuse_reason,
        "seat_held": enr.seat_held,
        "is_leader": enr.is_leader,
        "allow_reassign": enr.allow_reassign,
        "joined_at": iso_of(enr.joined_at),
        "confirm_deadline": iso_of(enr.confirm_deadline),
        "checked_in": rec is not None,
        "checkin_at": iso(rec.checkin_at) if rec else None,
        "checkout_at": iso(rec.checkout_at) if rec else None,
        "checkin": ck,
        # 联络群二维码只给已报上的人，「我的报名」里按需点开
        "contact_group_qr": (act.contact_group_qr
                             if enr.status == "enrolled" else ""),
    }


@router.get("/enrollments")
def my_enrollments(db: OrmSession = Depends(db_session),
                   user=Depends(current_user)):
    from .. import enroll_logic as logic
    logic.expire_stale_promotions(db)
    rows = (db.query(Enrollment)
            .filter(Enrollment.user_id == user.id)
            .order_by(Enrollment.joined_at.desc()).all())
    # 同组去重：只显示最新一条状态
    seen: set[str] = set()
    out = []
    for r in rows:
        if r.shift_id in seen and r.status == "cancelled":
            continue
        # 筛选时录到了别的场次：那几条意向记录没有信息量，不列
        if r.cancel_type == "not_chosen":
            continue
        if r.status != "cancelled":
            seen.add(r.shift_id)
        out.append(_enrollment_row(db, r))
    return out


@router.get("/enrollments/{enrollment_id}/team")
def my_team(enrollment_id: str, db: OrmSession = Depends(db_session),
            user=Depends(current_user)):
    """组长看本组组员的联系方式。

    范围是**同一岗位组**（组名相同的岗位共用一名组长），这正是组长的管辖
    范围。这是把一批同学的手机号给另一个同学，所以每次查看都留痕——
    志愿者侧看不到这条记录，出事时管理员查得到。
    """
    from .. import enroll_logic as logic
    from ..audit import log as audit_log

    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if not enr.is_leader:
        raise HTTPException(403, "只有组长可以查看组员联系方式")
    position = db.get(Position, enr.position_id) if enr.position_id else None
    if position is None:
        raise HTTPException(409, "您还没有岗位，暂时没有组员")

    siblings = logic.group_positions(db, position)
    ids = [p.id for p in siblings]
    rows = (db.query(Enrollment, User)
            .join(User, Enrollment.user_id == User.id)
            .filter(Enrollment.position_id.in_(ids),
                    Enrollment.status == "enrolled")
            .order_by(User.name).all())
    members = [{
        "name": u.name, "student_id": u.student_id, "college": u.college,
        "phone": u.phone, "wechat": u.wechat,
        "position": next((p.name for p in siblings
                          if p.id == e.position_id), ""),
        "is_leader": e.is_leader, "is_me": u.id == user.id,
    } for e, u in rows]

    act = db.get(Activity, enr.activity_id)
    shift = db.get(Shift, enr.shift_id)
    audit_log(db, actor=user, action="view_team", target_type="enrollment",
              target_id=enr.id, target_label=user.name,
              activity_id=enr.activity_id,
              summary=f"组长 {user.name} 查看了「{shift.name}·"
                      f"{logic.leader_scope_label(siblings, position)}」"
                      f"{len(members)} 位组员的联系方式")
    db.commit()
    return {"activity_title": act.title if act else "",
            "shift_name": shift.name if shift else "",
            "scope": logic.leader_scope_label(siblings, position),
            "members": members}


@router.get("/notifications")
def my_notifications(page: int = Query(1, ge=1),
                     size: int = Query(20, ge=1, le=100),
                     db: OrmSession = Depends(db_session),
                     user=Depends(current_user)):
    """消息列表，新的在前，分页（页面上是「加载更多」）。

    id 兜底排序：同一秒发出的几条通知 created_at 相同，只按时间排的话
    翻页时它们的先后不固定，会一条出现两次、另一条一次都不出现。
    """
    mine = db.query(Notification).filter(Notification.user_id == user.id)
    total = mine.count()
    rows = (mine.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * size).limit(size).all())
    unread = (db.query(func.count(Notification.id))
              .filter(Notification.user_id == user.id,
                      Notification.read.is_(False)).scalar() or 0)
    pending_ack = (db.query(func.count(Notification.id))
                   .filter(Notification.user_id == user.id,
                           Notification.need_ack.is_(True),
                           Notification.acked_at.is_(None)).scalar() or 0)
    # page：点开去哪一页。由后端按通知算，企业微信卡片的跳转链接出自同一份
    # 规则（msgcenter.page_for），站内和企业微信里点同一条不会各去各的
    return {"unread": unread, "pending_ack": pending_ack, "total": total,
            "has_more": page * size < total,
            "items": [{**n.out(), "page": msgcenter.page_for(n.template_key, n.data)}
                      for n in rows]}


@router.get("/notifications/unread")
def notifications_unread(db: OrmSession = Depends(db_session),
                         user: User = Depends(current_user)):
    """只要两个数：未读数、待签收数。

    给顶上那个 30 秒轮询用。走完整的 /notifications 会把一页通知的
    正文一并拉回来，而轮询只用到其中一个数字——开着的每个页面每 30 秒拉
    一次几十 KB 的正文，纯浪费。
    """
    return {
        "unread": (db.query(func.count(Notification.id))
                   .filter(Notification.user_id == user.id,
                           Notification.read.is_(False)).scalar() or 0),
        "pending_ack": (db.query(func.count(Notification.id))
                        .filter(Notification.user_id == user.id,
                                Notification.need_ack.is_(True),
                                Notification.acked_at.is_(None)).scalar() or 0),
    }


@router.post("/notifications/read-all")
def read_all(db: OrmSession = Depends(db_session), user=Depends(current_user)):
    """全部标为已读。待签收的通知只标已读，签收仍需本人逐条点。"""
    (db.query(Notification)
     .filter(Notification.user_id == user.id, Notification.read.is_(False))
     .update({"read": True}, synchronize_session=False))
    db.commit()
    return {"ok": True}


@router.post("/notifications/{notification_id}/read")
def read_notification(notification_id: str,
                      db: OrmSession = Depends(db_session),
                      user=Depends(current_user)):
    """标记一条已读。点开哪条算哪条，不用非得「全部已读」。"""
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "通知不存在")
    if not n.read:
        n.read = True
        db.commit()
    return {"ok": True}


@router.post("/notifications/{notification_id}/ack")
def ack_notification(notification_id: str,
                     db: OrmSession = Depends(db_session),
                     user=Depends(current_user)):
    """签收一条通知。已读只说明打开过消息中心，签收才是本人确认看到了。"""
    n = db.get(Notification, notification_id)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, "通知不存在")
    if not n.need_ack:
        raise HTTPException(400, "该通知无需签收")
    if n.acked_at is None:
        n.acked_at = now()
        n.read = True
        db.commit()
    return {"ok": True, "acked_at": iso(n.acked_at)}


@router.get("/hours")
def my_hours(db: OrmSession = Depends(db_session), user=Depends(current_user)):
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.user_id == user.id)
            .order_by(HoursRecord.created_at.desc()).all())
    # 选了劳务的那次不计入时长：账上只有志愿时长。党校同学的那几场也不计入：
    # 不能同时拿去做学分认定（见 hours.PARTY_SCHOOL），明细里照样列、带标签
    party = hours_flow.party_school_activities(db, user.id)
    counted = [r for r in rows if r.counts_hours() and r.activity_id not in party]
    total = sum(r.hours for r in counted)
    acts = {a.id: a for a in db.query(Activity)
            .filter(Activity.id.in_([r.activity_id for r in rows] or [""])).all()}
    locked = sum(r.hours for r in counted
                 if acts.get(r.activity_id) and acts[r.activity_id].hours_locked())
    appeals = {a.record_id: a for a in db.query(HoursAppeal)
               .filter(HoursAppeal.user_id == user.id)
               .order_by(HoursAppeal.created_at).all()}
    out = []
    for r in rows:
        act = acts.get(r.activity_id)
        a = appeals.get(r.id)
        # 确认与申诉只在「已核准、还没锁定、且没到截止时间」这段窗口里开着
        open_window = bool(
            act and act.hours_approved_at and not act.hours_locked()
            and (act.appeal_deadline is None or now() < act.appeal_deadline))
        pay_pos = hours_flow.pay_position(db, r.activity_id, user.id)
        out.append({"id": r.id, "activity_title": act.title if act else "",
                    "activity_id": r.activity_id,
                    # 补记录活动不公开：前端别给它挂活动详情的链接
                    "record_only": bool(act and act.is_record()),
                    "party_school": r.activity_id in party,
                    "hours": r.hours, "parts": r.parts or [],
                    "reward": r.reward, "reward_label": r.reward_label(),
                    "confirmed_at": iso_of(r.confirmed_at),
                    "approved": bool(act and act.hours_approved_at),
                    "locked": bool(act and act.hours_locked()),
                    "appeal_deadline": iso_of(act.appeal_deadline) if act else None,
                    "created_at": iso_of(r.created_at),
                    # 有申诉在处理中就先别确认；处理完再核准一次会重新问
                    "can_confirm": open_window and (
                        a is None or a.status != "pending"),
                    # 本人说了「确认无误」就不再给申诉入口：那句话是对着这个
                    # 数字说的，说过之后又反悔，该找负责人核对，而不是自己再点
                    "can_appeal": (open_window and r.confirmed_at is None
                                   and (a is None or a.status == "rejected")),
                    "pay_allowed": pay_pos is not None,
                    "pay_note": pay_pos.pay_note if pay_pos else "",
                    "appeal": ({"status": a.status, "label": a.status_label(),
                                "claimed_hours": a.claimed_hours,
                                "reason": a.reason, "reply": a.reply}
                               if a else None)})
    return {"total": round(total, 1), "locked": round(locked, 1),
            "records": out}


class ConfirmHoursBody(BaseModel):
    reward: str = "hours"      # hours 记志愿时长 / pay 劳务（岗位可选劳务时才行）


@router.post("/hours/{record_id}/confirm")
def confirm_hours(record_id: str, body: ConfirmHoursBody,
                  db: OrmSession = Depends(db_session), user=Depends(current_user)):
    """本人确认这条时长没问题，并选定报酬方式。截止前可以改主意再点一次。"""
    r = db.get(HoursRecord, record_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "时长记录不存在")
    act = db.get(Activity, r.activity_id)
    if act is None or act.hours_approved_at is None:
        raise HTTPException(409, "该时长尚未核准")
    if act.hours_locked():
        raise HTTPException(409, "该活动的时长已锁定")
    if act.appeal_deadline and now() > act.appeal_deadline:
        raise HTTPException(409, f"确认已于 {act.appeal_deadline:%m-%d %H:%M} 截止，"
                                 f"已按志愿时长记")
    if (db.query(HoursAppeal)
            .filter(HoursAppeal.record_id == r.id,
                    HoursAppeal.status == "pending").first()):
        raise HTTPException(409, "您的申诉正在处理中，处理完成后会重新核准")
    if body.reward not in HoursRecord.REWARD_LABELS:
        raise HTTPException(400, "报酬方式只能是 志愿时长 / 劳务")
    if body.reward == "pay" and hours_flow.pay_position(db, act.id, user.id) is None:
        raise HTTPException(400, "您的岗位没有开放劳务，只能记志愿时长")
    r.reward = body.reward
    r.confirmed_at = now()
    db.commit()
    return {"ok": True, "reward": r.reward, "reward_label": r.reward_label()}


class AppealBody(BaseModel):
    claimed_hours: float
    reason: str


@router.post("/hours/{record_id}/appeal")
def appeal_hours(record_id: str, body: AppealBody,
                 db: OrmSession = Depends(db_session), user=Depends(current_user)):
    """对某条时长提异议。一条记录同时只能有一个待处理的申诉。"""
    r = db.get(HoursRecord, record_id)
    if r is None or r.user_id != user.id:
        raise HTTPException(404, "时长记录不存在")
    act = db.get(Activity, r.activity_id)
    if act is None or act.hours_approved_at is None:
        raise HTTPException(409, "该时长尚未核准")
    if act.hours_locked():
        raise HTTPException(409, "该活动的时长已锁定，不可再申诉；请联系负责人")
    if act.appeal_deadline and now() > act.appeal_deadline:
        raise HTTPException(
            409, f"申诉已于 {act.appeal_deadline:%m-%d %H:%M} 截止；"
                 f"如确有问题请联系负责人")
    if r.confirmed_at is not None:
        # 前端已经不给入口了，这里再挡一道：确认过的时长不能再申诉，
        # 否则「确认无误」就成了可以反悔的一句话
        raise HTTPException(409, "您已确认过这条时长，如确有问题请联系负责人")
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(400, "请写明具体情况（至少 5 个字）")
    if body.claimed_hours <= 0:
        raise HTTPException(400, "申报时长必须大于 0")
    if body.claimed_hours > 24:
        raise HTTPException(400, "申报时长不能超过 24 小时")
    pending = (db.query(HoursAppeal)
               .filter(HoursAppeal.record_id == r.id,
                       HoursAppeal.status == "pending").first())
    if pending is not None:
        raise HTTPException(409, "已有一条申诉在处理中，请等待答复")
    db.add(HoursAppeal(id=uid("apl"), record_id=r.id, activity_id=r.activity_id,
                       user_id=user.id, old_hours=r.hours,
                       claimed_hours=round(body.claimed_hours, 2),
                       reason=reason))
    r.confirmed_at = None       # 提了异议就不算确认过，改完再问一次
    notify_managers(db, r.activity_id, f"[时长申诉] {act.title}",
                    f"{user.name}（{user.student_id}）对时长提出异议：\n"
                    f"{r.hours:g}h → 申报 {body.claimed_hours:g}h\n"
                    f"理由：{reason}\n"
                    f"有申诉在处理中，时长不会自动锁定——"
                    f"请到活动的「时长管理」页处理。", duty="hours", tab="hours")
    db.commit()
    return {"ok": True}


class ProfileBody(BaseModel):
    phone: str
    wechat: str
    campus: str = ""
    id_card: str = ""
    no_mainland_id: bool = False


@router.put("/profile")
def update_profile(body: ProfileBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(current_user)):
    """完善/修改个人资料：手机号、微信号、校区、身份证号。

    这四项学校接口里都没有（校区是 2026-09-22 与学校确认过的），只能本人填。
    港澳台学生与留学生可勾选「无内地身份证号」，这一项留空即可。
    """
    phone = (body.phone or "").strip()
    wechat = (body.wechat or "").strip()
    campus = (body.campus or "").strip()
    id_card = (body.id_card or "").strip().upper()

    if not (phone and wechat):
        raise HTTPException(400, "手机号与微信号均为必填项")
    if campus not in config.CAMPUSES:
        # 校区同时是招募范围的筛选项：写个不在册的值进去，人会静默匹配不上
        # 任何「限校区」的活动，且事后查不出原因（同 integrations/sso.py 的学段）
        raise HTTPException(400, "请选择所在校区")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise HTTPException(400, "手机号格式不正确")
    # 微信号不卡格式——有人填昵称，有人填「同手机号」，卡死只会误伤。
    # 只挡住会被 Excel 当公式的开头：导出表那边也转义，这里是第二道。
    if len(wechat) > 64 or wechat[0] in "=+-@\t\r":
        raise HTTPException(400, "微信号格式不正确")

    if body.no_mainland_id:
        id_card = ""
    elif not id_card:
        # 留空 = 不修改已填的号：修改资料时前端只显示脱敏值，不会把原号回填
        if not user.id_card:
            raise HTTPException(400, "请填写身份证号；港澳台学生与留学生"
                                     "可勾选「无内地身份证号」")
        id_card = user.id_card
    elif not valid_id_card(id_card):
        raise HTTPException(400, "身份证号不合法，请核对后重填")

    user.phone = phone
    user.wechat = wechat
    user.campus = campus
    user.id_card = id_card
    user.no_mainland_id = body.no_mainland_id
    db.commit()
    from ..liaison import session_user
    return {"ok": True, "user": session_user(db, user)}
