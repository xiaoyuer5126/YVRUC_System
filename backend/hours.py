"""志愿时长的结账：核准 → 本人确认或申诉 → 报校青协审批（一并填志愿北京活动
名称）→ 锁定 → 对外联络部在志愿北京上核对名称并确认 → 活动完成。

一场活动的账一起结，状态记在活动上（hours_approved_at / appeal_deadline /
hours_locked_at）；每个人的表态记在自己那条 HoursRecord 上（confirmed_at /
各段 HoursSegment 的 reward）。这里放接口与定时任务都要用的那几步，免得两边
各写一份。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from .audit import log as audit_log
from .integrations import msgcenter
from .models import (Activity, Enrollment, HoursAppeal, HoursRecord,
                     HoursRequest, Position, User, now, uid)


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


def revoke_approval(db: OrmSession, act: Activity) -> bool:
    """核准后账变了：撤销活动的核准与截止时间，要求管理者重新核准。返回是否撤销了。

    自动报批只看活动级截止时间；不撤销就可能把新增或改过的时长按旧截止时间
    直接送审，志愿者没有完整的确认/申诉机会。

    只撤销活动这一级。没改到的人，上次通知过的数值（approved_hours）和本人
    的确认都留着：重新核准时只通知新增的、时长变了的人（见
    staff_ops.hours_approve），改一个人不该惊动整份名单。这一轮提过申诉的人
    例外，清掉他的 approved_hours，重新核准时照样通知到他。
    """
    if act.hours_approved_at is None:
        return False
    appealed = {a.record_id for a in db.query(HoursAppeal).filter(
        HoursAppeal.activity_id == act.id,
        HoursAppeal.created_at >= act.hours_approved_at)}
    act.hours_approved_at = None
    act.appeal_deadline = None
    for rec in (db.query(HoursRecord)
                .filter(HoursRecord.activity_id == act.id,
                        HoursRecord.id.in_(appealed or [""])).all()):
        rec.confirmed_at = None
        rec.approved_hours = None
    return True


def reopen_record(db: OrmSession, act: Activity, rec: HoursRecord | None) -> bool:
    """管理者改了这一笔的数（手动加扣、删段、核定）：本人得对着新数重新表态。

    确认作废、劳务选择清掉（本人是对着旧数选的；扣减只记在志愿时长上，留着
    劳务会把志愿那一半扣成负数），重新核准时一定通知到他（approved_hours 清
    空）。核准过的活动撤销核准。rec 为 None 是这一笔整个删掉了，只撤销核准。
    返回是否撤销了核准。
    """
    if rec is not None:
        rec.confirmed_at = None
        rec.approved_hours = None
        rec.clear_pay()
    return revoke_approval(db, act)


def lock_records(db: OrmSession, activity_id: str) -> list[HoursRecord]:
    """锁住这场活动（活动行）与它的时长记录，返回这些记录，按 id 顺序取锁。

    先锁活动行：两个请求同时给同一个人记第一笔时，记录行还不存在、锁不住，
    两边都会去建，撞上「一人一场一笔」的唯一约束；活动行锁让它们排队，后来的
    那个看得到先来的那一笔。改了数要撤销核准时，改的也是活动行。

    要动段的路径先过这一步：段是行，「读出现有的几段 → 加减 → 写回」中间被别人
    插一脚，两条路径各写各的段，拼出来的是谁都没写过的组合（手动加减、本人选
    报酬方式、名单导入、锁定时清掉未表态的报酬，都是这种）。**锁在读之前**，
    整段「读 → 改 → 写回」才排他；锁到手也顺带取库里的最新值
    （populate_existing），不用会话里的旧快照。按 id 排序是为了让并发进来的
    两个批量操作取锁顺序一致。
    """
    (db.query(Activity).filter(Activity.id == activity_id)
     .populate_existing().with_for_update().one_or_none())
    return (db.query(HoursRecord).filter(HoursRecord.activity_id == activity_id)
            .populate_existing().with_for_update().order_by(HoursRecord.id).all())


def pay_positions(db: OrmSession, segments: list | None) -> dict[str, Position]:
    """这些段里开了「可选劳务」的岗位，按岗位 id。

    按岗位现在的设置判断：管理者在核准后才给岗位开劳务，本人照样能选。"""
    ids = {s.position_id for s in (segments or [])
           if s.kind == "shift" and s.position_id}
    if not ids:
        return {}
    return {p.id: p for p in db.query(Position)
            .filter(Position.id.in_(ids), Position.pay_optional.is_(True))}


def parts_view(db: OrmSession, rec: HoursRecord) -> list[dict]:
    """给本人看的明细：每段带上能不能选劳务（pay_ok）、劳务说明和眼下记的
    报酬方式（reward，同 HoursRecord.split 的口径，跟合计对得上）。"""
    segs = rec.live_segments()
    pay = pay_positions(db, segs)
    out = []
    for s in segs:
        pos = pay.get(s.position_id) if s.kind == "shift" else None
        out.append({**s.out(), "pay_ok": pos is not None,
                    "pay_note": pos.pay_note if pos else "",
                    "reward": "pay" if s.is_pay() else "hours"})
    return out


def result_text(rec: HoursRecord) -> str:
    """通知里的一句话：记几小时，有没有按劳务计的部分。

    报的是结算口径的那个数（志愿时长那一半），跟本人页面上看到的一致——
    通知里写原始合计，本人对着页面上的数会以为算错了。"""
    counted, pay = rec.split()
    if pay <= 0:
        return f"本次记 {counted:g} 小时"
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
    # 没表态的那些记录要把各段的报酬选择清掉（clear_pay 逐段改写）：先锁住这场
    # 活动的记录行再读改写。名单导入/编辑是先拿活动行锁再拿记录行锁，但那条路
    # 要求「没有待审批的锁定申请」，而走到这里时申请还挂着（同一事务里才改成
    # 已通过）：两边不会成环
    rows = lock_records(db, act.id)
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
