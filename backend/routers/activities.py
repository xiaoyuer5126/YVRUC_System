"""学生端：活动浏览与报名（冲突提示 / 候补 / 退选 / 转正确认）。"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import and_, case, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

from .. import config, enroll_logic as logic, oplog
from ..database import db_session
from ..deps import current_user
from .. import notify
from ..integrations import msgcenter
from .. import locks
from ..locks import (GATE_FULL, GATE_OK, invalidate_gates, may_recheck_gate,
                     new_gate_ticket, settle_gate, shift_lock,
                     sync_and_take_gate, take_gate)
from ..models import (Activity, Application, Enrollment, Shift, ShiftPeriod, Position,
                      User, like_pattern, now, parse_local_dt, uid)
from ..perm import is_activity_manager, managed_activity_ids
from ..qrcodes import page_qr
from .. import pubcache
from ..serializers import (activity_detail, brief_parts, display_status_of,
                           recruit_scope_text, reg_started, with_times)
from .approvals import can_review

router = APIRouter(prefix="/api/activities", tags=["activities"])

# 学生可见的活动状态：筹备中的活动尚未定稿，只有管理者看得到
PUBLIC_STATUSES = ("published", "closed", "finished", "vb_pending", "completed")


def is_public(act: Activity) -> bool:
    """学生看得到这个活动吗。补记录活动（不公开招募）任何状态都不公开：
    它只是把线下办过的活动补记进来，志愿者在「我的志愿时长」里看到自己那笔账，
    不需要、也不该从广场或转发的链接点进一个报不了名的活动页。"""
    return act.status in PUBLIC_STATUSES and not act.is_record()


def _ensure_not_editing(act: Activity) -> None:
    """编辑冻结期间不接受报名与退选。

    学生不该报进一个正在被改的岗位表——名额、条件、组长都可能下一秒就变；
    也不该在管理者调整到一半时退出，让对方看到一份自己动着动着就变了的名单。
    """
    if act.editing_since is not None:
        raise _editing_paused()


def _editing_paused() -> HTTPException:
    return HTTPException(409, "负责人正在调整本活动的安排，报名暂停中，请稍后再试")


def _involved_activity_ids(db: OrmSession, user: User) -> set[str]:
    """这个人报过名或投过申请的活动。列表里用它一次性豁免，不逐条查。"""
    ids = {a for (a,) in db.query(Enrollment.activity_id)
           .filter(Enrollment.user_id == user.id).distinct()}
    ids |= {a for (a,) in db.query(Application.activity_id)
            .filter(Application.user_id == user.id).distinct()}
    return ids


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
    if is_public(act):
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


def _date_range(start: str, end: str) -> tuple[datetime | None, datetime | None]:
    """广场的时间范围：两个 YYYY-MM-DD，返回 [起, 止)，end 含当天整天。

    志愿者找的是「这段时间有空的」，所以按服务场次的跨度与区间**有交集**算：
    跨天的活动沾到任一天就出现。
    """
    lo = hi = None
    if start.strip():
        try:
            lo = parse_local_dt(start)
        except ValueError:
            raise HTTPException(400, "开始日期格式不对") from None
    if end.strip():
        try:
            hi = parse_local_dt(end) + timedelta(days=1)
        except ValueError:
            raise HTTPException(400, "结束日期格式不对") from None
    return lo, hi


# 按可参与时段筛：一格两小时，从 8 点排到 22 点
SLOT_HOURS = (8, 10, 12, 14, 16, 18, 20)
SLOT_LEN = timedelta(hours=2)
SLOT_MAX = 7 * len(SLOT_HOURS) * 5      # 翻着周选，五周的格子够用了


def _slots(raw: str) -> list[tuple[datetime, datetime]]:
    """「2026-10-26T08,2026-10-26T10」→ 各格的 [起, 止)。格子只能落在整两小时上。"""
    out = []
    for part in [p for p in raw.split(",") if p.strip()]:
        try:
            t = datetime.strptime(part.strip(), "%Y-%m-%dT%H")
        except ValueError:
            raise HTTPException(400, "时段格式不对") from None
        if t.hour not in SLOT_HOURS:
            raise HTTPException(400, "时段格式不对")
        out.append((t, t + SLOT_LEN))
    if len(out) > SLOT_MAX:
        raise HTTPException(400, "选的时段太多了，少选几格再试")
    return out


def _slot_hits(db: OrmSession, slots: list[tuple[datetime, datetime]],
               activity_ids=None):
    """和所选格子沾边的服务场次：(活动 id, 场次 id, 时段数, 是否时间灵活)。

    有一段和任一格重叠就算：多时段的场次要求同一批人每段都来，卡片上另外
    标出共几段，由志愿者自己判断。时间灵活的场次钟点只是个大概，所选格子
    所在的日子里有它的时段就算。
    """
    exact = or_(*[and_(ShiftPeriod.start_time < hi, ShiftPeriod.end_time > lo)
                  for lo, hi in slots])
    days = {lo.date() for lo, _hi in slots}
    loose = or_(*[and_(ShiftPeriod.start_time < datetime.combine(d, datetime.min.time())
                       + timedelta(days=1),
                       ShiftPeriod.end_time > datetime.combine(d, datetime.min.time()))
                  for d in days])
    n_periods = (db.query(func.count(ShiftPeriod.id))
                 .filter(ShiftPeriod.shift_id == Shift.id)
                 .correlate(Shift).scalar_subquery())
    q = (db.query(Shift.activity_id, Shift.id, n_periods, Shift.flexible_time)
         .join(ShiftPeriod, ShiftPeriod.shift_id == Shift.id)
         .filter(Shift.kind == "service",
                 or_(exact, and_(Shift.flexible_time.is_(True), loose))))
    if activity_ids is not None:
        q = q.filter(Shift.activity_id.in_(activity_ids))
    return q.distinct()


# 广场上各展示状态的先后（见 list_activities）
SQUARE_ORDER = {"published": 0, "upcoming": 1, "closed": 2, "ongoing": 3,
                "finished": 4}


@router.get("")
def list_activities(q: str = "", mode: str = "", organizer: str = "",
                    status: str = "", start: str = "", end: str = "",
                    slots: str = "",
                    page: int = 1, size: int = 12,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(current_user)):
    """广场列表：按标题/类型/组织单位/展示状态/时间范围筛，分页返回。

    重活（数场次、数报名）只对当页的十几条做。筛选与排序尽量交给 SQL：候选集是
    「全部公开活动」，活动攒到几千条时每个请求都要把它拉一遍，所以时间范围和
    「报名中 / 未开始 / 已截止 / 进行中」先用一条宽松的条件把候选行砍下来。
    SQL 只做预筛，最终展示状态仍按 display_status_of 按时间算，口径不变。

    slots 是按可参与时段筛选选中的格子（见 _slots），只看报名中的活动。
    """
    rows = db.query(Activity.id, Activity.status, Activity.reg_start,
                    Activity.reg_end, Activity.recruit_campuses,
                    Activity.recruit_stages, Activity.recruit_colleges,
                    Activity.pinned_at)
    rows = rows.filter(Activity.status.in_(PUBLIC_STATUSES),
                       Activity.recruit_mode != config.RECORD_MODE)
    if q.strip():
        rows = rows.filter(Activity.title.ilike(like_pattern(q.strip()),
                                                escape="\\"))
    if mode:
        rows = rows.filter(Activity.recruit_mode == mode)
    if organizer:
        rows = rows.filter(Activity.organizer == organizer)
    # 时间范围：与服务场次的跨度有交集的活动才可能入选（培训/彩排跟着服务场次走）
    lo, hi = _date_range(start, end)
    if lo is not None or hi is not None:
        hit = db.query(Shift.activity_id).filter(Shift.kind == "service")
        if lo is not None:
            hit = hit.filter(Shift.end_time >= lo)
        if hi is not None:
            hit = hit.filter(Shift.start_time < hi)
        rows = rows.filter(Activity.id.in_(hit))
    # 可参与时段：找的是现在还能报的，只看报名中
    picked_slots = _slots(slots)
    if picked_slots:
        status = "published"
        rows = rows.filter(Activity.id.in_(
            _slot_hits(db, picked_slots).with_entities(Shift.activity_id)))
    # 状态预筛：这几种展示状态只可能从这几个业务状态里出来，先把历史活动砍掉
    if status in ("published", "upcoming"):
        rows = rows.filter(Activity.status == "published")
    elif status in ("ongoing", "closed"):
        rows = rows.filter(Activity.status.in_(("published", "closed")))
    # 急招的排最前（后置顶的更靠前），其余按创建时间；展示状态的先后在下面
    # 算完状态后再排
    urgent = case((Activity.pinned_at.isnot(None)
                   & (Activity.status == "published"), 0), else_=1)
    rows = rows.order_by(urgent,
                         func.coalesce(Activity.pinned_at,
                                       Activity.created_at).desc()).all()

    # {活动: ((最早开始, 最晚结束), 最晚开始)}，只算服务场次（同 Activity.time_span）。
    # 只查筛出来的这些活动：候选集小了，这条分组查询也跟着小
    cand = [r[0] for r in rows]
    spans = {aid: ((first, last), last_start) for aid, first, last, last_start in
             db.query(Shift.activity_id, func.min(Shift.start_time),
                      func.max(Shift.end_time), func.max(Shift.start_time))
             .filter(Shift.kind == "service", Shift.activity_id.in_(cand))
             .group_by(Shift.activity_id).all()} if cand else {}
    # 招募范围外的活动不上广场：看得见报不了，只会多一次被拦的失望。
    # 报过名的人不受此限（范围是后来缩的），自己管的活动同样照常显示——
    # 管理者要从这里进详情核对志愿者看到的样子。
    managed = managed_activity_ids(db, user)
    involved = _involved_activity_ids(db, user)

    shown = []
    for aid, st, reg_start, reg_end, campuses, stages, colleges, pinned in rows:
        span, last_start = spans.get(aid, (None, None))
        code, _label = display_status_of(st, reg_start, reg_end, span, last_start)
        if status and code != status:
            continue
        if (managed is None or aid in managed or aid in involved
                or not logic.unmet_for_tags(
                    user, logic.recruit_scope_tags(campuses, stages, colleges))):
            shown.append((pinned is not None and st == "published",
                          SQUARE_ORDER.get(code, len(SQUARE_ORDER)), aid))
    # 「全部状态」下还能报的排在前面：急招，再报名中、未开始，再已截止、进行中、
    # 已结束。sorted 是稳定的，同一档里保持上面 SQL 的先后
    ids = [aid for _u, _r, aid in sorted(shown, key=lambda x: (not x[0], x[1]))]

    size = max(1, min(size, 50))
    page = max(1, page)
    picked = ids[(page - 1) * size: page * size]
    # 卡片内容与看的人无关，走 3 秒缓存（见 pubcache）；自己管的活动例外，
    # 管理者改完回广场核对，得看到刚改的样子。时间字段每次现算（with_times）
    fresh = [i for i in picked if managed is None or i in managed]
    found = pubcache.get_many("brief", [i for i in picked if i not in fresh],
                              lambda want: brief_parts(db, want))
    found.update(brief_parts(db, fresh))
    items = [with_times(found[i]) for i in picked if i in found]
    if picked_slots and items:
        # 卡片上提醒一句：沾边的场次里有要来好几段的、或者钟点只是个大概的
        notes: dict[str, dict] = {}
        for aid, _gid, n, flexible in _slot_hits(db, picked_slots,
                                                 [x["id"] for x in items]):
            note = notes.setdefault(aid, {"periods": 1, "flexible": False})
            note["periods"] = max(note["periods"], n)
            note["flexible"] = note["flexible"] or bool(flexible)
        for x in items:
            x["slot_note"] = notes.get(x["id"])
    return {
        "items": items,
        "total": len(ids), "page": page, "size": size,
        "organizers": [o for (o,) in
                       db.query(Activity.organizer)
                       .filter(Activity.status.in_(PUBLIC_STATUSES),
                               Activity.recruit_mode != config.RECORD_MODE,
                               Activity.organizer != "")
                       .distinct().order_by(Activity.organizer).all()],
        # 联合主办方的历史值：给新建/编辑表单做输入建议。筛选、统计仍按
        # organizer 走，合办方多一个不代表这场活动归到别处
        "co_organizers": [o for (o,) in
                          db.query(Activity.co_organizer)
                          .filter(Activity.status.in_(PUBLIC_STATUSES),
                                  Activity.recruit_mode != config.RECORD_MODE,
                                  Activity.co_organizer != "")
                          .distinct().order_by(Activity.co_organizer).all()],
    }


@router.get("/{activity_id}")
def get_activity(activity_id: str,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    manager = is_activity_manager(db, user, activity_id)
    # 审批人也按管理者视角渲染：培训内容、学习材料这些是判断活动办得成不成的
    # 依据，看不到就批不下去。只在活动**还没公开**时给——那时一个报名的人都
    # 没有，不存在暴露联系方式的问题；一旦公开，他和其他人看到的就该是同一页。
    review = (not manager and not is_public(act)
              and can_review(db, user, act))
    # 管理者要能在同一个页面核对培训内容与组长，不必先去后台
    # 管理视角不走缓存：刚改完的东西必须立刻看到原样
    out = activity_detail(db, act, user, manager=manager or review,
                          cached=not (manager or review))
    out["manager"] = manager
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
    answers: dict = {}                # 活动设了报名问卷时的答案（题目 id → 答案）
    confirm_conflict: bool = False    # 看过时段冲突的提示、仍要报


def _check_eligibility(db: OrmSession, user: User, act: Activity,
                       g: Shift) -> None:
    """报名资格：活动状态、报名窗、用户类型、资料、招募范围与场次条件。

    与名额无关的校验全在这里，锁外先跑一遍，把明确报不了的人挡在锁外面。
    """
    if act.status != "published":
        raise HTTPException(409, f"活动当前为「{act.label_status()}」，不在报名期")
    _ensure_not_editing(act)

    t = now()
    # 与详情页的 enroll_open、倒计时毫秒数同一个判断（serializers.reg_started）
    if not reg_started(act.reg_start, t):
        raise HTTPException(409, f"报名尚未开始（{act.reg_start:%Y-%m-%d %H:%M} 起）")
    # 截止按场次各算各的：活动的报名窗可能还没到期，但这一场已经开场了
    deadline = logic.enroll_deadline(act, g)
    if t > deadline:
        raise HTTPException(
            409, "报名已截止" if deadline == act.reg_end else
            f"「{g.name}」已于 {g.start_time:%Y-%m-%d %H:%M} 开场，不再接受报名")

    # 老师类型不参与报名（无信用分/时长等个人数据）
    if user.user_type == "teacher":
        raise HTTPException(403, "老师类型用户不参与志愿报名")

    # 学生必须先完善个人信息（学校接口里没有手机号/微信号/身份证号/校区）
    if not user.profile_complete():
        raise HTTPException(403, "请先完善个人信息（手机号/微信号/校区/身份证号）后再报名")

    # 招募范围（活动级）与场次条件：不满足的人根本不该出现在这场的名单上。
    # assign 模式下报名时没有岗位可选，岗位那层条件轮不到，只能靠这两层。
    scope = logic.unmet_recruit_scope(user, act)
    if scope:
        raise HTTPException(403, f"本活动的招募范围不含您：{'；'.join(scope)}")
    unmet_shift = logic.unmet_shift_requirements(user, g)
    if unmet_shift:
        raise HTTPException(403, f"您不满足场次「{g.name}」的条件："
                                 f"{'；'.join(unmet_shift)}")


def _room_left(db, g, position) -> int:
    """岗位还剩几个正式名额（待确认的也算占着，见 logic.position_occupied_count）。
    占位岗位按整场数，同 logic.has_room。"""
    if position.is_placeholder:
        return position.quota - logic.shift_occupied_count(db, g.id)
    return position.quota - logic.position_occupied_count(db, position.id)


def _seat_gate_id(g, position_id: str | None) -> str | None:
    """名额闸门挂在哪个岗位上。assign 场次一律是占位岗位：报名都从它进，
    名额按整场数，所以这一场里谁退选（哪怕已经分到了真实岗位）都要作废它。"""
    holder = g.placeholder() if g.needs_assign() else None
    return holder.id if holder else position_id


def _waitlist_left(db, g, position) -> int:
    """岗位的候补队列还剩几个位子。"""
    if position.waitlist_quota <= 0:
        return 0        # 不设候补：连查都不用查，这条路上每次查库都是白花
    return position.waitlist_quota - logic.position_waitlist_count(
        db, g.id, position.id)


def _gate(db, g, position, kind, left_fn, ticket) -> bool | None:
    """闸门放行吗。返回 True（号已扣在手里）/ False（没位子）/ None（闸门用不了）。

    闸门说「满了」时，限流地拿数据库核一遍（见 locks 的「名额闸门」）；没预热时
    必核。核完按「数据库剩余 − 在途」对齐计数器再扣——不能直接写成数据库剩余，
    那会把在途的号全还回去。**Redis 用不了时返回 None**：闸门只是加速用的，
    它不在就该退回「每个请求都查库」，而不是反过来把人挡在外面。
    """
    got = take_gate(g.id, position.id, kind, ticket)
    if got == GATE_OK:
        return True
    if got == GATE_FULL and not may_recheck_gate(g.id, position.id, kind):
        return False        # 这一小会儿里已经有人核过了：满就是满
    left = left_fn(db, g, position)
    got = sync_and_take_gate(g.id, position.id, kind, ticket, max(0, left))
    if got == GATE_OK:
        return True
    return False if got == GATE_FULL else None


def _settle_gates(g, position, tickets: dict, status: str | None) -> None:
    """给闸门结账。tickets：{kind: ticket}，只含扣到了号的那几种。

    status 为 None 表示没报上：号原样还回去。报上了的话，用掉的那个只从在途里
    划掉；扣了却没用上的（比如扣了候补号、结果数据库说有正式名额）还回去。
    扣了正式号却进了候补——数据库说其实没位子，那个号不还，还了计数器只会
    更虚高。
    """
    used = {"enrolled": "seat", "waitlisted": "waitlist"}.get(status or "")
    for kind, ticket in tickets.items():
        restore = status is None or (kind == "waitlist" and kind != used)
        settle_gate(g.id, position.id, kind, ticket, restore=restore)


def _check_time_conflicts(rows, g: Shift, attached: list[Shift]) -> None:
    """拿 logic.active_rows 查回来的有效报名，和主场次及其培训/彩排逐个比时段，
    有重叠就回「时段冲突」的 409（logic.conflict_error），由志愿者确认后重发。

    一次查、在内存里比，不必每个目标场次各查一遍全部报名。本活动别的场次的
    报名同样算：同一活动里两场时间压着，也只能去一场。
    """
    clashes, where = [], ""
    for target in [g] + attached:
        hit = logic.conflicts_among(rows, target)
        if hit and not clashes and target is not g:
            where = f"（{target.kind_label()}：{target.name}）"
        clashes += hit
    if clashes:
        clashes = logic.dedupe_conflicts(clashes)
        raise logic.conflict_error(
            f"时间段与您已报名的场次冲突{where}："
            f"{logic.conflict_message(clashes, g.activity_id)}", clashes)


@router.post("/{activity_id}/shifts/{shift_id}/enroll")
def enroll(activity_id: str, shift_id: str, body: EnrollBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    act = _get_activity(db, activity_id, user)
    g = _get_group(db, shift_id)
    if g.activity_id != act.id:
        raise HTTPException(400, "场次不属于该活动")
    if act.is_record():
        raise HTTPException(409, "本活动为补记录活动，不接受报名")
    if act.is_screened():
        raise HTTPException(409, "本活动需填写报名问卷申请，由负责人筛选后录取")

    # 岗位有效性与硬性条件（与名额无关，锁外先判）。
    # 「截止后统一分配」的场次不让学生挑岗位，一律落到占位岗位上——
    # 名额、候补、签到、服装全都挂在岗位上，报名却不给岗位的话，
    # 这些账就得再记一套。
    if g.needs_assign():
        position = g.placeholder()
        if position is None:
            raise HTTPException(409, f"场次「{g.name}」尚未就绪，请联系负责人")
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

    # 暂停、锁定排在闸门前面：停了就不该占闸门的号
    why = logic.closed_reason(g, position)
    if why:
        raise HTTPException(409, why)

    # ★ 名额闸门排在资格校验**前面**，这是它存在的全部意义：几百个请求同一瞬间
    # 到的时候，抢不到的那些只花一次 Redis 往返就被挡回去，下面那十来条资格
    # 查询、时段比较、场次锁根本不会执行。闸门只挡洪水、不判定名额——拿不到号
    # 也可能是计数器旧了（_gate 会拿数据库核一遍），拿到号也照样要走下面那条
    # 以数据库为准的判定，所以它旧了也不会超卖。
    ticket = new_gate_ticket()
    tickets: dict[str, str] = {}      # 扣到了号的闸门
    seat_gate = _gate(db, g, position, "seat", _room_left, ticket)
    if seat_gate:
        tickets["seat"] = ticket
    elif seat_gate is False:
        wl_gate = _gate(db, g, position, "waitlist", _waitlist_left, ticket)
        if wl_gate:
            tickets["waitlist"] = ticket
        elif wl_gate is False:
            raise HTTPException(409, f"候补名额已满（上限{position.waitlist_quota}人）")

    enrolled = False          # 报名有没有真的落库
    try:
        _check_eligibility(db, user, act, g)

        # 报名问卷答案：题目不存在就是空表，答案照样为空。挂了适用岗位的题只问
        # 报这个岗位的人，所以要等岗位定下来再校验
        from .screening import validate_answers
        answers = validate_answers(db, act, user, body.answers or {}, [position.id])

        # ★ 时段冲突（粗粒度）：与所有有效报名的场次比区间，重叠就先提示，
        # 志愿者确认仍要报（confirm_conflict）才往下走。
        # 候补中的场次同样算已占用——排了队就等于预留了这段时间。
        # 培训/彩排是连带报上的，它们的时间也要一起比：报完才发现彩排那晚
        # 有别的活动，等于把冲突留给了现场。
        #
        # 附属场次只在这里查一次，锁里的时段重查、连带报名、通知正文都用它。
        # 锁外查到的会不会过时？改培训/彩排（增删、改时间、改适用岗位）必须
        # 先进编辑态，而锁里第一条 SQL（enroll_ledger）会再看一眼编辑态：
        # 从这里到进锁之间谁开始改了，锁里就按「报名暂停」拒掉，不会拿一份
        # 旧名单去落库。进锁之后才开始的编辑只剩「锁里那几毫秒」的窗口
        # （编辑接口不拿场次锁，锁里再查一遍也挡不住它）。
        attached_all = logic.attached_shifts(db, g.id)
        attached = logic.for_position(attached_all, position.id)
        if not body.confirm_conflict:
            _check_time_conflicts(logic.active_rows(db, user), g, attached)

        # 上面全是读，先落一个空事务把**数据库连接还回池子**，再去抢场次锁。
        # 不还的话，排进程名额（见 locks.shift_lock）的这段时间是「占着一条连接
        # 在等」：请求一多，池子会被等锁的人占满，反而轮到锁的人拿不到连接去
        # 干活——两边互相等，一直到连接池 30 秒超时才一批批散开。
        # （commit 而不是 rollback：这就是读阶段的正常收尾，语义更直白；
        #   本会话 expire_on_commit=False，对象在 commit 后照样能读。）
        db.commit()

        # ★ 名额判断与落库整段串行化到场次粒度，防止超卖与重复报名。
        # 走完闸门的请求最多只剩「名额数」这么多个，所以等锁的队伍很短，
        # 不会几百个一起排队、大半等到超时。
        # 扣到号的人是先到的那批，多给它们等一会儿，别让 503 把名额送给后来者
        # 锁随事务走：下面判定、落库、结算闸门都在这一个事务里，最后那次
        # commit 同时就是放锁
        #
        # 场次锁之后紧接着拿**这个人的**用户锁（locks.user_lock）：时段冲突是
        # 跨场次的，同一个人同时报两场重叠的班，两个请求拿的是两把不同的场次锁，
        # 锁外各查各的都看不见对方。用户锁把同一个人的报名排成队，锁里再比一遍
        # 时段，后到的那个一定看得见先到的已提交的那条。先场次后用户、拿了用户锁
        # 不再等场次锁，所以不会死锁（见 locks 的「用户锁」）。
        with shift_lock(db, g.id, wait=(locks.TICKET_WAIT_TIMEOUT if tickets
                                        else None), user_id=user.id):
            # 查重、数名额、数候补、看编辑态、重比时段合成一条（见 logic.enroll_ledger）。
            # 它发在拿到用户锁之后：读已提交下每条语句一个新快照，同一个人
            # 先到的那个请求已提交的报名，这里一定看得见
            ledger = logic.enroll_ledger(db, act.id, g.id, position.id, user.id,
                                         [g] + attached,
                                         whole_shift=position.is_placeholder)
            if ledger.mine is not None:
                label = {"enrolled": "已报名为正式成员", "waitlisted": "已在候补队列中",
                         "pending_confirm": "有候补转正待确认"}.get(
                             ledger.mine, "已有报名记录")
                raise HTTPException(409, f"该场次{label}，请勿重复报名")
            # 排锁期间负责人进了编辑态：附属场次、名额都可能正在改，锁外那份
            # 名单不能再用（见上面查附属场次处）
            if ledger.editing:
                raise _editing_paused()
            # 排锁期间这一场开放了选岗，「待分配」已经删掉
            if ledger.gone:
                raise HTTPException(409, f"场次「{g.name}」刚开放选岗，请刷新后选择岗位再报名")
            # 排锁期间负责人点了暂停、锁定，或者过了自动锁定时刻：锁外那一关
            # 已经过时，按锁里读到的再判一次，不然暂停期间名单还在动
            logic.take_flags(g, position, ledger)
            why = logic.closed_reason(g, position)
            if why:
                raise HTTPException(409, why)
            # 同一个人别的报名可能刚在别的场次锁下提交：持用户锁重比一遍时段。
            # 有没有冲突已经在上面那条里数过了，真撞上才去查明细拼提示。
            # 已确认冲突的不再比，名额照样按下面锁里数到的判
            if ledger.clash and not body.confirm_conflict:
                _check_time_conflicts(logic.active_rows(db, user), g, attached)

            enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                             user_id=user.id, joined_at=now(),
                             allow_reassign=body.allow_reassign,
                             leader_willing=body.leader_willing,
                             answers=answers)
            if position is not None:
                enr.position_id = position.id
            wl_pos = 0
            if ledger.occupied < position.quota:        # 口径同 logic.has_room
                enr.status = "enrolled"
                enr.assigned_at = enr.joined_at
            else:
                # 队列上限按岗位算；assign 场次的队列就排在占位岗位上
                limit = position.waitlist_quota
                if ledger.queued >= limit:
                    raise HTTPException(409, f"候补名额已满（上限{limit}人）")
                # 排在队尾：锁里数到的人都比我早（joined_at 更小），
                # 与 logic.waitlist_position 按 joined_at 排出来的名次一致
                wl_pos = ledger.queued + 1
                enr.status = "waitlisted"
                # 调剂意愿是本人勾的（默认接受），候补也一样：排班时只用来提示和
                # 留痕，不拦分配（不接受的人被调换时点名，见 assign.apply_plan）
            db.add(enr)
            db.flush()
            logic.record_change(db, user, enr,
                                "enroll" if enr.status == "enrolled" else "waitlist")

            if enr.status == "enrolled":
                logic.sync_attached(db, enr, attached_all)   # 培训/彩排连带报上
                extra = "".join(
                    f"\n{s.kind_label()}：{s.span_text()}"
                    f"（{s.location or act.location or '地点待定'}）"
                    for s in attached)
                msgcenter.send_user(db, user, "signup_success", activity=act.title,
                                shift=g.name, time=g.span_text(),
                                detail=(f"岗位：{position.name}（{position.location}）"
                                        if position else "具体岗位将于报名截止后公布")
                                       + extra)
            else:
                msgcenter.send_user(db, user, "waitlist_queued", activity=act.title,
                                shift=g.name, pos=wl_pos)
            # 在 commit **之前**结算：commit 之后、结算之前那一瞬，这个号既在
            # 数据库里占着名额、又挂在在途里，别的请求对齐闸门时会把它减两遍，
            # 把有位子的人挡在外面。先结算的话最坏是 commit 失败时少还一个号
            # （下面 finally 里再还是空操作），闸门偏紧，下次核库就对齐回来
            _settle_gates(g, position, tickets, enr.status)
            try:
                db.commit()
            except IntegrityError:  # 唯一索引兜底：锁失效时的并发重复报名
                db.rollback()
                raise HTTPException(409, "该场次您已有报名记录，请勿重复报名")
            enrolled = True         # 到这儿报名已经落库了，剩下的只是收尾
    finally:
        if not enrolled:
            # 上面任何一步没走通（资格不符、时段冲突、名额没了、库出错），
            # 扣在手里的号都要还回去，否则一次失败的报名白占一个名额
            _settle_gates(g, position, tickets, None)

    return {
        "status": enr.status,
        "enrollment_id": enr.id,
        "waitlist_position": wl_pos,
        "is_leader": enr.is_leader,
        "message": ("报名成功，名额已锁定。" if enr.status == "enrolled"
                    else "名额已满，已为您加入候补队列。"),
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
    管理者在场次开始前几天的人员变动日报里看到请假和销假（scheduled.send_roster_digests）。
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

    enr.excused = body.excused
    enr.excuse_reason = reason if body.excused else ""
    enr.excused_at = now() if body.excused else None
    logic.record_change(db, user, enr,
                        "excuse" if body.excused else "unexcuse",
                        reason if body.excused else "")
    db.commit()
    return {"ok": True, "excused": enr.excused}


class ReassignPrefBody(BaseModel):
    allow_reassign: bool


@router.patch("/enrollments/{enrollment_id}/reassign-pref")
def set_reassign_pref(enrollment_id: str, body: ReassignPrefBody, request: Request,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    """志愿者改自己的调剂意愿。只是给管理者的参考，活动开始前随时可改。

    候补也照本人勾的来：这个字段不拦分配，只在排班时提示与留痕（不接受的人被
    调换时点名，见 assign.apply_plan）。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if enr.status not in logic.ACTIVE:
        raise HTTPException(409, "该报名记录已失效")
    # 请求正文不进操作日志，改成了什么由这里报一句（见 oplog.mark_note）
    oplog.mark_note(request, "接受调剂" if body.allow_reassign else "不接受调剂")
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
        raise HTTPException(
            409, f"「{g.name}」是本活动的{g.kind_label()}，随服务场次的报名产生，"
                 f"不能单独退出；不能参加的话请点「我无法参加」请假，或联系负责人")
    act = db.get(Activity, enr.activity_id)
    _ensure_not_editing(act)

    # 把连接还回池子再去抢锁，理由同 enroll
    db.commit()

    # 退选、递补、通知在同一个事务里：场次锁在 commit 时就放了，要是「先提交
    # 退选、再递补」，中间那一瞬别人能看到放出来的位子、抢在候补队首前面报上
    with shift_lock(db, g.id):
        # 锁外读到的状态可能已经旧了（连点两下退选：第一下在锁里退掉、放出名额、
        # 候补顶上；第二下拿到锁时手里的 enr 还是「已报名」）。会话是
        # expire_on_commit=False，不重读就会照着旧状态再退一遍。
        # 场次和岗位也重读：锁没锁、停没停决定记不记「锁定后退出」、给不给候补
        db.refresh(enr)
        logic.reload_shift(db, g)
        if enr.status not in ("pending_confirm", "waitlisted", "enrolled"):
            raise HTTPException(400, "该记录已退选")
        if enr.status == "pending_confirm":
            # 放弃候补转正：名额顺延下一位
            enr.status = "cancelled"
            enr.cancel_type = "abandoned"
            enr.cancelled_at = now()
            enr.confirm_deadline = None
            logic.record_change(db, user, enr, "abandon")
            db.flush()                   # 递补按落库后的人数算，但先不提交（见上）
            # 锁定后这个位子不会自动补上：和退选同一个口径告诉管理者
            notify.notify_locked_dropout_if_locked(db, enr, user, what="abandon")
            logic.promote_all(db, g)
            db.commit()
            invalidate_gates(g.id, _seat_gate_id(g, enr.position_id))
            return {"ok": True, "effect": "abandoned",
                    "message": "已放弃候补转正，名额顺延下一位。"}

        if enr.status == "waitlisted":
            enr.status = "cancelled"
            enr.cancel_type = "normal"
            enr.cancelled_at = now()
            logic.record_change(db, user, enr, "quit_queue")
            db.commit()
            invalidate_gates(g.id, _seat_gate_id(g, enr.position_id))
            return {"ok": True, "effect": "quit_queue", "message": "已退出候补队列。"}

        # enrolled：活动开始后不可退
        t = now()
        if t >= g.start_time:
            raise HTTPException(409, "活动已开始，无法退选；如有特殊情况请联系负责人。")

        hours_left = (g.start_time - t).total_seconds() / 3600
        # 名单锁定（整场或他所在的岗位）后退出记一笔「锁定后退出」，青协在档案里
        # 看得到次数；空出的位子不自动给候补，由管理者手动转正
        position = db.get(Position, enr.position_id) if enr.position_id else None
        locked = logic.position_locked(g, position)

        vacated, was_leader = enr.position_id, enr.is_leader
        enr.status = "cancelled"
        enr.cancel_type = "locked" if locked else "normal"
        enr.cancelled_at = t
        enr.is_leader = False
        logic.sync_attached(db, enr)     # 培训/彩排跟着退掉

        logic.record_change(
            db, user, enr, "cancel",
            f"距开始 {hours_left:.1f} 小时" + ("，名单锁定后退出" if locked else ""))
        db.flush()                       # 同上：只 flush，和递补一起提交

        if locked:
            notify.notify_locked_dropout(db, enr, user)
        # 名额放回去，候补按顺序递补；暂停、锁定时 promote_all 原地不动
        logic.promote_all(db, g)
        if was_leader:
            logic.on_leader_vacated(db, vacated)
        db.commit()
        # 名额在闸门之外变了（放出一个正式名额，或候补顶上后空出一个排队位）：
        # 作废计数器，下一个报名按数据库重新对齐，别让刚放出来的位子被挡住
        invalidate_gates(g.id, _seat_gate_id(g, vacated))

    if locked:
        return {"ok": True, "effect": "locked",
                "message": "已退出。名单已锁定，本次退出已记录。"}
    return {"ok": True, "effect": "released", "message": "退选成功。"}


@router.post("/enrollments/{enrollment_id}/confirm-promotion")
def confirm_promotion(enrollment_id: str,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(current_user)):
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    db.commit()          # 还连接再抢锁，理由同 enroll
    with shift_lock(db, enr.shift_id):
        db.refresh(enr)
        if enr.status != "pending_confirm":
            raise HTTPException(400, "该记录不在待确认状态")
        if enr.confirm_deadline and now() > enr.confirm_deadline:
            raise HTTPException(409, "确认窗口已超时，名额已顺延")
        logic.confirm_promotion(db, enr, user)
        logic.record_change(db, user, enr, "promote")
        db.commit()
    st_name = ""
    if enr.position_id:
        st = db.get(Position, enr.position_id)
        st_name = f"岗位：{st.name}（{st.location}）"
    act = db.get(Activity, enr.activity_id)
    return {"ok": True, "position_id": enr.position_id,
            "message": f"确认成功，已恢复正式报名。{st_name}",
            "contact_group_qr": act.contact_group_qr}
