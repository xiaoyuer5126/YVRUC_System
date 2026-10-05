"""志愿时长的结账：核准 → 本人确认或申诉 → 报校青协审批（一并填志愿北京活动
名称）→ 锁定 → 对外联络部在志愿北京上核对名称并确认 → 活动完成。

一场活动的账一起结，状态记在活动上（hours_approved_at / appeal_deadline /
hours_locked_at）；每个人的表态记在自己那条 HoursRecord 上（confirmed_at /
parts 里每段的 reward）。这里放接口与定时任务都要用的那几步，免得两边各写一份。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from .audit import log as audit_log
from .integrations import msgcenter
from .models import (Activity, Enrollment, HoursRecord, HoursRequest, Position,
                     User, now, uid)


# 以党校同学身份补招进来的报名（Enrollment.source）。党校同学的志愿时长另有
# 用途，不能同时拿去做学分认定：不进时长导出表（那张表是给志愿北京录时长的）、
# 不计入本人的累计时长；数据总览照算，那是平台自己的数。
PARTY_SCHOOL = "party_school"


def party_school_users(db: OrmSession, activity_id: str) -> set[str]:
    """这场活动里以党校同学身份补招进来的人。"""
    return {r[0] for r in db.query(Enrollment.user_id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.source == PARTY_SCHOOL).distinct()}


def party_school_activities(db: OrmSession, user_id: str) -> set[str]:
    """这个人以党校同学身份参加的那些活动。"""
    return {r[0] for r in db.query(Enrollment.activity_id)
            .filter(Enrollment.user_id == user_id,
                    Enrollment.source == PARTY_SCHOOL).distinct()}


def pending_lock(db: OrmSession, activity_id: str) -> HoursRequest | None:
    """这场活动是不是有一条锁定申请正等着部长处理。"""
    return (db.query(HoursRequest)
            .filter(HoursRequest.activity_id == activity_id,
                    HoursRequest.kind == "lock",
                    HoursRequest.status == "pending").first())


def submit_lock(db: OrmSession, act: Activity, actor: User | None,
                reason: str) -> HoursRequest | None:
    """提交锁定审批。已经挂着一条就不再提，返回 None。不提交事务。

    时长要由校青协过目才算数：申诉期满系统自动提交，管理者也可以手动提前提交。
    等的这段时间里时长冻结，改不了——否则部长看的和最终封的不是同一份账。
    """
    if pending_lock(db, act.id) is not None:
        return None
    req = HoursRequest(id=uid("hrq"), activity_id=act.id, kind="lock",
                       reason=reason,
                       requested_by=actor.id if actor else "system")
    db.add(req)
    audit_log(db, actor=actor, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"提交时长锁定审批（{reason}）")
    return req


def default_appeal_deadline() -> datetime:
    """默认申诉截止：第二天中午。大家一般当天就自己看了，留一夜足够。"""
    t = now() + timedelta(days=1)
    return t.replace(hour=12, minute=0, second=0, microsecond=0)


def pay_positions(db: OrmSession, parts: list | None) -> dict[str, Position]:
    """这些段落里开了「可选劳务」的岗位，按岗位 id。

    按岗位现在的设置判断：管理者在核准后才给岗位开劳务，本人照样能选。"""
    ids = {p.get("position_id") for p in (parts or [])
           if p.get("kind") == "shift" and p.get("position_id")}
    if not ids:
        return {}
    return {p.id: p for p in db.query(Position)
            .filter(Position.id.in_(ids), Position.pay_optional.is_(True))}


def parts_view(db: OrmSession, rec: HoursRecord) -> list[dict]:
    """给本人看的明细：每段带上能不能选劳务（pay_ok）、劳务说明和眼下记的
    报酬方式（reward，同 HoursRecord.split 的口径，跟合计对得上）。"""
    pay = pay_positions(db, rec.parts)
    out = []
    for p in rec.parts or []:
        pos = pay.get(p.get("position_id")) if p.get("kind") == "shift" else None
        out.append({**p, "pay_ok": pos is not None,
                    "pay_note": pos.pay_note if pos else "",
                    "reward": "pay" if rec.part_is_pay(p) else "hours"})
    return out


def result_text(rec: HoursRecord) -> str:
    """通知里的一句话：记几小时，有没有按劳务计的部分。"""
    counted, pay = rec.split(rec.hours, rec.parts)
    if pay <= 0:
        return f"本次记 {rec.hours:g} 小时"
    if counted <= 0:
        return "本次按劳务计，不记入志愿时长"
    return f"本次记 {counted:g} 小时志愿时长，另有 {pay:g} 小时按劳务计"


def lock_hours(db: OrmSession, act: Activity, actor: User | None,
               reason: str, *, to_liaison: bool = True) -> int:
    """锁定这场活动的时长，逐个通知本人。不提交事务。

    没表态的人按「没问题 + 各段都记志愿时长」处理，锁定时补上确认时间；
    这样锁定之后每条记录都有本人（或视同本人）的确认。
    锁定还不是终点：活动落「待外联确认」，管理者提交时长审核时填的志愿北京
    活动名称推给对外联络部，外联在志愿北京核对并确认后才算「已完成」
    （见 routers/liaison.decide_vb_confirm）。内部、外部活动一样走这一步。
    to_liaison=False：一条时长都没有，志愿北京那边没什么可录的，直接完成。
    """
    from . import liaison

    t = now()
    rows = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).all()
    for r in rows:
        if r.confirmed_at is None:
            r.confirmed_at = t
            r.clear_pay()
    act.hours_locked_at = t
    act.status = "vb_pending" if to_liaison else "completed"
    audit_log(db, actor=actor, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"锁定志愿时长，志愿北京活动名称"
                       f"{liaison.names_text(act.vb_names)}推送对外联络部确认（{reason}）"
                       if to_liaison else f"锁定志愿时长，活动已完成（{reason}）"))
    if to_liaison:
        liaison.push_vb_confirm(db, act, actor, "lock")
    for r in rows:
        u = db.get(User, r.user_id)
        if u is not None:
            msgcenter.send_user(db, u, "hours_locked", data={"activity_id": act.id},
                            activity=act.title, result=result_text(r))
    return len(rows)
