"""学生端「我的」：报名记录、消息通知、信用分、志愿时长。"""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import current_user
from ..models import (Activity, CheckinRecord, CreditLog, Enrollment,
                      HoursRecord, Notification, Shift, Position, User,
                      iso, now)
from ..serializers import iso_of

router = APIRouter(prefix="/api/me", tags=["me"])

ID_CARD_WEIGHTS = (7, 9, 10, 5, 8, 4, 2, 1, 6, 3, 7, 9, 10, 5, 8, 4, 2)
ID_CARD_CHECK = "10X98765432"


def valid_id_card(value: str) -> bool:
    """18 位居民身份证号校验（含末位校验码）。

    在校学生均为 18 位号，不放行 15 位旧号——填错一位就是一条对不上人的记录，
    而这个号会跟着时长回传到数智人大。
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
        "activity": {"id": act.id, "title": act.title, "type": act.type,
                     "status": act.status, "location": act.location},
        "shift": {"id": g.id, "name": g.name, "time_text": g.span_text(),
                  "start_time": iso_of(g.start_time),
                  "end_time": iso_of(g.end_time)},
        "position": ({"id": st.id, "name": st.name, "time_text": st.time_text(),
                     "location": st.location} if st else None),
        "status": enr.status, "cancel_type": enr.cancel_type,
        "seat_held": enr.seat_held,
        "allow_reassign": enr.allow_reassign,
        "joined_at": iso_of(enr.joined_at),
        "confirm_deadline": iso_of(enr.confirm_deadline),
        "checked_in": rec is not None,
        "checkout_at": iso(rec.checkout_at) if rec else None,
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
        if r.status != "cancelled":
            seen.add(r.shift_id)
        out.append(_enrollment_row(db, r))
    return out


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


@router.get("/credit")
def my_credit(db: OrmSession = Depends(db_session), user=Depends(current_user)):
    logs = (db.query(CreditLog).filter(CreditLog.user_id == user.id)
            .order_by(CreditLog.created_at.desc()).limit(50).all())
    frozen = bool(user.credit_frozen_until and user.credit_frozen_until > now())
    return {
        "score": user.credit_score,
        "frozen": frozen,
        "frozen_until": iso_of(user.credit_frozen_until),
        "abandon_count": user.abandon_count,
        "waitlist_banned_until": iso_of(user.waitlist_banned_until),
        "restricted_types": user.restricted_types or [],
        "restricted_until": iso_of(user.restricted_until),
        "logs": [{
            "delta": l.delta, "after": l.after_score, "reason": l.reason,
            "created_at": iso_of(l.created_at)} for l in logs],
    }


@router.get("/hours")
def my_hours(db: OrmSession = Depends(db_session), user=Depends(current_user)):
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.user_id == user.id)
            .order_by(HoursRecord.created_at.desc()).all())
    total = sum(r.hours for r in rows)
    uploaded = sum(r.hours for r in rows if r.status == "uploaded")
    out = []
    for r in rows:
        act = db.get(Activity, r.activity_id)
        out.append({"id": r.id, "activity_title": act.title if act else "",
                    "hours": r.hours, "status": r.status,
                    "upload_ref": r.upload_ref,
                    "created_at": iso_of(r.created_at)})
    return {"total": round(total, 1), "uploaded": round(uploaded, 1),
            "records": out}


class ProfileBody(BaseModel):
    phone: str
    wechat: str
    id_card: str = ""
    no_mainland_id: bool = False
    id_doc_type: str = ""      # 港澳台居民居住证 / 护照 / 台胞证 …


@router.put("/profile")
def update_profile(body: ProfileBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(current_user)):
    """完善/修改个人联系方式：手机号、微信号、身份证号。

    数智人大拿不到这三项，志愿者需自行补全后才能报名。
    港澳台学生与留学生可勾选「无内地身份证号」免填，改为说明所持证件类型。
    """
    phone = (body.phone or "").strip()
    wechat = (body.wechat or "").strip()
    id_card = (body.id_card or "").strip().upper()
    doc_type = (body.id_doc_type or "").strip()

    if not (phone and wechat):
        raise HTTPException(400, "手机号与微信号均为必填项")
    if not re.fullmatch(r"1[3-9]\d{9}", phone):
        raise HTTPException(400, "手机号格式不正确")

    if body.no_mainland_id:
        if not doc_type:
            raise HTTPException(400, "请选择您所持证件的类型")
        if doc_type not in config.ID_DOC_TYPES:
            raise HTTPException(400, "证件类型不在可选范围内")
        id_card = ""
    else:
        if not id_card:
            raise HTTPException(400, "请填写身份证号；港澳台学生与留学生"
                                     "可勾选「无内地身份证号」")
        if not valid_id_card(id_card):
            raise HTTPException(400, "身份证号不合法，请核对后重填")
        doc_type = ""

    user.phone = phone
    user.wechat = wechat
    user.id_card = id_card
    user.no_mainland_id = body.no_mainland_id
    user.id_doc_type = doc_type
    db.commit()
    return {"ok": True, "user": user.full()}
