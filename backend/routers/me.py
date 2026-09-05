"""学生端「我的」：报名记录、消息通知、志愿时长。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from .. import hours as hours_flow
from ..database import db_session
from ..deps import current_user
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


def _enrollment_row(db: OrmSession, enr: Enrollment) -> dict:
    act = db.get(Activity, enr.activity_id)
    g = db.get(Shift, enr.shift_id)
    st = db.get(Position, enr.position_id) if enr.position_id else None
    rec = db.query(CheckinRecord).filter(
        CheckinRecord.user_id == enr.user_id,
        CheckinRecord.shift_id == enr.shift_id).first()
    return {
        "id": enr.id,
        "activity": {"id": act.id, "title": act.title,
                     "recruit_mode": act.recruit_mode,
                     "status": act.status, "location": act.location},
        "shift": {"id": g.id, "name": g.name, "time_text": g.span_text(),
                  "start_time": iso_of(g.start_time),
                  "end_time": iso_of(g.end_time),
                  "location": g.location,
                  # 培训/彩排是主场次带出来的，学生不能单独退，只能整体退
                  "kind": g.kind, "kind_label": g.kind_label(),
                  "attached": g.is_attached(),
                  "parent_name": (db.get(Shift, g.parent_id).name
                                  if g.is_attached() else ""),
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
        "checkout_at": iso(rec.checkout_at) if rec else None,
        # 联络群二维码只给已报上的人；没弹过的由前端弹一次并回写 seen。
        # 培训/彩排是随主场次带上的，不单独弹
        "contact_group_qr": (act.contact_group_qr
                             if enr.status == "enrolled" else ""),
        "group_qr_pending": (enr.status == "enrolled" and not enr.group_qr_seen
                             and not g.is_attached()),
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


@router.post("/enrollments/{enrollment_id}/group-qr-seen")
def group_qr_seen(enrollment_id: str, db: OrmSession = Depends(db_session),
                  user=Depends(current_user)):
    """联络群二维码弹过一次了，之后只在「我的报名」里按需看。

    一个活动一个群，按活动标记：同一活动下的其他场次不再弹。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    (db.query(Enrollment)
     .filter(Enrollment.activity_id == enr.activity_id,
             Enrollment.user_id == user.id)
     .update({"group_qr_seen": True}, synchronize_session=False))
    db.commit()
    return {"ok": True}


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
def my_notifications(db: OrmSession = Depends(db_session),
                     user=Depends(current_user)):
    rows = (db.query(Notification).filter(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc()).limit(50).all())
    unread = (db.query(func.count(Notification.id))
              .filter(Notification.user_id == user.id,
                      Notification.read.is_(False)).scalar() or 0)
    pending_ack = (db.query(func.count(Notification.id))
                   .filter(Notification.user_id == user.id,
                           Notification.need_ack.is_(True),
                           Notification.acked_at.is_(None)).scalar() or 0)
    return {"unread": unread, "pending_ack": pending_ack,
            "items": [n.out() for n in rows]}


@router.post("/notifications/read-all")
def read_all(db: OrmSession = Depends(db_session), user=Depends(current_user)):
    """全部标为已读。待签收的通知只标已读，签收仍需本人逐条点。"""
    (db.query(Notification)
     .filter(Notification.user_id == user.id, Notification.read.is_(False))
     .update({"read": True}, synchronize_session=False))
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
    # 选了劳务的那次不计入时长：账上只有志愿时长
    total = sum(r.hours for r in rows if r.counts_hours())
    acts = {a.id: a for a in db.query(Activity)
            .filter(Activity.id.in_([r.activity_id for r in rows] or [""])).all()}
    locked = sum(r.hours for r in rows
                 if r.counts_hours() and acts.get(r.activity_id)
                 and acts[r.activity_id].hours_locked())
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
                    "hours": r.hours,
                    "reward": r.reward, "reward_label": r.reward_label(),
                    "confirmed_at": iso_of(r.confirmed_at),
                    "approved": bool(act and act.hours_approved_at),
                    "locked": bool(act and act.hours_locked()),
                    "appeal_deadline": iso_of(act.appeal_deadline) if act else None,
                    "created_at": iso_of(r.created_at),
                    # 有申诉在处理中就先别确认；处理完再核准一次会重新问
                    "can_confirm": open_window and (
                        a is None or a.status != "pending"),
                    "can_appeal": open_window and (
                        a is None or a.status == "rejected"),
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
        raise HTTPException(409, "这条时长还没核准，等负责老师核准后再看")
    if act.hours_locked():
        raise HTTPException(409, "该活动的时长已锁定")
    if act.appeal_deadline and now() > act.appeal_deadline:
        raise HTTPException(409, f"确认已于 {act.appeal_deadline:%m-%d %H:%M} 截止，"
                                 f"已按志愿时长记")
    if (db.query(HoursAppeal)
            .filter(HoursAppeal.record_id == r.id,
                    HoursAppeal.status == "pending").first()):
        raise HTTPException(409, "您的申诉还在处理中，处理完负责老师会再核准一次")
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
        raise HTTPException(409, "这条时长还没核准，等负责老师核准后再看")
    if act.hours_locked():
        raise HTTPException(409, "该活动的时长已锁定，不可再申诉；请联系负责老师")
    if act.appeal_deadline and now() > act.appeal_deadline:
        raise HTTPException(
            409, f"申诉已于 {act.appeal_deadline:%m-%d %H:%M} 截止；"
                 f"如确有问题请联系负责老师")
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(400, "请把情况写清楚（至少 5 个字），管理者才好判断")
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
                    f"请到活动的「导出与时长」页处理。", duty="hours")
    db.commit()
    return {"ok": True}


class ProfileBody(BaseModel):
    phone: str
    wechat: str
    id_card: str = ""
    no_mainland_id: bool = False


@router.put("/profile")
def update_profile(body: ProfileBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(current_user)):
    """完善/修改个人联系方式：手机号、微信号、身份证号。

    数智人大拿不到这三项，志愿者需自行补全后才能报名。
    港澳台学生与留学生可勾选「无内地身份证号」，这一项留空即可。
    """
    phone = (body.phone or "").strip()
    wechat = (body.wechat or "").strip()
    id_card = (body.id_card or "").strip().upper()

    if not (phone and wechat):
        raise HTTPException(400, "手机号与微信号均为必填项")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise HTTPException(400, "手机号格式不正确")

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
    user.id_card = id_card
    user.no_mainland_id = body.no_mainland_id
    db.commit()
    return {"ok": True, "user": user.full()}
