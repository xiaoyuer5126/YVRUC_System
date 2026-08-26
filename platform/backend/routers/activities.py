"""学生端：活动浏览与报名（冲突检测 / 候补 / 退选 / 转正确认）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from .. import config, credit, enroll_logic as logic
from ..database import db_session
from ..deps import current_user
from ..integrations import wecom
from ..locks import group_lock
from ..models import Activity, Enrollment, PositionGroup, Station, User, now, uid
from ..perm import is_activity_manager
from ..serializers import activity_brief, activity_detail

router = APIRouter(prefix="/api/activities", tags=["activities"])

# 学生可见的活动状态：筹备中的活动尚未定稿，只有管理者看得到
PUBLIC_STATUSES = ("published", "closed", "finished")


def _get_activity(db: OrmSession, activity_id: str, user: User) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status not in PUBLIC_STATUSES and not is_activity_manager(db, user, act.id):
        raise HTTPException(404, "活动不存在")
    return act


def _get_group(db: OrmSession, group_id: str) -> PositionGroup:
    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    return g


@router.get("")
def list_activities(scope: str = "open",
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(current_user)):
    """scope: open 报名中 / all 全部已公开的（含已截止、已结束，供回看）。"""
    q = db.query(Activity).filter(Activity.status.in_(PUBLIC_STATUSES))
    if scope == "open":
        q = q.filter(Activity.status == "published")
    acts = q.order_by(Activity.created_at.desc()).all()
    return [activity_brief(db, a) for a in acts]


@router.get("/{activity_id}")
def get_activity(activity_id: str,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    return activity_detail(db, act, user)


class EnrollBody(BaseModel):
    station_id: str | None = None     # 直接定二级模式必填，其余留空
    allow_reassign: bool = True       # 是否接受管理者调剂工位（仅作参考）


def _check_eligibility(db: OrmSession, user: User, act: Activity,
                       g: PositionGroup) -> None:
    """报名资格：活动状态、报名窗、用户类型、资料、信用、限制准入。

    与名额无关的校验全在这里，锁外先跑一遍，把明确报不了的人挡在锁外面。
    """
    if act.status != "published":
        raise HTTPException(409, f"活动当前为「{act.label_status()}」，不在报名期")

    t = now()
    if act.reg_start and t < act.reg_start:
        raise HTTPException(409, f"报名尚未开始（{act.reg_start:%m-%d %H:%M} 起）")
    reg_end = act.reg_end or g.start_time
    if reg_end and t > reg_end:
        raise HTTPException(409, "报名已截止")

    # 老师类型不参与报名（无信用分/时长等个人数据）
    if user.user_type == "teacher":
        raise HTTPException(403, "老师类型用户不参与志愿报名")

    # 学生必须先完善个人信息（数智人大拿不到手机号/微信号/身份证号）
    if not user.profile_complete():
        raise HTTPException(403, "请先完善个人信息（手机号/微信号/身份证号）后再报名")

    if user.credit_frozen_until and user.credit_frozen_until > t:
        raise HTTPException(
            403, f"信用分低于{config.CREDIT_FREEZE_BELOW}分，报名权限冻结至 "
                 f"{user.credit_frozen_until:%Y-%m-%d}")

    # 限制准入（《志愿者服务规范》）：按活动**发生时间**判定——
    # 「招募在限制期内、活动已出限制期」的允许报名。
    if (user.restricted_types and act.type in user.restricted_types
            and user.restricted_until and g.start_time <= user.restricted_until):
        types = "、".join(user.restricted_types)
        raise HTTPException(
            403, f"您处于限制准入名单（不得参与{types}类活动，"
                 f"至 {user.restricted_until:%Y-%m-%d}），无法报名本活动")


@router.post("/{activity_id}/groups/{group_id}/enroll")
def enroll(activity_id: str, group_id: str, body: EnrollBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    g = _get_group(db, group_id)
    if g.activity_id != act.id:
        raise HTTPException(400, "岗位组不属于该活动")

    _check_eligibility(db, user, act, g)

    # 工位有效性（与名额无关，锁外先判）
    station = None
    if body.station_id:
        station = db.get(Station, body.station_id)
        if station is None or station.group_id != g.id:
            raise HTTPException(400, "二级工位无效")
        if g.needs_assign():
            raise HTTPException(400, "该岗位组为「二级待定」模式，工位由调配期统一分配")
    elif not g.needs_assign() and g.stations:
        raise HTTPException(400, "请选择具体工位")

    # ★ 时段冲突（粗粒度）：与所有有效报名的一级岗位比区间，重叠即拦截。
    # 候补中的场次同样算已占用——排了队就等于预留了这段时间。
    clashes = logic.time_conflicts(db, user, g)
    if clashes:
        raise HTTPException(409, {
            "message": f"时间段与您已报名的活动冲突：{logic.conflict_message(clashes)}",
            "conflicts": clashes})

    # ★ 名额判断与落库整段串行化到场次粒度，防止超卖与重复报名
    with group_lock(g.id):
        live = (db.query(Enrollment)
                .filter(Enrollment.group_id == g.id,
                        Enrollment.user_id == user.id)
                .filter((Enrollment.status.in_(logic.ACTIVE))
                        | (Enrollment.seat_held.is_(True))).first())
        if live is not None:
            if live.seat_held:
                raise HTTPException(
                    409, "您此前在该场次临近退选，名额已按规则扣留，无法再次报名；"
                         "如确有需要请联系负责老师")
            label = {"confirmed": "已报名为正式成员", "waitlisted": "已在候补队列中",
                     "pending_confirm": "有候补转正待确认"}[live.status]
            raise HTTPException(409, f"该场次{label}，请勿重复报名")

        enr = Enrollment(id=uid("enr"), activity_id=act.id, group_id=g.id,
                         user_id=user.id, joined_at=now(),
                         allow_reassign=body.allow_reassign)
        if logic.has_room(db, g, station):
            enr.status = "confirmed"
            if station is not None:
                enr.station_id = station.id
                enr.assigned_at = enr.joined_at
        else:
            # 名额满了才轮到候补，此时才检查候补资格
            ok, why = credit.can_join_waitlist(user)
            if not ok:
                raise HTTPException(403, why)
            if len(logic.waitlist_rows(db, g.id)) >= g.waitlist_quota:
                raise HTTPException(409, f"候补名额已满（上限{g.waitlist_quota}人）")
            enr.status = "waitlisted"
        db.add(enr)

        if enr.status == "confirmed":
            wecom.send_user(db, user, "signup_success", activity=act.title,
                            group=g.name, time=g.span_text(),
                            detail=(f"工位：{station.name}（{station.location}）"
                                    if station else "具体工位将于调配期公布"))
        else:
            db.flush()
            wecom.send_user(db, user, "waitlist_queued", activity=act.title,
                            group=g.name, pos=logic.waitlist_position(db, enr))
        try:
            db.commit()
        except IntegrityError:      # 唯一索引兜底：锁失效时的并发重复报名
            db.rollback()
            raise HTTPException(409, "该场次您已有报名记录，请勿重复报名")

    return {
        "status": enr.status,
        "enrollment_id": enr.id,
        "waitlist_position": (logic.waitlist_position(db, enr)
                              if enr.status == "waitlisted" else 0),
        "message": ("报名成功！名额已锁定。" if enr.status == "confirmed"
                    else "名额已满，已为您加入候补队列。"),
    }


class ReassignPrefBody(BaseModel):
    allow_reassign: bool


@router.patch("/enrollments/{enrollment_id}/reassign-pref")
def set_reassign_pref(enrollment_id: str, body: ReassignPrefBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    """志愿者改自己的调剂意愿。只是给管理者的参考，随时可改。"""
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if enr.status not in logic.ACTIVE:
        raise HTTPException(409, "该报名记录已失效")
    enr.allow_reassign = body.allow_reassign
    db.commit()
    return {"ok": True, "allow_reassign": enr.allow_reassign}


@router.delete("/enrollments/{enrollment_id}")
def cancel_enroll(enrollment_id: str,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(current_user)):
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if enr.status == "cancelled":
        raise HTTPException(400, "该记录已退选")
    g = db.get(PositionGroup, enr.group_id)
    act = db.get(Activity, enr.activity_id)

    with group_lock(g.id):
        if enr.status == "pending_confirm":
            # 放弃候补转正：顺延下一位并计数
            credit.register_abandon(db, user)
            enr.status = "cancelled"
            enr.cancel_type = "abandoned"
            enr.confirm_deadline = None
            db.commit()
            logic.promote_head(db, g)
            db.commit()
            return {"ok": True, "effect": "abandoned",
                    "message": "已放弃候补转正，名额顺延下一位。"
                               f"连续{config.MAX_ABANDON_TIMES}次放弃将暂停候补资格。"}

        if enr.status == "waitlisted":
            enr.status = "cancelled"
            enr.cancel_type = "normal"
            db.commit()
            return {"ok": True, "effect": "quit_queue", "message": "已退出候补队列。"}

        # confirmed：活动开始后不可退
        t = now()
        if t >= g.start_time:
            raise HTTPException(409, "活动已开始，无法退选；如有特殊情况请联系负责老师。")

        if (g.start_time - t).total_seconds() / 3600 >= config.LATE_CANCEL_HOURS:
            enr.status = "cancelled"
            enr.cancel_type = "normal"
            db.commit()
            logic.promote_head(db, g)   # 正常退选 → 名额释放给候补
            db.commit()
            return {"ok": True, "effect": "released",
                    "message": "退选成功，名额已释放给候补队列。"}

        # 临近退选：扣分，且名额不再释放——记录保留占位（《设计方案》阶段二.3）
        enr.status = "cancelled"
        enr.cancel_type = "late"
        enr.seat_held = True
        score = credit.apply_delta(db, user, -config.LATE_CANCEL_PENALTY,
                                   f"临近退选：距「{act.title}·{g.name}」开始不足"
                                   f"{config.LATE_CANCEL_HOURS}小时",
                                   activity_id=act.id)
        db.commit()
    return {"ok": True, "effect": "penalized",
            "credit_score": score,
            "message": f"距开始不足{config.LATE_CANCEL_HOURS}小时退选，"
                       f"扣除信用分{config.LATE_CANCEL_PENALTY}分"
                       f"（当前{score}分）；该名额已按规则扣留，不再释放。"}


@router.post("/enrollments/{enrollment_id}/confirm-promotion")
def confirm_promotion(enrollment_id: str,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    with group_lock(enr.group_id):
        db.refresh(enr)
        if enr.status != "pending_confirm":
            raise HTTPException(400, "该记录不在待确认状态")
        if enr.confirm_deadline and now() > enr.confirm_deadline:
            raise HTTPException(409, "确认窗口已超时，名额已顺延")
        logic.confirm_promotion(db, enr, user)
        db.commit()
    st_name = ""
    if enr.station_id:
        st = db.get(Station, enr.station_id)
        st_name = f"工位：{st.name}（{st.location}）"
    return {"ok": True, "station_id": enr.station_id,
            "message": f"确认成功，已恢复正式报名。{st_name}"}
