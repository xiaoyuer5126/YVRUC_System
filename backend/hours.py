"""志愿时长的结账：核准 → 本人确认或申诉 → 报校青协审批（一并填志愿北京活动
名称）→ 锁定 → 对外联络部在志愿北京上核对名称并确认 → 活动完成。

一场活动的账一起结，状态记在活动上（hours_approved_at / appeal_deadline /
hours_locked_at）；每个人的表态记在自己那条 HoursRecord 上（confirmed_at /
reward）。这里放接口与定时任务都要用的那几步，免得两边各写一份。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from .audit import log as audit_log
from .integrations import msgcenter
from .models import (Activity, Enrollment, HoursRecord, HoursRequest, Position,
                     User, now, uid)


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


def pay_position(db: OrmSession, activity_id: str, user_id: str) -> Position | None:
    """这个人在本活动里正式报名的岗位中，第一个标了「可选劳务」的。
    没有就是只能记志愿时长。"""
    rows = (db.query(Position)
            .join(Enrollment, Enrollment.position_id == Position.id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.user_id == user_id,
                    Enrollment.status == "enrolled",
                    Position.pay_optional.is_(True))
            .order_by(Position.sort).all())
    return rows[0] if rows else None


def result_text(rec: HoursRecord) -> str:
    """通知里的一句话：记几小时，还是按劳务计。"""
    if rec.counts_hours():
        return f"本次记 {rec.hours:g} 小时"
    return "本次按劳务计，不记入志愿时长"


def lock_hours(db: OrmSession, act: Activity, actor: User | None,
               reason: str, *, to_liaison: bool = True) -> int:
    """锁定这场活动的时长，逐个通知本人。不提交事务。

    没表态的人按「没问题 + 志愿时长」处理，锁定时补上确认时间；
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
            r.reward = "hours"
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
