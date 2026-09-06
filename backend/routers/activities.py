"""学生端：活动浏览与报名（冲突检测 / 候补 / 退选 / 转正确认）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from .. import config, enroll_logic as logic
from ..database import db_session
from ..deps import current_user
from .. import notify
from ..integrations import wecom
from ..locks import shift_lock
from ..models import (Activity, Application, Enrollment, Shift, Position,
                      User, now, uid)
from ..perm import is_activity_manager, managed_activity_ids
from ..qrcodes import page_qr
from ..serializers import activity_brief, activity_detail, recruit_scope_text
from .approvals import can_review

router = APIRouter(prefix="/api/activities", tags=["activities"])

# 学生可见的活动状态：筹备中的活动尚未定稿，只有管理者看得到
PUBLIC_STATUSES = ("published", "closed", "finished", "completed")


def _ensure_not_editing(act: Activity) -> None:
    """编辑冻结期间不接受报名与退选。

    学生不该报进一个正在被改的岗位表——名额、条件、组长都可能下一秒就变；
    也不该在管理者调整到一半时退出，让对方看到一份自己动着动着就变了的名单。
    """
    if act.editing_since is not None:
        raise HTTPException(409, "负责老师正在调整本活动的安排，报名暂停中，"
                                 "请稍后再试")


def _outside_scope(db: OrmSession, user: User, act: Activity) -> bool:
    """招募范围不含这个人，且他在本活动里没有任何报名/申请记录。

    范围缩小时管理者可以选择保留已报名的人，那些人当然还得看得见自己
    报了什么。管理者看自己的活动不受此限，在调用处另判。
    """
    if not logic.unmet_recruit_scope(user, act):
        return False
    involved = (db.query(Enrollment.id)
                .filter(Enrollment.activity_id == act.id,
                        Enrollment.user_id == user.id).first() is not None
                or db.query(Application.id)
                .filter(Application.activity_id == act.id,
                        Application.user_id == user.id).first() is not None)
    return not involved


def _get_activity(db: OrmSession, activity_id: str, user: User) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if is_activity_manager(db, user, act.id):
        return act
    if act.status in PUBLIC_STATUSES:
        # 招募范围外的人打开转发来的链接：不给 404，说清楚为什么看不了
        if _outside_scope(db, user, act):
            raise HTTPException(
                403, f"本活动招募范围为「{recruit_scope_text(act)}」，"
                     f"不含您（{'；'.join(logic.unmet_recruit_scope(user, act))}）")
        return act
    # 还没公开：这条审批线上的人要看得到安排才批得下去
    if can_review(db, user, act):
        return act
    raise HTTPException(404, "活动不存在")


def _get_group(db: OrmSession, shift_id: str) -> Shift:
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
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
    # 招募范围外的活动不上广场：看得见报不了，只会多一次被拦的失望。
    # 自己管的活动照常显示，管理者要从这里进详情核对志愿者看到的样子。
    managed = managed_activity_ids(db, user)
    acts = [a for a in acts
            if managed is None or a.id in managed or not _outside_scope(db, user, a)]
    # 急招的排最前（后置顶的更靠前），其余按创建时间
    acts.sort(key=lambda a: (a.status != "published" or a.pinned_at is None,
                             -(a.pinned_at or a.created_at).timestamp()))
    return [activity_brief(db, a) for a in acts]


@router.get("/{activity_id}")
def get_activity(activity_id: str,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    manager = is_activity_manager(db, user, activity_id)
    # 审批人也按管理者视角渲染：培训内容、学习材料这些是判断活动办得成不成的
    # 依据，看不到就批不下去。只在活动**还没公开**时给——那时一个报名的人都
    # 没有，不存在暴露联系方式的问题；一旦公开，他和其他人看到的就该是同一页。
    review = (not manager and act.status not in PUBLIC_STATUSES
              and can_review(db, user, act))
    # 管理者要能在同一个页面核对培训内容与组长，不必先去后台
    out = activity_detail(db, act, user, manager=manager or review)
    out["review_preview"] = review
    return out


@router.get("/{activity_id}/qr.svg", response_class=Response)
def activity_qr(request: Request, activity_id: str, base: str = "",
                db: OrmSession = Depends(db_session)):
    """活动页二维码，贴海报、发群里都用它。内容就是活动详情页的地址。

    不要登录态：<img> 标签不会带 Authorization 头，加了鉴权图就是裂的。
    码里只有一个本来就要公开发出去的链接，扫码后照常要登录才看得到内容。
    仍然查一下活动在不在，免得打错 id 印出一张扫不出东西的码。
    """
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return page_qr(request, f"/activity/{act.id}", base)


class EnrollBody(BaseModel):
    position_id: str | None = None    # pick 模式必填；assign 模式留空
    allow_reassign: bool = True       # 是否接受管理者调剂岗位（仅作参考）
    leader_willing: bool = False      # 是否愿意担任该岗位组长
    answers: dict = {}                # 活动设了报名表时的答案（题目 id → 答案）


def _check_eligibility(db: OrmSession, user: User, act: Activity,
                       g: Shift) -> None:
    """报名资格：活动状态、报名窗、用户类型、资料、招募范围与场次条件。

    与名额无关的校验全在这里，锁外先跑一遍，把明确报不了的人挡在锁外面。
    """
    if act.status != "published":
        raise HTTPException(409, f"活动当前为「{act.label_status()}」，不在报名期")
    _ensure_not_editing(act)

    t = now()
    if act.reg_start and t < act.reg_start:
        raise HTTPException(409, f"报名尚未开始（{act.reg_start:%m-%d %H:%M} 起）")
    # 截止按场次各算各的：活动的报名窗可能还没到期，但这一场已经开场了
    deadline = logic.enroll_deadline(act, g)
    if t > deadline:
        raise HTTPException(
            409, "报名已截止" if deadline == act.reg_end else
            f"「{g.name}」已于 {g.start_time:%m-%d %H:%M} 开场，不再接受报名")

    # 老师类型不参与报名（无信用分/时长等个人数据）
    if user.user_type == "teacher":
        raise HTTPException(403, "老师类型用户不参与志愿报名")

    # 学生必须先完善个人信息（数智人大拿不到手机号/微信号/身份证号）
    if not user.profile_complete():
        raise HTTPException(403, "请先完善个人信息（手机号/微信号/身份证号）后再报名")

    # 招募范围（活动级）与场次条件：不满足的人根本不该出现在这场的名单上。
    # assign 模式下报名时没有岗位可选，岗位那层条件轮不到，只能靠这两层。
    scope = logic.unmet_recruit_scope(user, act)
    if scope:
        raise HTTPException(403, f"本活动的招募范围不含您：{'；'.join(scope)}")
    unmet_shift = logic.unmet_shift_requirements(user, g)
    if unmet_shift:
        raise HTTPException(403, f"您不满足场次「{g.name}」的条件："
                                 f"{'；'.join(unmet_shift)}")


@router.post("/{activity_id}/shifts/{shift_id}/enroll")
def enroll(activity_id: str, shift_id: str, body: EnrollBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    g = _get_group(db, shift_id)
    if g.activity_id != act.id:
        raise HTTPException(400, "场次不属于该活动")
    if act.is_screened():
        raise HTTPException(409, "本活动需填写报名表申请，由负责老师筛选后录取")

    _check_eligibility(db, user, act, g)
    # 报名表答案：题目不存在就是空表，答案照样为空
    from .screening import validate_answers
    answers = validate_answers(db, act, user, body.answers or {})

    # 岗位有效性与硬性条件（与名额无关，锁外先判）。
    # 「截止后统一分配」的场次不让学生挑岗位，一律落到占位岗位上——
    # 名额、候补、签到、服装全都挂在岗位上，报名却不给岗位的话，
    # 这些账就得再记一套。
    if g.needs_assign():
        position = g.placeholder()
        if position is None:
            raise HTTPException(409, f"场次「{g.name}」尚未就绪，请联系负责老师")
    elif body.position_id:
        position = db.get(Position, body.position_id)
        if position is None or position.shift_id != g.id:
            raise HTTPException(400, "岗位无效")
        unmet = logic.unmet_requirements(user, position)
        if unmet:
            raise HTTPException(
                403, f"您不满足「{position.name}」的报名条件：{'；'.join(unmet)}")
    else:
        raise HTTPException(400, "请选择具体岗位")

    # ★ 时段冲突（粗粒度）：与所有有效报名的场次比区间，重叠即拦截。
    # 候补中的场次同样算已占用——排了队就等于预留了这段时间。
    # 培训/彩排是连带报上的，它们的时间也要一起比：报完才发现彩排那晚
    # 有别的活动，等于把冲突留给了现场。
    for target in [g] + logic.attached_shifts(
            db, g.id, position.id if position else None):
        clashes = logic.time_conflicts(db, user, target)
        if clashes:
            extra = "" if target is g else f"（{target.kind_label()}：{target.name}）"
            raise HTTPException(409, {
                "message": f"时间段与您已报名的活动冲突{extra}："
                           f"{logic.conflict_message(clashes)}",
                "conflicts": clashes})

    # ★ 名额判断与落库整段串行化到场次粒度，防止超卖与重复报名
    with shift_lock(g.id):
        live = (db.query(Enrollment)
                .filter(Enrollment.shift_id == g.id,
                        Enrollment.user_id == user.id)
                .filter((Enrollment.status.in_(logic.ACTIVE))
                        | (Enrollment.seat_held.is_(True))).first())
        if live is not None:
            if live.seat_held:
                raise HTTPException(
                    409, "您此前在该场次临期退选，名额已按规则扣留，无法再次报名；"
                         "如确有需要请联系负责老师")
            label = {"enrolled": "已报名为正式成员", "waitlisted": "已在候补队列中",
                     "pending_confirm": "有候补转正待确认"}[live.status]
            raise HTTPException(409, f"该场次{label}，请勿重复报名")

        enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                         user_id=user.id, joined_at=now(),
                         allow_reassign=body.allow_reassign,
                         leader_willing=body.leader_willing,
                         answers=answers)
        if position is not None:
            enr.position_id = position.id
        if logic.has_room(db, g, position):
            enr.status = "enrolled"
            if position is not None:
                enr.assigned_at = enr.joined_at
        else:
            # 队列上限按岗位算；assign 场次的队列就排在占位岗位上
            queued = [r for r in logic.waitlist_rows(db, g.id)
                      if r.position_id == position.id]
            limit = position.waitlist_quota
            if len(queued) >= limit:
                raise HTTPException(409, f"候补名额已满（上限{limit}人）")
            enr.status = "waitlisted"
            # 候补必须接受调剂：管理者要能把排队的人调到还有空位的岗位，
            # 否则队列会卡在一个岗位上，别处空着没人。
            enr.allow_reassign = True
        db.add(enr)
        db.flush()
        logic.record_change(db, user, enr,
                            "enroll" if enr.status == "enrolled" else "waitlist")

        if enr.status == "enrolled":
            enr.group_qr_seen = True          # 接口返回时就弹给本人看
            logic.sync_attached(db, enr)      # 培训/彩排连带报上
            extra = "".join(
                f"\n{s.kind_label()}：{s.span_text()}"
                f"（{s.location or act.location or '地点待定'}）"
                for s in logic.attached_shifts(
                    db, g.id, position.id if position else None))
            wecom.send_user(db, user, "signup_success", activity=act.title,
                            shift=g.name, time=g.span_text(),
                            detail=(f"岗位：{position.name}（{position.location}）"
                                    if position else "具体岗位将于报名截止后公布")
                                   + extra)
        else:
            db.flush()
            wecom.send_user(db, user, "waitlist_queued", activity=act.title,
                            shift=g.name, pos=logic.waitlist_position(db, enr))
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
        "is_leader": enr.is_leader,
        "message": ("报名成功！名额已锁定。" if enr.status == "enrolled"
                    else "名额已满，已为您加入候补队列（候补默认接受岗位调剂）。"),
        # 报上了就把联络群给到本人；候补的等转正再给
        "contact_group_qr": (act.contact_group_qr
                             if enr.status == "enrolled" else ""),
    }


class ExcuseBody(BaseModel):
    excused: bool
    reason: str = ""


@router.post("/enrollments/{enrollment_id}/excuse")
def set_excuse(enrollment_id: str, body: ExcuseBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(current_user)):
    """培训/彩排请假（或销假）。

    只对附属场次开放：服务场次不来就是退选，那条路有信用分规则，不能靠请假绕过。
    开始之后不能再改——人已经到没到，现场说了算。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    g = db.get(Shift, enr.shift_id)
    if not g.is_attached():
        raise HTTPException(400, "只有培训/彩排可以请假；服务场次请走退选")
    if enr.status != "enrolled":
        raise HTTPException(409, "该报名记录不在生效状态")
    if now() >= g.start_time:
        raise HTTPException(409, f"「{g.name}」已经开始，不能再改请假状态")
    reason = body.reason.strip()
    if body.excused and len(reason) < 2:
        raise HTTPException(400, "请简单说明无法参加的原因")

    act = db.get(Activity, enr.activity_id)
    enr.excused = body.excused
    enr.excuse_reason = reason if body.excused else ""
    enr.excused_at = now() if body.excused else None
    logic.record_change(db, user, enr,
                        "excuse" if body.excused else "unexcuse",
                        reason if body.excused else "")
    # 临近才请假的要立刻让管理者知道：现场少一个人是要补位的
    hours_left = (g.start_time - now()).total_seconds() / 3600
    if body.excused and hours_left < config.LATE_CANCEL_HOURS:
        notify.notify_managers(
            db, act.id, f"[{g.kind_label()}请假] {act.title}",
            f"{user.name}（{user.student_id}）无法参加「{g.name}」"
            f"（{g.span_text()}），距开始不足 {config.LATE_CANCEL_HOURS} 小时。\n"
            f"事由：{reason}", duty="roster")
    db.commit()
    return {"ok": True, "excused": enr.excused}


class ReassignPrefBody(BaseModel):
    allow_reassign: bool


@router.patch("/enrollments/{enrollment_id}/reassign-pref")
def set_reassign_pref(enrollment_id: str, body: ReassignPrefBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    """志愿者改自己的调剂意愿。只是给管理者的参考，活动开始前随时可改。"""
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if enr.status not in logic.ACTIVE:
        raise HTTPException(409, "该报名记录已失效")
    if enr.status == "waitlisted":
        raise HTTPException(409, "候补期间必须接受调剂——"
                                 "有空位的岗位才好把排队的人调过去")
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
    if enr.status == "applied":
        raise HTTPException(409, "申请中的活动请在活动页「撤回申请」")
    if enr.status == "cancelled":
        raise HTTPException(400, "该记录已退选")
    g = db.get(Shift, enr.shift_id)
    if g.is_attached():
        parent = db.get(Shift, g.parent_id)
        raise HTTPException(
            409, f"「{g.name}」是「{parent.name}」的{g.kind_label()}，"
                 f"随该场次报名产生，不能单独退出；如确实不能参加，"
                 f"请退选「{parent.name}」或联系负责老师")
    act = db.get(Activity, enr.activity_id)
    _ensure_not_editing(act)

    with shift_lock(g.id):
        if enr.status == "pending_confirm":
            # 放弃候补转正：名额顺延下一位
            enr.status = "cancelled"
            enr.cancel_type = "abandoned"
            enr.confirm_deadline = None
            logic.record_change(db, user, enr, "abandon")
            db.commit()
            logic.promote_all(db, g)
            db.commit()
            return {"ok": True, "effect": "abandoned",
                    "message": "已放弃候补转正，名额顺延下一位。"}

        if enr.status == "waitlisted":
            enr.status = "cancelled"
            enr.cancel_type = "normal"
            logic.record_change(db, user, enr, "quit_queue")
            db.commit()
            return {"ok": True, "effect": "quit_queue", "message": "已退出候补队列。"}

        # enrolled：活动开始后不可退
        t = now()
        if t >= g.start_time:
            raise HTTPException(409, "活动已开始，无法退选；如有特殊情况请联系负责老师。")

        hours_left = (g.start_time - t).total_seconds() / 3600
        late = hours_left < config.LATE_CANCEL_HOURS
        # 临期退选记一笔（青协在档案里能看到次数），但名额只在「报名已截止」
        # 时才扣留：报名还开着说明平台仍在招人，把位子废掉自相矛盾——
        # 新人能报、候补能转正，唯独这个位子空着谁都不能用。
        reg_closed = bool(act.reg_end and t > act.reg_end)
        hold_seat = late and reg_closed

        vacated, was_leader = enr.position_id, enr.is_leader
        enr.status = "cancelled"
        enr.cancel_type = "late" if late else "normal"
        enr.seat_held = hold_seat
        enr.is_leader = False
        logic.sync_attached(db, enr)     # 培训/彩排跟着退掉

        logic.record_change(
            db, user, enr, "cancel",
            f"距开始 {hours_left:.1f} 小时"
            + ("，临期退选并扣留名额" if hold_seat else "，临期退选" if late else ""))
        db.commit()

        notify.notify_late_dropout(db, enr, user)
        if not hold_seat:
            logic.promote_all(db, g)     # 名额放回去，候补按顺序递补
        if was_leader:
            logic.on_leader_vacated(db, vacated)
        db.commit()

    if hold_seat:
        return {"ok": True, "effect": "held",
                "message": f"距开始不足{config.LATE_CANCEL_HOURS}小时且报名已截止，"
                           f"该名额已按规则扣留，如需补人请联系负责老师。"}
    return {"ok": True, "effect": "released",
            "message": "退选成功，名额已释放给候补队列。"}


@router.post("/enrollments/{enrollment_id}/confirm-promotion")
def confirm_promotion(enrollment_id: str,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    with shift_lock(enr.shift_id):
        db.refresh(enr)
        if enr.status != "pending_confirm":
            raise HTTPException(400, "该记录不在待确认状态")
        if enr.confirm_deadline and now() > enr.confirm_deadline:
            raise HTTPException(409, "确认窗口已超时，名额已顺延")
        logic.confirm_promotion(db, enr, user)
        logic.record_change(db, user, enr, "promote")
        enr.group_qr_seen = True          # 接口返回时就弹给本人看
        db.commit()
    st_name = ""
    if enr.position_id:
        st = db.get(Position, enr.position_id)
        st_name = f"岗位：{st.name}（{st.location}）"
    act = db.get(Activity, enr.activity_id)
    return {"ok": True, "position_id": enr.position_id,
            "message": f"确认成功，已恢复正式报名。{st_name}",
            "contact_group_qr": act.contact_group_qr}
