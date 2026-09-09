"""教师/青协端：活动筹备（活动与岗位树 CRUD）、报名名单、二次分配。

岗位树校验（《设计方案》二）：
  · 岗位时间必须被包裹在场次时间范围内；
  · 场次总人数需大于等于下属岗位人数之和。
所有增删改走审计留痕；影响志愿者的时间/地点类修改会推送「活动变更警告」，
附变更前后对比摘要。
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     UploadFile)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from .. import enroll_logic as logic
from .. import assign, config, copying, scoring
from ..stage import ensure_hours_editable, ensure_not_finished
from ..audit import diff_changes, log as audit_log
from ..database import db_session
from ..deps import require_council, require_manager
from ..locks import shift_lock
from ..notify import notify_enrolled
from ..perm import (activity_manager_ids, add_manager,
                     ensure_activity_manager, managed_activity_ids,
                     remove_manager)
from ..integrations import wecom
from ..models import (Activity, ActivityManager, Application, ApprovalEvent,
                      AttireRecord, AttireTask, AuditLog,
                      CheckinRecord, CheckinTask, Enrollment, FormQuestion,
                      Notification, Position, Shift, User,
                      iso, now, uid)
from ..serializers import activity_brief
from .screening import answers_out

router = APIRouter(prefix="/api/staff", tags=["staff"])

ACTIVITY_FIELDS = {
    "title": "名称", "recruit_mode": "活动类型", "intro": "简介",
    "location": "地点", "organizer": "组织单位",
    "contact_info": "联系方式", "contacts": "联系人",
    "contact_group_qr": "志愿者联络群二维码",
    "volunteer_requirement": "志愿者要求", "volunteer_benefit": "志愿者保障",
    "recruit_campuses": "招募校区", "recruit_stages": "招募学段",
    "reg_start": "报名开始", "reg_end": "报名截止",
}


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


def _shift(db: OrmSession, gid: str) -> Shift:
    g = db.get(Shift, gid)
    if g is None:
        raise HTTPException(404, "场次不存在")
    return g


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v))
    except ValueError:
        raise HTTPException(400, f"时间格式不合法：{v}")


def _notify_assignment_bg(items: list[dict]):
    """后台发送岗位分配通知（apply_plan 收集，路由层调度）。"""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        for it in items:
            u = db.get(User, it["user_id"])
            st = db.get(Position, it["position_id"])
            g = db.get(Shift, it["shift_id"])
            if not (u and st and g):
                continue
            wecom.send_user(db, u, "assignment",
                            activity=db.get(Activity, g.activity_id).title,
                            shift=g.name, position=st.name,
                            position_time=st.time_text(),
                            position_loc=st.location or "待定")
        db.commit()
    finally:
        db.close()


# ======================= 活动列表与基本信息 =======================

@router.get("/activities")
def list_all(q: str = "", status: str = "", page: int = 1, size: int = 20,
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """管理端活动列表。分页返回：每条都要数场次、报名与候补，全量拉会越来越慢。"""
    rows = db.query(Activity).order_by(Activity.created_at.desc())
    managed = managed_activity_ids(db, user)
    if managed is not None:
        rows = rows.filter(Activity.id.in_(managed or ['__none__']))
    if q.strip():
        rows = rows.filter(Activity.title.ilike(f"%{q.strip()}%"))
    if status:
        rows = rows.filter(Activity.status == status)
    total = rows.count()
    size = max(1, min(size, 100))
    page = max(1, page)
    out = []
    for a in rows.offset((page - 1) * size).limit(size).all():
        item = activity_brief(db, a)
        item["waitlist_count"] = sum(len(logic.waitlist_rows(db, g.id))
                                     for g in a.shifts)
        out.append(item)
    return {"items": out, "total": total, "page": page, "size": size}


class ActivityBody(BaseModel):
    title: str = Field(max_length=128)
    organizer: str = Field(default="", max_length=64)
    recruit_mode: str = "instant"
    intro: str = ""
    location: str = ""
    contact_info: str = ""
    contacts: list[dict] = []
    contact_group_qr: str = ""
    cover_hue: int = 0
    # 都选填：整体要求（着装、集合）与保障（餐食、时长、纪念品）
    volunteer_requirement: str = ""
    volunteer_benefit: str = ""
    # 招募范围：限定校区/学段，空 = 不限
    recruit_campuses: list[str] = []
    recruit_stages: list[str] = []
    reg_start: str | None = None
    reg_end: str | None = None


def _clean_scope(raw: list[str], allowed: list[str], label: str) -> list[str]:
    """校验并去重一组条件值（校区/学段）。

    必须落在允许集合里：这些值是拿去跟志愿者档案里的字段逐字比对的，
    放一个错别字进来，条件永远匹配不上，所有人都被挡在外面且看不出为什么。
    """
    out = []
    for v in raw or []:
        v = str(v).strip()
        if not v:
            continue
        if v not in allowed:
            raise HTTPException(400, f"{label}「{v}」不在可选范围内")
        if v not in out:
            out.append(v)
    return out


def _clean_requirement_tags(raw: dict) -> dict:
    """场次/岗位的条件要求。只认识的标签留下，值按各自的可选集合校验。"""
    known = {"校区": config.CAMPUSES, "学段": config.STAGES,
             "学院": config.COLLEGES, "性别": ["男", "女"]}
    out = {}
    for tag, value in (raw or {}).items():
        if tag not in known:
            raise HTTPException(400, f"未知的条件标签「{tag}」")
        vals = value if isinstance(value, list) else [value]
        vals = _clean_scope(vals, known[tag], tag)
        if vals:
            out[tag] = vals
    return out


def _clean_contacts(raw: list[dict]) -> list[dict]:
    """清洗联系人名单：至少一位，且每位的姓名与电话均非空。"""
    contacts = []
    for c in raw or []:
        name = str(c.get("name") or "").strip() if isinstance(c, dict) else ""
        phone = str(c.get("phone") or "").strip() if isinstance(c, dict) else ""
        if not name:
            raise HTTPException(400, "请填写每位联系人的姓名")
        if not phone:
            raise HTTPException(400, f"请填写联系人「{name}」的电话/微信")
        contacts.append({"name": name, "phone": phone})
    if not contacts:
        raise HTTPException(400, "请至少添加一位联系人（姓名+电话）")
    return contacts


def ensure_unique_title(db: OrmSession, title: str, *,
                        exclude_id: str = "") -> str:
    """活动名全平台唯一（草稿也算）。

    重名的活动在广场、名单、导出文件名、留痕摘要里全都分不清是哪一场，
    志愿者更是没法判断自己报的是哪个。
    """
    name = title.strip()
    q = db.query(Activity.id).filter(Activity.title == name)
    if exclude_id:
        q = q.filter(Activity.id != exclude_id)
    if q.first():
        raise HTTPException(409, f"已有同名活动「{name}」，请换一个名字")
    return name


def unique_copy_title(db: OrmSession, title: str) -> str:
    """复制用：撞名就往后加序号，不打断操作。"""
    name = title.strip()
    if not db.query(Activity.id).filter(Activity.title == name).first():
        return name
    for i in range(2, 100):
        candidate = f"{name} {i}"
        if not db.query(Activity.id).filter(Activity.title == candidate).first():
            return candidate
    raise HTTPException(409, f"已有太多同名活动「{name}」，请自己起个名字")


@router.post("/activities")
def create_activity(body: ActivityBody, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    # 创建活动信息完整性校验：全部必填
    if not body.title.strip():
        raise HTTPException(400, "请填写活动名称")
    ensure_unique_title(db, body.title)
    if not body.organizer.strip():
        raise HTTPException(400, "请填写组织单位")
    if body.recruit_mode not in config.RECRUIT_MODES:
        raise HTTPException(400, "请选择活动类型")
    if not body.location.strip():
        raise HTTPException(400, "请填写活动地点")
    contacts = _clean_contacts(body.contacts)
    if not body.intro.strip():
        raise HTTPException(400, "请填写活动简介")
    if not body.contact_group_qr.strip():
        raise HTTPException(400, "请上传志愿者联络群二维码")
    reg_start, reg_end = _parse_dt(body.reg_start), _parse_dt(body.reg_end)
    if reg_start is None or reg_end is None:
        raise HTTPException(400, "请选择报名开始与截止时间")
    act = Activity(id=uid("act"), title=body.title.strip(),
                   recruit_mode=body.recruit_mode,
                   organizer=body.organizer.strip(),
                   intro=body.intro.strip(), location=body.location.strip(),
                   contact_info=body.contact_info.strip(),
                   contact_group_qr=body.contact_group_qr.strip(),
                   cover_hue=body.cover_hue,
                   volunteer_requirement=body.volunteer_requirement.strip(),
                   volunteer_benefit=body.volunteer_benefit.strip(),
                   recruit_campuses=_clean_scope(body.recruit_campuses,
                                                 config.CAMPUSES, "校区"),
                   recruit_stages=_clean_scope(body.recruit_stages,
                                               config.STAGES, "学段"),
                   reg_start=reg_start, reg_end=reg_end,
                   # 归口部门：青协的人建的活动归自己部门，部长从草稿期就看得到；
                   # 会长团与发起方建的留空，提交审批时再选
                   approval_dept_id=(user.department_id
                                     if user.has_role("qingxie") else None),
                   created_by=user.id, status="draft")
    act.contacts = contacts
    db.add(act)
    db.flush()
    # 创建者自动成为本活动管理者（多人共管的基础，见 perm.add_manager 同款逻辑）
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=user.id, added_by=user.id))
    audit_log(db, actor=user, action="create", target_type="activity",
              target_id=act.id, target_label=act.title,
              activity_id=act.id, summary=f"创建活动「{act.title}」")
    db.commit()
    return {"id": act.id}


@router.patch("/activities/{activity_id}")
def update_activity(activity_id: str, body: dict, bg: BackgroundTasks,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_not_finished(act)
    before = {k: getattr(act, k) for k in ACTIVITY_FIELDS}
    for key in ACTIVITY_FIELDS:
        if key not in body:
            continue
        value = body[key]
        if key == "title" and str(value).strip() != act.title:
            ensure_unique_title(db, str(value), exclude_id=act.id)
        if key in ("reg_start", "reg_end"):
            setattr(act, key, _parse_dt(value))
        elif key == "contacts":
            act.contacts = _clean_contacts(value)
        elif key == "recruit_campuses":
            act.recruit_campuses = _clean_scope(value, config.CAMPUSES, "校区")
        elif key == "recruit_stages":
            act.recruit_stages = _clean_scope(value, config.STAGES, "学段")
        elif key == "recruit_mode":
            _apply_recruit_mode(db, act, value)
        elif key == "contact_group_qr":
            if not str(value).strip():
                raise HTTPException(400, "志愿者联络群二维码不能为空")
            act.contact_group_qr = str(value).strip()
        else:
            setattr(act, key, value)
    if "reg_end" in body:
        ensure_reg_end_in_range(act)
    if ("recruit_campuses" in body or "recruit_stages" in body) \
            and not body.get("keep_outsiders"):
        _refuse_if_enrolled_outside_scope(db, act)
    changes = diff_changes(
        {k: (iso(v) if isinstance(v, datetime) else v) for k, v in before.items()},
        {k: (iso(getattr(act, k)) if isinstance(getattr(act, k), datetime)
             else getattr(act, k)) for k in ACTIVITY_FIELDS},
        ACTIVITY_FIELDS)
    if not changes:
        return {"ok": True, "changes": []}

    # 活动变更警告（带前后对比摘要），对已报名志愿者推送 —— 《设计方案》四.3
    if act.status != "draft":
        summary = "；".join(f"{c['label']}：{c['old']}→{c['new']}"
                            for c in changes[:6])
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title, change_summary=summary)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"修改活动信息：{'、'.join(c['label'] for c in changes)}",
              changes=changes,
              visible=True)
    db.commit()
    return {"ok": True, "changes": changes}


def enrolled_outside_scope(db: OrmSession, act: Activity) -> list[dict]:
    """已报名、候补、待确认或已申请，但落在（新的）招募范围之外的人。"""
    ids = {r[0] for r in db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status.in_(("enrolled", "waitlisted",
                                          "pending_confirm", "applied"))).all()}
    ids |= {r[0] for r in db.query(Application.user_id)
            .filter(Application.activity_id == act.id,
                    Application.status.in_(("applied", "accepted"))).all()}
    out = []
    for u in db.query(User).filter(User.id.in_(ids)).order_by(User.name).all():
        unmet = logic.unmet_recruit_scope(u, act)
        if unmet:
            out.append({"name": u.name, "student_id": u.student_id,
                        "reason": "；".join(unmet)})
    return out


def _refuse_if_enrolled_outside_scope(db: OrmSession, act: Activity) -> None:
    """缩小招募范围时，已经报上名的人不能悄悄变成「不该在名单上的人」。

    不自动退任何人：拦下来把名单交给管理者，要么先去名单页处理，
    要么明确说「这些人保留」再改。
    """
    outsiders = enrolled_outside_scope(db, act)
    if outsiders:
        raise HTTPException(409, {
            "code": "OUTSIDERS_ENROLLED",
            "message": f"有 {len(outsiders)} 位已报名的志愿者不在新的招募范围内",
            "outsiders": outsiders})


def _apply_recruit_mode(db: OrmSession, act: Activity, value: str) -> None:
    """改活动类型。公开之后不给改：即选即得已经有人占了岗位，报名后筛选
    已经有人填了表，两边的账互相对不上。"""
    if value not in config.RECRUIT_MODES:
        raise HTTPException(400, "请选择活动类型")
    if value == act.recruit_mode:
        return
    if act.status not in ("draft", "pending", "approved"):
        raise HTTPException(409, "活动已公开，不能再改活动类型")
    act.recruit_mode = value


def _notify_activity_bg(activity_id: str, template: str, **kw):
    """后台任务入口：自带数据库会话，避免拖慢接口响应。

    变更通知要遍历全部报名者逐个落库，人多时耗时数秒——放后台，
    接口先返回，前端按钮立刻恢复。
    """
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        act = db.get(Activity, activity_id)
        if act is not None:
            notify_enrolled(db, act, template, **kw)
            db.commit()
    finally:
        db.close()


# ======================= 编辑冻结 =======================

@router.post("/activities/{activity_id}/edit-lock")
def start_editing(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """进入编辑态：本活动全部场次暂停报名与退选。

    冻结整个活动而不是单个场次——批量复制岗位、调名额这类改动本来就跨场次，
    而且同一活动里「有的能报有的不能报」学生只会更困惑。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_not_finished(act)
    if act.status == "draft":
        raise HTTPException(409, "筹备中的活动可直接编辑，无需暂停报名")
    if act.editing_since is not None:
        who = db.get(User, act.editing_by)
        raise HTTPException(409, f"{who.name if who else '其他管理者'} "
                                 f"正在编辑本活动，请等对方保存后再试")
    act.editing_since = now()
    act.editing_by = user.id
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="进入编辑态，暂停报名")
    db.commit()
    return {"ok": True, "editing": True,
            "auto_release_hours": config.EDIT_LOCK_HOURS}


@router.post("/activities/{activity_id}/publish-changes")
def finish_editing(activity_id: str, bg: BackgroundTasks,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """保存并发布：解冻，并把这段时间的改动汇总推给已报名的志愿者。

    改动里常有扩容——加名额、加岗位。名额空出来就该轮到候补，否则队伍
    一直排着、位子一直空着，得等下一个人退选才动。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.editing_since is None:
        raise HTTPException(409, "本活动不在编辑态")
    since = act.editing_since
    act.editing_since = None
    act.editing_by = None

    rows = (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id,
                    AuditLog.created_at >= since,
                    AuditLog.visible.is_(True))
            .order_by(AuditLog.created_at).all())
    summary = "；".join(r.summary for r in rows[:8]) or "无实质改动"
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"结束编辑并发布改动：{summary}")
    db.commit()
    promoted = _fill_vacancies(db, act)
    if rows and act.status != "draft":
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title, change_summary=summary)
    return {"ok": True, "editing": False, "changes": len(rows),
            "promoted": promoted, "summary": summary}


def _fill_vacancies(db: OrmSession, act: Activity) -> int:
    """活动下所有场次各自补齐名额，返回通知了多少位候补去确认。"""
    total = 0
    for g in act.shifts:
        if g.is_attached():
            continue      # 培训/彩排跟着主场次走，没有自己的队列
        with shift_lock(g.id):
            total += logic.fill_vacancies(db, g)
            db.commit()
    return total


# ======================= 复制 =======================

class CopyActivityBody(BaseModel):
    title: str = ""
    shift_days: int = 7      # 所有时间整体平移多少天
    contact_group_qr: str = ""   # 新活动新群，复制时就要传新图


@router.post("/activities/{activity_id}/copy")
def copy_activity_route(activity_id: str, body: CopyActivityBody,
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """复制一个活动的结构。报名、签到、时长、留痕都不带过来。"""
    ensure_activity_manager(db, user, activity_id)
    src = _act(db, activity_id)
    if body.title.strip():
        title = ensure_unique_title(db, body.title)     # 自己起的名字就得是新的
    else:
        title = unique_copy_title(db, f"{src.title}（副本）")
    if len(title) > 128:
        raise HTTPException(400, "活动名称过长")
    if not body.contact_group_qr.strip():
        raise HTTPException(400, "请上传新活动的志愿者联络群二维码")
    act = copying.copy_activity(db, src, user, title=title,
                                shift_days=body.shift_days,
                                contact_group_qr=body.contact_group_qr.strip())
    audit_log(db, actor=user, action="copy", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"复制自「{src.title}」（{copying.describe(src)}），"
                      f"时间整体平移 {body.shift_days} 天")
    db.commit()
    return {"id": act.id, "title": act.title}


class CopyPositionsBody(BaseModel):
    position_ids: list[str] = []      # 空 = 该场次下全部岗位
    target_shift_ids: list[str]


@router.post("/shifts/{shift_id}/positions/copy")
def copy_positions_route(shift_id: str, body: CopyPositionsBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    """把本场次的岗位批量复制到同一活动的其它场次。"""
    src = _shift(db, shift_id)
    ensure_activity_manager(db, user, src.activity_id)
    _ensure_editing(db, src.activity_id)
    sources = sorted(src.positions, key=lambda p: p.sort)
    if body.position_ids:
        wanted = set(body.position_ids)
        sources = [p for p in sources if p.id in wanted]
    if not sources:
        raise HTTPException(400, "该场次下没有可复制的岗位")

    targets = []
    for tid in body.target_shift_ids:
        target = db.get(Shift, tid)
        if target is None or target.activity_id != src.activity_id:
            raise HTTPException(400, "目标场次不属于本活动")
        _ensure_pick_mode_for_positions(target)
        if target.id == src.id:
            continue
        targets.append(target)
    if not targets:
        raise HTTPException(400, "请选择至少一个目标场次")

    result = copying.copy_positions_to(db, sources, targets, user)
    audit_log(db, actor=user, action="copy", target_type="position",
              target_id=src.id, target_label=src.name,
              activity_id=src.activity_id,
              summary=f"从「{src.name}」复制 {len(sources)} 个岗位到 "
                      f"{len(targets)} 个场次，新建 {result['copied']} 个"
                      + (f"；同名跳过 {len(result['skipped'])} 个"
                         if result["skipped"] else ""))
    db.commit()
    return result


# ======================= 状态流转 =======================

@router.post("/activities/{activity_id}/publish")
def publish(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    """手动发布。只有提交审批时关掉了「通过即发布」的活动才会走到这里。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    # 所有活动发布前都要过校青协：草稿要先提交审批，通过后才轮到这一步
    if act.status == "draft":
        raise HTTPException(409, "请先提交校青协审批，通过后再发布")
    if act.status != "approved":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能发布")
    publish_approved(db, act, user)
    db.commit()
    return {"ok": True}


def publish_approved(db: OrmSession, act: Activity, user: User) -> None:
    """把审批通过的活动放到广场上。审批人拍板时自动走这里，
    关掉了「通过即发布」的则由发起方点按钮走这里。不提交事务，交给调用方。"""
    ensure_ready_to_publish(act)
    act.status = "published"
    if act.reg_start is None:
        act.reg_start = now()
    audit_log(db, actor=user, action="publish", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="发布活动，开始报名")


def ensure_ready_to_publish(act: Activity) -> None:
    """结构上够不够格公开。提交审批与发布都走这一道——审批期间发起方还能
    继续改，真上线的那一刻不能带着窟窿出去。"""
    if not act.contact_group_qr:
        raise HTTPException(400, "请先在基本信息里上传志愿者联络群二维码")
    if not act.main_shifts():
        raise HTTPException(400, "请先至少创建一个场次")
    # 「报名即选岗位」的场次没有岗位就等于没有名额，发出去学生一个都报不了
    empty = [g.name for g in act.main_shifts()
             if not g.needs_assign() and not g.positions]
    if empty:
        raise HTTPException(
            400, f"场次「{'、'.join(empty)}」还没有岗位，学生无法报名。"
                 f"请先添加岗位，或把这些场次改为「截止后统一分配」")
    ensure_reg_end_in_range(act)


def ensure_reg_end_in_range(act: Activity) -> None:
    """报名截止不能晚于最后一个场次的开始时间。

    晚于它的那一段是死窗口：每个场次在自己开场时就停止收人，最后一场都开始了，
    活动却还挂着「报名中」，学生点进去每一场都是灰的。
    """
    last = act.last_shift_start()
    if act.reg_end and last and act.reg_end > last:
        raise HTTPException(
            400, f"报名截止时间（{act.reg_end:%m-%d %H:%M}）晚于最后一个场次的"
                 f"开始时间（{last:%m-%d %H:%M}），请将报名截止时间提前，"
                 f"或将场次时间后移")


def _transition(activity_id: str, target: str, action: str, summary: str,
                allowed_from: tuple[str, ...], db: OrmSession, user: User,
                bg: BackgroundTasks):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status not in allowed_from:
        raise HTTPException(409,
                            f"当前状态为「{act.label_status()}」，不允许该操作")
    act.status = target
    audit_log(db, actor=user, action=action, target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)
    db.commit()
    if action == "finish":
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title,
                    change_summary="活动已结束，感谢您的参与。时长将陆续录入。")
    return {"ok": True}


@router.post("/activities/{activity_id}/close")
def close(activity_id: str, bg: BackgroundTasks,
          db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    out = _transition(activity_id, "closed", "close", "截止报名",
                      ("published",), db, user, bg)
    # 手动截止和系统自动截止是同一件事，排岗提醒不能只挂在定时任务上
    from ..scheduled import notify_managers_to_assign
    notify_managers_to_assign(db, _act(db, activity_id))
    db.commit()
    return out


class PinBody(BaseModel):
    pinned: bool


@router.post("/activities/{activity_id}/pin")
def pin(activity_id: str, body: PinBody,
        db: OrmSession = Depends(db_session),
        user: User = Depends(require_council)):
    """置顶（急招）/ 取消置顶。只有会长团和管理员能点：急招是全校层面的
    调度，不是某个活动自己说了算。只对报名中的活动有意义。"""
    act = _act(db, activity_id)
    if body.pinned and act.status != "published":
        raise HTTPException(409, "只有报名中的活动才能置顶为急招")
    act.pinned_at = now() if body.pinned else None
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="置顶为急招" if body.pinned else "取消急招置顶")
    db.commit()
    return {"ok": True, "pinned": act.pinned_at is not None}


class ReopenBody(BaseModel):
    reg_end: str          # 新的报名截止时间，必须晚于此刻


@router.post("/activities/{activity_id}/reopen")
def reopen(activity_id: str, body: ReopenBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """已截止 → 重新开放报名。

    必须重设截止时间：定时任务是按 reg_end 判断的，只把状态改回「报名中」
    而不动 reg_end，下一分钟就会被再截止一次，等于白点。
    最后一个场次也开场之后不给恢复——那时每一场都已经各自关闸，恢复出来
    一个人也报不进来。前面几场开过了不要紧，后面还没开的照样可以补人。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status != "closed":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能恢复报名")
    last = act.last_shift_start()
    t = now()
    if last and t >= last:
        raise HTTPException(409, "所有场次都已开始，无法恢复报名")
    new_end = _parse_dt(body.reg_end)
    if new_end is None:
        raise HTTPException(400, "请选择新的报名截止时间")
    if new_end <= t:
        raise HTTPException(400, "新的报名截止时间必须晚于当前时间")
    if last and new_end > last:
        raise HTTPException(400, f"报名截止时间不能晚于最后一个场次的开始时间"
                                 f"（{last:%m-%d %H:%M}）")
    old_end = act.reg_end
    act.status = "published"
    act.reg_end = new_end
    audit_log(db, actor=user, action="publish", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"恢复报名，报名截止时间 "
                      f"{old_end:%m-%d %H:%M} → {new_end:%m-%d %H:%M}"
                      if old_end else f"恢复报名，报名截止 {new_end:%m-%d %H:%M}",
              visible=True)
    db.commit()
    # 截止期间自动递补是停着的，恢复报名等于名额又能动了，先把队伍消一遍
    promoted = _fill_vacancies(db, act)
    return {"ok": True, "reg_end": iso(act.reg_end), "promoted": promoted}


@router.post("/activities/{activity_id}/finish")
def finish(activity_id: str, bg: BackgroundTasks,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    # 只能从「已截止」结束：报名还开着就宣布结束，等于让正在报名的人
    # 报进一个已经收摊的活动。先截止，再结束。
    return _transition(activity_id, "finished", "finish", "结束活动",
                       ("closed",), db, user, bg)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """仅草稿可删——发布过的活动承载着报名记录与留痕，不允许物理删除。

    场次与岗位有 ORM 级联，其余指向本活动的表要自己清：共管名单、审批流水、
    报名问卷、服装台账、签到任务。少清一张表，删除就撞外键报 500——草稿活动
    身上至少永远挂着一条创建者的共管记录。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status != "draft":
        raise HTTPException(409, "仅「筹备中」的活动可以删除；"
                                 "已发布的活动请使用「结束活动」归档")
    label = act.title
    for model in (ActivityManager, ApprovalEvent, AttireRecord, AttireTask,
                  CheckinTask, Enrollment, FormQuestion):
        (db.query(model).filter(model.activity_id == activity_id)
         .delete(synchronize_session=False))
    db.flush()
    db.delete(act)
    audit_log(db, actor=user, action="delete", target_type="activity",
              target_id=activity_id, target_label=label, activity_id=None,
              summary=f"删除草稿活动「{label}」")
    db.commit()
    return {"ok": True}


# ======================= 一级场次 =======================

SHIFT_MODES = ("pick", "assign")


SHIFT_KINDS = ("service", "training", "rehearsal")
HOURS_MODES = ("checkin", "fixed", "none")


class ShiftBody(BaseModel):
    name: str = Field(max_length=128)
    start_time: str
    end_time: str
    mode: str = "pick"          # pick 报名即选岗位 / assign 截止后统一分配
    # 只有 assign 模式用得上：报名时还没有岗位可选，名额只能记在场次上
    capacity: int = Field(default=0, ge=0)
    note: str = ""
    location: str = Field(default="", max_length=128)
    waitlist_quota: int = Field(default=3, ge=0)   # 候补队列人数上限
    hours_mode: str = "checkin"
    # —— 培训/彩排（附属场次）——
    kind: str = "service"
    parent_id: str = ""
    applies_to_positions: list[str] = []
    # 场次级硬性条件：{"校区": [...], "学段": [...]}，空 = 不限
    requirement_tags: dict = {}


def _validate_shift_times(start: datetime, end: datetime):
    if end <= start:
        raise HTTPException(400, "结束时间必须晚于开始时间")


def _apply_attachment(db: OrmSession, g: Shift, body: ShiftBody,
                      act: Activity) -> None:
    """把「这是某个服务场次的培训/彩排」这层关系落到场次上。

    附属场次不设名额与候补：来不来由主场次的报名决定，自己不招人。
    """
    if body.hours_mode not in HOURS_MODES:
        raise HTTPException(400, "时长认定方式不合法")
    if body.kind not in SHIFT_KINDS:
        raise HTTPException(400, "场次类型不合法")
    g.hours_mode = body.hours_mode
    g.kind = body.kind
    g.requirement_tags = _clean_requirement_tags(body.requirement_tags)
    if not body.parent_id:
        if body.kind != "service":
            raise HTTPException(400, "培训/彩排必须挂在一个服务场次下")
        g.parent_id = None
        g.applies_to_positions = []
        return
    if body.kind == "service":
        raise HTTPException(400, "附属场次的类型必须是培训或彩排")
    parent = db.get(Shift, body.parent_id)
    if parent is None or parent.activity_id != act.id:
        raise HTTPException(400, "所属服务场次不属于本活动")
    if parent.is_attached():
        raise HTTPException(400, "培训/彩排不能再挂培训/彩排")
    if parent.id == g.id:
        raise HTTPException(400, "场次不能挂在自己下面")
    known = {p.id for p in parent.positions}
    bad = [i for i in body.applies_to_positions if i not in known]
    if bad:
        raise HTTPException(400, "所选岗位不属于该服务场次")
    g.parent_id = parent.id
    g.applies_to_positions = list(body.applies_to_positions)
    g.mode = "pick"          # 附属场次不选岗位，模式对它没有意义


PLACEHOLDER_NAME = "待分配"


def _sync_placeholder(db: OrmSession, g: Shift, capacity: int,
                      waitlist_quota: int) -> None:
    """「截止后统一分配」的场次自动带一个占位岗位，名额记在它头上。

    这样名额只有一个来源（岗位），报名、候补、签到、服装全都不必再为
    「没有岗位的报名」单开一套账。学生看到的是一个写明「暂不选岗位」的
    选项，不是一个真岗位。
    """
    if g.is_attached() or not g.needs_assign():
        return
    if capacity <= 0:
        raise HTTPException(400, "「截止后统一分配」的场次必须填写人数上限")
    holder = g.placeholder()
    if holder is None:
        # 这个场次可能切到过 pick——那时占位岗位转成了普通岗位。没人报的话
        # 直接把它收回来，否则会多出一个同名岗位。
        holder = next((p for p in g.positions
                       if p.name == PLACEHOLDER_NAME
                       and not logic.position_occupied_count(db, p.id)), None)
    if holder is None:
        holder = Position(id=uid("p"), shift_id=g.id, name=PLACEHOLDER_NAME,
                          sort=-1,
                          service_task="报名截止后由负责人统一分配岗位",
                          training_note="岗位确定后另行通知")
        db.add(holder)
        g.positions.append(holder)
    holder.is_placeholder = True
    enrolled = logic.position_enrolled_count(db, holder.id) if holder.id else 0
    if capacity < enrolled:
        raise HTTPException(400, f"人数上限不能小于已报名的 {enrolled} 人")
    holder.quota = capacity
    holder.waitlist_quota = waitlist_quota


def _apply_shift_mode(db: OrmSession, g: Shift, mode: str) -> int:
    """切换场次模式。返回 0，签名保留是因为调用方还在报「影响人数」。

    assign → pick 是正常流程：报名截止后改模式、建岗位、跑一键分配。
    占位岗位就地转成普通岗位：名字还叫「待分配」，管理者一眼认得出这是谁，
    一个场次只有一个岗位时不必再新建一个，一键分配也照常往里排人。
    预置的服务任务与培训内容清空——那两句话是说给「还没分岗位」的人听的。

    pick → assign 只在**一个人都没报**时允许。反向切要作废所有人已经选定的
    岗位，那是一次实打实的减损；与其做一个带二次确认的破坏性操作，不如
    干脆不给——建错模式的场次删了重建就是。
    """
    if mode not in SHIFT_MODES:
        raise HTTPException(400, "场次模式必须是 pick 或 assign")
    if mode == "assign" and g.mode != "assign" and g.id:
        live = (db.query(Enrollment)
                .filter(Enrollment.shift_id == g.id,
                        Enrollment.status.in_(logic.ACTIVE)).count())
        if live:
            raise HTTPException(
                409, f"「{g.name}」已有 {live} 人报名，不能改回"
                     f"「截止后统一分配」，如确需调整请新建场次。")
    if mode == "pick" and g.mode == "assign":
        holder = g.placeholder()
        if holder is not None:
            holder.is_placeholder = False
            holder.service_task = ""
            holder.training_note = ""
    g.mode = mode
    return 0


@router.post("/activities/{activity_id}/shifts")
def create_shift(activity_id: str, body: ShiftBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _ensure_editing(db, activity_id)
    start, end = _parse_dt(body.start_time), _parse_dt(body.end_time)
    _validate_shift_times(start, end)
    g = Shift(id=uid("g"), activity_id=act.id, name=body.name.strip(),
                      start_time=start, end_time=end,
                      note=body.note.strip(), sort=len(act.shifts),
                      location=body.location.strip())
    _apply_shift_mode(db, g, body.mode)
    _apply_attachment(db, g, body, act)
    db.add(g)
    db.flush()
    # 占位岗位要等场次落库拿到 id 之后再建
    _sync_placeholder(db, g, body.capacity, body.waitlist_quota)
    db.flush()
    if g.is_attached():
        parent = db.get(Shift, g.parent_id)
        scope = ("全场次" if not g.applies_to_positions else
                 "、".join(p.name for p in parent.positions
                           if p.id in g.applies_to_positions))
        summary = (f"为「{parent.name}」新增{g.kind_label()}「{g.name}」"
                   f"（{g.span_text()}，{g.location or '地点待定'}，"
                   f"适用 {scope}，{g.hours_label()}）")
    else:
        summary = (f"新建场次「{g.name}」（{g.span_text()}，"
                   f"{g.mode_label()}，上限{body.capacity}人）")
    audit_log(db, actor=user, action="create", target_type="group",
              target_id=g.id, target_label=f"{act.title}·{g.name}",
              activity_id=act.id, summary=summary, visible=True)
    if g.is_attached():
        # 已经报上名的人要连带报进这场培训/彩排，否则他们看不到也签不了到
        for enr in (db.query(Enrollment)
                    .filter(Enrollment.shift_id == g.parent_id,
                            Enrollment.status == "enrolled").all()):
            logic.sync_attached(db, enr)
    db.commit()
    if act.status != "draft":
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title, change_summary=summary)
    return {"id": g.id}


# 名额与候补不在这张表里：它们记在占位岗位上，由 _sync_placeholder 处理
SHIFT_FIELDS = {"name": "场次名", "start_time": "开始时间", "end_time": "结束时间",
                "mode": "岗位模式", "note": "备注",
                "location": "地点", "hours_mode": "时长认定",
                "requirement_tags": "条件要求"}


@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: str, body: dict, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    before_raw = {k: getattr(g, k) for k in SHIFT_FIELDS}
    cleared = 0
    for key in SHIFT_FIELDS:
        if key not in body:
            continue
        v = body[key]
        if key in ("start_time", "end_time"):
            setattr(g, key, _parse_dt(v))
        elif key == "mode":
            cleared = _apply_shift_mode(db, g, v)
        elif key == "hours_mode":
            if v not in HOURS_MODES:
                raise HTTPException(400, "时长认定方式不合法")
            g.hours_mode = v
        elif key == "requirement_tags":
            g.requirement_tags = _clean_requirement_tags(v)
        else:
            setattr(g, key, v)
    if "applies_to_positions" in body and g.is_attached():
        parent = db.get(Shift, g.parent_id)
        known = {p.id for p in parent.positions}
        if [i for i in body["applies_to_positions"] if i not in known]:
            raise HTTPException(400, "所选岗位不属于该服务场次")
        g.applies_to_positions = list(body["applies_to_positions"])
        db.flush()
        for enr in (db.query(Enrollment)
                    .filter(Enrollment.shift_id == g.parent_id,
                            Enrollment.status == "enrolled").all()):
            logic.sync_attached(db, enr)
    _validate_shift_times(g.start_time, g.end_time)
    # 名额与候补落到占位岗位上；没传就沿用它当前的值
    holder = g.placeholder()
    _sync_placeholder(db, g,
                      int(body.get("capacity", holder.quota if holder else 0)),
                      int(body.get("waitlist_quota",
                                   holder.waitlist_quota if holder else 3)))

    def fmt(v):
        if isinstance(v, datetime):
            return v.strftime("%m-%d %H:%M")
        return v
    changes = diff_changes({k: fmt(v) for k, v in before_raw.items()},
                           {k: fmt(getattr(g, k)) for k in SHIFT_FIELDS},
                           SHIFT_FIELDS)
    time_or_place = any(c["field"] in ("start_time", "end_time", "name", "note")
                        for c in changes)
    if changes and act.status != "draft" and time_or_place:
        summary = "；".join(f"{c['label']}：{c['old']}→{c['new']}" for c in changes)
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title,
                    change_summary=f"{g.name} 场次调整：{summary}")
    audit_log(db, actor=user, action="update", target_type="group",
              target_id=g.id, target_label=f"{act.title}·{g.name}",
              activity_id=act.id,
              summary=f"修改场次「{g.name}」："
                      f"{'、'.join(c['label'] for c in changes) or '无实质变化'}"
                      + (f"；已清空 {cleared} 人的岗位，改由截止后统一分配"
                         if cleared else ""),
              changes=changes,
              visible=True)
    db.commit()
    return {"ok": True, "changes": changes, "cleared": cleared}


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: str, force: int = 0, bg: BackgroundTasks = None,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    # 附属的培训/彩排跟着主场次走：它们的报名本来就是主场次带出来的
    children = db.query(Shift).filter(Shift.parent_id == g.id).all()
    n_active = db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status.in_(logic.ACTIVE)).count()
    if n_active and not force:
        raise HTTPException(409, f"该场次已有 {n_active} 人有效报名，"
                                 f"删除需强制执行（force=1），全部记录将被取消")
    for child in children:
        (db.query(Enrollment).filter(Enrollment.shift_id == child.id)
         .delete(synchronize_session=False))
        db.delete(child)
    db.flush()
    if n_active and force:
        # 场次没了，扣留的占位名额也一并失效——这不是志愿者的责任
        (db.query(Enrollment).filter(Enrollment.shift_id == g.id)
         .update({"status": "cancelled", "cancel_type": "shift_deleted",
                  "seat_held": False},
                 synchronize_session=False))
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title,
                    change_summary=f"场次「{g.name}」已被取消，相关报名记录失效")
    label = f"{act.title}·{g.name}"
    db.delete(g)
    audit_log(db, actor=user, action="delete", target_type="group",
              target_id=g.id, target_label=label, activity_id=act.id,
              summary=f"删除场次「{g.name}」" +
                      (f"（强制，含{n_active}条报名记录）" if n_active else "") +
                      (f"；同时删除 {len(children)} 个附属的培训/彩排"
                       if children else ""),
              visible=True)
    db.commit()
    return {"ok": True}


# ======================= 岗位 =======================

class PositionBody(BaseModel):
    """岗位没有时间——时间随场次。名额、候补、条件、组长都在这一层。"""
    name: str = Field(max_length=128)
    location: str = ""
    quota: int = Field(gt=0)
    waitlist_quota: int = Field(default=3, ge=0)
    group_name: str = ""
    requirement_tags: dict = {}
    service_task: str = ""
    position_requirement: str = ""       # 选填
    training_note: str = ""
    needs_leader: bool = False
    attire: str = "none"                 # none / issued / own
    attire_note: str = ""                # 统一发放或自备时必填
    pay_optional: bool = False           # 可选劳务
    pay_note: str = ""                   # 可选劳务时必填：标准与发放方式
    copy_from: str = ""                  # 复制自哪个岗位：学习材料跟着带过来


POSITION_FIELDS = {"name": "岗位名", "location": "地点", "quota": "名额",
                   "group_name": "岗位组",
                   "waitlist_quota": "候补名额", "needs_leader": "需要组长",
                   "attire": "志愿服装", "attire_note": "服装备注",
                   "pay_optional": "可选劳务", "pay_note": "劳务备注",
                   "requirement_tags": "条件要求",
                   "service_task": "服务任务", "position_requirement": "岗位要求",
                   "training_note": "培训内容"}


def _ensure_editing(db: OrmSession, activity_id: str) -> Activity:
    """改场次/岗位之前，活动必须处于编辑态。

    筹备中的活动随便改——还没公开，没人报名。已发布之后每一次改动都可能
    让学生看到半成品：名额改到一半、岗位删了又建。所以强制走
    「调整岗位 → 改 → 保存并发布」这条路，改动期间报名暂停，
    保存时把这批改动一次性通知到人。
    """
    act = _act(db, activity_id)
    ensure_not_finished(act)
    if act.status != "draft" and act.editing_since is None:
        raise HTTPException(
            409, "请先点击右上角「调整岗位（暂停报名）」，再修改场次与岗位")
    return act


def _ensure_pick_mode_for_positions(g: Shift) -> None:
    """加岗位前必须是「报名即选岗位」模式。

    这是两种场次的分界线：assign 场次报名时只锁时段，名额记在场次上，
    岗位是报名截止后才建的；留在 assign 模式下建岗位，名额会同时挂在
    两层，谁也说不清以哪个为准。前端在点「添加岗位」时就会提示并给出
    一键切换，不会让人填完一整个表单才被拦下。
    """
    if g.needs_assign():
        raise HTTPException(
            409, f"场次「{g.name}」当前是「截止后统一分配」，请先在场次设置中"
                 f"改为「报名即选岗位」，再添加岗位")


def _require_position_text(s: Position) -> None:
    """服务任务是志愿者到岗前唯一必看的一句话；地点留空用活动地点，
    培训内容可以之后再补。"""
    if not (s.service_task or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的服务任务")
    if s.attire not in Position.ATTIRE_LABELS:
        raise HTTPException(400, "志愿服装只能是 不需要 / 统一发放 / 志愿者自备")
    if s.attire != "none" and not (s.attire_note or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的服装备注"
                                 "（领取与归还方式，或自备的样式要求）")
    if s.pay_optional and not (s.pay_note or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的劳务备注"
                                 "（标准与发放方式）")


def _validate_position(db: OrmSession, g: Shift, s: Position):
    """岗位自带名额，不再受场次总数约束——场次只管时间。

    assign 模式例外：切过模式的场次可能留着一批岗位，那时名额记在场次上，
    各岗位之和不能超过它，否则一键分配会分出比报名人数更多的坑位。
    """
    if s.quota <= 0:
        raise HTTPException(400, "岗位名额必须大于 0")
    # 同上：只跟已报名的真人比。扣留的空位不该挡住与名额无关的修改
    enrolled = logic.position_enrolled_count(db, s.id) if s.id else 0
    if s.quota < enrolled:
        raise HTTPException(400, f"岗位名额不能小于已报名的 {enrolled} 人")
    if g.needs_assign():
        quota_sum = sum(x.quota for x in g.positions if x.id != s.id) + s.quota
        if quota_sum > g.capacity:
            raise HTTPException(
                400, f"各岗位名额之和({quota_sum})超过场次人数上限({g.capacity})")


@router.post("/shifts/{shift_id}/positions")
def create_position(shift_id: str, body: PositionBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    _ensure_pick_mode_for_positions(g)
    source = db.get(Position, body.copy_from) if body.copy_from else None
    if source is not None and source.shift.activity_id != g.activity_id:
        raise HTTPException(400, "只能复制本活动的岗位")
    s = Position(id=uid("s"), shift_id=g.id, name=body.name.strip(),
                location=body.location.strip(), quota=body.quota,
                waitlist_quota=body.waitlist_quota,
                needs_leader=body.needs_leader,
                attire=body.attire, attire_note=body.attire_note.strip(),
                pay_optional=body.pay_optional, pay_note=body.pay_note.strip(),
                group_name=body.group_name.strip(),
                requirement_tags=_clean_requirement_tags(body.requirement_tags),
                service_task=body.service_task.strip(),
                position_requirement=body.position_requirement.strip(),
                training_note=body.training_note.strip(),
                sort=len(g.positions))
    _validate_position(db, g, s)
    _require_position_text(s)
    db.add(s)
    db.flush()   # 让 group 关系可用，供 time_text() 使用
    copied = (copying.copy_attachments(db, "position", source.id, s.id,
                                       act.id, user) if source else 0)
    audit_log(db, actor=user, action="create", target_type="position",
              target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
              activity_id=act.id,
              summary=f"新建岗位「{s.name}」（{s.time_text()}，"
                      f"{s.location or act.location or '地点待定'}，{s.quota}人）"
                      + (f"，复制自「{source.name}」"
                         + (f"（含 {copied} 份学习材料）" if copied else "")
                         if source else ""),
              visible=True)
    db.commit()
    return {"id": s.id}


@router.patch("/positions/{position_id}")
def update_position(position_id: str, body: dict, bg: BackgroundTasks,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _shift(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    before_raw = {k: getattr(s, k) for k in POSITION_FIELDS}
    for key in POSITION_FIELDS:
        if key not in body:
            continue
        if key == "requirement_tags":
            s.requirement_tags = _clean_requirement_tags(body[key])
        else:
            setattr(s, key, body[key])
    _validate_position(db, g, s)
    _require_position_text(s)

    # 组长开关变化要落到人身上：关掉了还留着组长标记，导出表和大屏就会
    # 显示一个「不存在的岗位角色」；打开了则提醒管理者去指定一位。
    if s.needs_leader != before_raw["needs_leader"]:
        if s.needs_leader:
            db.flush()
            logic.notify_leader_vacancy(db, s)
        else:
            (db.query(Enrollment)
             .filter(Enrollment.position_id == s.id,
                     Enrollment.is_leader.is_(True))
             .update({"is_leader": False}, synchronize_session=False))

    def fmt(k, v):
        if isinstance(v, datetime):
            return v.strftime("%m-%d %H:%M")
        if k == "attire":
            return Position.ATTIRE_LABELS.get(v, v)
        return v
    changes = diff_changes({k: fmt(k, v) for k, v in before_raw.items()},
                           {k: fmt(k, getattr(s, k)) for k in POSITION_FIELDS},
                           POSITION_FIELDS)
    if changes and act.status != "draft":
        affected = (db.query(Enrollment.user_id)
                    .filter(Enrollment.position_id == s.id,
                            Enrollment.status == "enrolled").all())
        for (uid_,) in affected:
            bg.add_task(_notify_assignment_bg,
                        [{"user_id": uid_, "position_id": s.id,
                          "shift_id": g.id}])
        summary = "；".join(f"{c['label']}：{c['old']}→{c['new']}"
                            for c in changes if c["field"] != "requirement_tags")
        if summary:
            bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                        activity=act.title,
                        change_summary=f"岗位「{s.name}」调整：{summary}")
    audit_log(db, actor=user, action="update", target_type="position",
              target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
              activity_id=act.id,
              summary=f"修改岗位「{s.name}」："
                      f"{'、'.join(c['label'] for c in changes) or '无实质变化'}",
              changes=changes,
              visible=True)
    db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/positions/{position_id}")
def delete_position(position_id: str,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _shift(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    n = db.query(Enrollment).filter(Enrollment.position_id == s.id,
                                    Enrollment.status == "enrolled").count()
    if n:
        raise HTTPException(409, f"该岗位仍有 {n} 人已报名，请先在分配面板调整")
    label = f"{act.title}·{g.name}·{s.name}"
    db.delete(s)
    audit_log(db, actor=user, action="delete", target_type="position",
              target_id=s.id, target_label=label, activity_id=act.id,
              summary=f"删除岗位「{s.name}」",
              visible=True)
    db.commit()
    return {"ok": True}


# ======================= 活动管理者 =======================

@router.get("/activities/{activity_id}/managers")
def list_managers(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(ActivityManager)
            .filter(ActivityManager.activity_id == activity_id).all())
    duties = {r.user_id: r.duties or [] for r in rows}
    users = [db.get(User, i) for i in activity_manager_ids(db, activity_id)]
    return {"duties": ActivityManager.DUTIES, "items": [{
        "id": u.id, "name": u.name, "student_id": u.student_id,
        "college": u.college, "roles": u.roles or [],
        "role_label": u.active_role_label(),
        "post_label": u.post_label() if u.post() else "",
        # 部门成员在名单里只能看：界面上要标出来，别让人以为多了个能干活的
        "is_trainee": u.is_trainee(),
        "is_creator": u.id == act.created_by,
        # 管理员天然是管理者，名单里没有他那一行，也就没有分工可言
        "duties": duties.get(u.id, []),
    } for u in users if u]}


class ManagerBody(BaseModel):
    student_id: str


class DutiesBody(BaseModel):
    duties: list[str]


@router.post("/activities/{activity_id}/managers")
def add_activity_manager(activity_id: str, body: ManagerBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    target = db.query(User).filter(
        User.student_id == body.student_id.strip()).first()
    if target is None:
        raise HTTPException(404, f"学号 {body.student_id} 不存在")
    row = add_manager(db, activity_id, target, user)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"添加活动管理者：{target.name}（{target.student_id}）")
    db.commit()
    return {"ok": True, "id": row.id}


@router.patch("/activities/{activity_id}/managers/{user_id}/duties")
def set_manager_duties(activity_id: str, user_id: str, body: DutiesBody,
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """给某位管理者分工。只影响通知发给谁，不限制他能做什么。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    row = (db.query(ActivityManager)
           .filter(ActivityManager.activity_id == activity_id,
                   ActivityManager.user_id == user_id).first())
    if row is None:
        raise HTTPException(404, "该用户不在本活动的管理者名单中")
    unknown = [d for d in body.duties if d not in ActivityManager.DUTIES]
    if unknown:
        raise HTTPException(400, f"未知的分工：{'、'.join(unknown)}")
    row.duties = list(dict.fromkeys(body.duties))
    target = db.get(User, user_id)
    labels = "、".join(ActivityManager.DUTIES[d] for d in row.duties)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"{target.name if target else user_id} 的分工改为"
                      f"{labels or '全部通知'}")
    db.commit()
    return {"ok": True, "duties": row.duties}


@router.delete("/activities/{activity_id}/managers/{user_id}")
def remove_activity_manager(activity_id: str, user_id: str,
                            db: OrmSession = Depends(db_session),
                            user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if user_id == user.id:
        raise HTTPException(400, "不能移除自己的管理权限；"
                                 "如需退出管理，请由其他管理者操作")
    target = db.get(User, user_id)
    remove_manager(db, activity_id, user_id, act.created_by)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"移除活动管理者：{target.name if target else user_id}")
    db.commit()
    return {"ok": True}


# ======================= 志愿者通知 =======================

class NotifyBody(BaseModel):
    scope: str = "all"      # all 全部志愿者 / group 指定场次 / not_checked_in 场次未到岗
    shift_id: str = ""
    title: str = Field(max_length=128)
    content: str
    need_ack: bool = False  # 要求志愿者点「我已知悉」


@router.post("/activities/{activity_id}/notify")
def notify_volunteers(activity_id: str, body: NotifyBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """向该活动 status=enrolled 的报名者发送自定义通知。

    scope=group：再按 shift_id 过滤；scope=not_checked_in：按 shift_id 过滤
    且排除已在该场次有签到记录的人。Mock 模式下消息落 notifications 表。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    title, content = body.title.strip(), body.content.strip()
    if not title:
        raise HTTPException(400, "请填写通知标题")
    if not content:
        raise HTTPException(400, "请填写通知内容")
    if body.scope not in ("all", "group", "not_checked_in"):
        raise HTTPException(400, "通知范围不合法")
    shift_id = ""
    if body.scope != "all":
        if not body.shift_id:
            raise HTTPException(400, "请选择场次")
        g = _shift(db, body.shift_id)
        if g.activity_id != act.id:
            raise HTTPException(400, "所选场次不属于该活动")
        shift_id = g.id
    # 两步查询：先 DISTINCT user_id（JSON 列在 PG 上无相等运算符，不能整行去重）
    ids = (db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status == "enrolled"))
    if shift_id:
        ids = ids.filter(Enrollment.shift_id == shift_id)
    ids = [r[0] for r in ids.distinct().all()]
    users = [db.get(User, i) for i in ids]
    users = [u for u in users if u]
    if body.scope == "not_checked_in":
        checked = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == shift_id).all()}
        users = [u for u in users if u.id not in checked]
    batch_id = uid("bat") if body.need_ack else None
    for u in users:
        wecom.send_user(db, u, "custom", title=title, content=content,
                        data={"activity_id": act.id},
                        need_ack=body.need_ack, batch_id=batch_id)
    n = len(users)
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=act.id, target_label=act.title,
              activity_id=act.id,
              summary=f"向{n}位志愿者发送通知：{title}"
                      + ("（要求签收）" if body.need_ack else ""))
    db.commit()
    return {"ok": True, "count": n, "batch_id": batch_id}


@router.get("/activities/{activity_id}/notify-batches")
def notify_batches(activity_id: str, db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """需要签收的群发批次及其签收情况。

    催签收的通知（ack_reminder）不进人数口径，只累计催办次数——
    催一次就多一条待签收的话，5 个人的批次催一轮会显示成 10 条。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    # 该活动报名者收到的、要求签收的通知，按批次聚合
    member_ids = [r[0] for r in db.query(Enrollment.user_id)
                  .filter(Enrollment.activity_id == act.id).distinct().all()]
    if not member_ids:
        return []
    rows = (db.query(Notification)
            .filter(Notification.batch_id.isnot(None),
                    (Notification.need_ack.is_(True))
                    | (Notification.template_key == "ack_reminder"),
                    Notification.user_id.in_(member_ids))
            .order_by(Notification.created_at.desc()).all())
    batches: dict[str, dict] = {}
    for n in rows:
        # 志愿者往往同时报了好几个活动，只认属于本活动的那些批次，
        # 否则这里会把别的活动的未签收名单（姓名+学号）摊给本活动管理者
        if str((n.data or {}).get("activity_id") or "") != act.id:
            continue
        b = batches.setdefault(n.batch_id, {
            "batch_id": n.batch_id, "title": n.title,
            "created_at": iso(n.created_at), "total": 0, "acked": 0,
            "reminded": 0, "last_remind_at": None, "pending": [],
            "_rounds": set()})
        if n.template_key == "ack_reminder":
            # 一轮催办给几个人就有几条，按轮次去重才是「催了几次」
            b["_rounds"].add(str((n.data or {}).get("remind_round") or n.id))
            b["reminded"] = len(b["_rounds"])
            b["last_remind_at"] = max(b["last_remind_at"] or "",
                                      iso(n.created_at))
            continue
        b["title"] = n.title          # 以原通知的标题为准
        b["created_at"] = iso(n.created_at)
        b["total"] += 1
        if n.acked_at:
            b["acked"] += 1
        else:
            u = db.get(User, n.user_id)
            if u is not None:
                b["pending"].append({"name": u.name, "student_id": u.student_id})
    for b in batches.values():
        b.pop("_rounds", None)
    return list(batches.values())


@router.post("/notify-batches/{batch_id}/remind")
def remind_batch(batch_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """一键提醒未签收的志愿者：催他们回去把原通知签掉。

    催办本身**不是**待签收通知——原来那条才是。否则催一次就多一条待办，
    5 个人的批次催一轮变成 10 条，谁也说不清还剩几个人没回。
    """
    pending = (db.query(Notification)
               .filter(Notification.batch_id == batch_id,
                       Notification.need_ack.is_(True),
                       Notification.acked_at.is_(None)).all())
    if not pending:
        raise HTTPException(400, "该批通知已全部签收，无需提醒")
    first = pending[0]
    activity_id = str((first.data or {}).get("activity_id") or "")
    if activity_id:
        ensure_activity_manager(db, user, activity_id)
    elif not user.is_admin():
        raise HTTPException(403, "无法确认该批通知所属活动，请由管理员操作")
    round_id = uid("rnd")
    for n in pending:
        u = db.get(User, n.user_id)
        if u is None:
            continue
        wecom.send_user(db, u, "ack_reminder", title=n.title,
                        data={"activity_id": activity_id,
                              "remind_round": round_id,
                              "ack_of": n.id},
                        batch_id=batch_id)
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=batch_id, target_label=first.title,
              activity_id=activity_id or None,
              summary=f"提醒 {len(pending)} 位未签收志愿者：{first.title}")
    db.commit()
    return {"ok": True, "count": len(pending)}


# ======================= 报名名单 =======================

@router.get("/shifts/{shift_id}/roster")
def roster(shift_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    positions = sorted(g.positions, key=lambda s: s.sort)
    checked_ids = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == g.id).all()}
    # 即选即得的报名问卷答案挂在报名记录上；筛选活动的在「报名申请」那张表里看
    questions = [] if act.is_screened() else (
        db.query(FormQuestion).filter(FormQuestion.activity_id == act.id)
        .order_by(FormQuestion.sort).all())

    def user_row(e: Enrollment) -> dict:
        u = db.get(User, e.user_id)
        st = db.get(Position, e.position_id) if e.position_id else None
        return {
            "enrollment_id": e.id,
            "answers": answers_out(db, questions, e.answers) if questions else [],
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "gender": u.gender, "phone": u.phone,
                     "grade": u.grade, **scoring.brief(db, u.id)},
            "position_id": e.position_id,
            "position_name": st.name if st else "",
            "position_group": st.group_name if st else "",
            "is_leader": e.is_leader,
            "leader_willing": e.leader_willing,
            "assign_forced": e.assign_forced,
            "allow_reassign": e.allow_reassign,
            "source": e.source,
            "joined_at": iso(e.joined_at),
            "checked_in": u.id in checked_ids,
            "confirm_deadline": iso(e.confirm_deadline),
            "status": e.status,
            "cancel_type": e.cancel_type,
            "seat_held": e.seat_held,
        }

    enrolled = [user_row(e) for e in db.query(Enrollment)
                 .filter(Enrollment.shift_id == g.id,
                         Enrollment.status == "enrolled")
                 .order_by(Enrollment.joined_at).all()]
    waitlist = []
    for e in logic.waitlist_rows(db, g.id):
        row = user_row(e)
        # 队列按岗位分，名次也要按岗位算（见 logic.waitlist_position）
        row["queue_position"] = logic.waitlist_position(db, e)
        waitlist.append(row)
    held = [user_row(e) for e in db.query(Enrollment)
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.seat_held.is_(True))
            .order_by(Enrollment.joined_at).all()]
    cancelled = [user_row(e) for e in db.query(Enrollment)
                 .filter(Enrollment.shift_id == g.id,
                         Enrollment.status == "cancelled")
                 .order_by(Enrollment.joined_at.desc()).limit(20).all()]
    deadline = logic.auto_promote_deadline(db, g)
    # 人员变动流水：活动前一天少了几个人，得看得见是谁、什么时候退的
    changes = (db.query(AuditLog)
               .filter(AuditLog.activity_id == act.id,
                       AuditLog.action.in_(tuple(logic.ROSTER_ACTIONS)))
               .order_by(AuditLog.created_at.desc()).limit(30).all())
    return {
        "questions": [q.out() for q in questions],
        "roster_changes": [{
            "action": c.action,
            "action_label": logic.ROSTER_ACTIONS.get(c.action, c.action),
            "who": c.actor_name, "summary": c.summary,
            "created_at": iso(c.created_at),
        } for c in changes],
        "auto_promote_deadline": iso(deadline),
        "auto_promote_over": now() > deadline,
        "shift": {"id": g.id, "name": g.name, "time_text": g.span_text(),
                  "capacity": g.total_capacity(), "needs_assign": g.needs_assign(),
                  "mode": g.mode, "mode_label": g.mode_label(),
                  "occupied": logic.shift_occupied_count(db, g.id),
                  "activity_id": act.id, "activity_title": act.title,
                  "start_time": iso(g.start_time)},
        "positions": [{"id": p.id, "name": p.name, "quota": p.quota,
                       "location": p.location,
                       "needs_leader": p.needs_leader,
                       "requirement_desc": p.requirement_desc() or "不限",
                       "enrolled": logic.position_enrolled_count(db, p.id)}
                      for p in positions],
        "enrolled": enrolled,
        "waitlist": waitlist,
        "held": held,
        "cancelled": cancelled,
        "checked_in_count": len(checked_ids),
    }


@router.post("/enrollments/{enrollment_id}/release-seat")
def release_held_seat(enrollment_id: str, db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """把临期退选扣留的名额放回去。

    扣留是对退选者的约束（他自己也不能再报），但位子空着与否是现场需求——
    补不补人由负责人判断。放回后候补队列按顺序递补。
    岗位名额本身不受影响：扣留从来不改老师填的数字，只是让一条已取消的
    记录占着位子。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None:
        raise HTTPException(404, "报名记录不存在")
    ensure_activity_manager(db, user, enr.activity_id)
    ensure_not_finished(_act(db, enr.activity_id))
    if not enr.seat_held:
        raise HTTPException(409, "该记录没有扣留名额")
    g = _shift(db, enr.shift_id)
    target = db.get(User, enr.user_id)
    with shift_lock(g.id):
        enr.seat_held = False
        audit_log(db, actor=user, action="update", target_type="enrollment",
                  target_id=enr.id, target_label=target.name,
                  activity_id=enr.activity_id,
                  summary=f"释放 {target.name} 临期退选扣留的名额"
                          f"（{db.get(Position, enr.position_id).name if enr.position_id else g.name}）")
        db.commit()
        promoted = logic.promote_all(db, g)
        db.commit()
    # 过了自动转正截止点，放出来的位子不会自动被候补占上——老师释放名额
    # 多半就是为了补人，这里要说清楚还差一步，否则位子会一直空着。
    over = now() > logic.auto_promote_deadline(db, g)
    return {"ok": True, "promoted": promoted, "auto_promote_over": over,
            "message": ("名额已释放，候补队首已转为待确认。" if promoted
                        else ("名额已释放；已过自动转正时间，"
                              "请在候补名单中手动转正。" if over
                              else "名额已释放，但当前没有候补在排队。"))}


@router.post("/enrollments/{enrollment_id}/promote")
def promote_manually(enrollment_id: str, db: OrmSession = Depends(db_session),
                     user: User = Depends(require_manager)):
    """管理者手动为候补开确认窗。

    自动转正在「报名截止」与「活动开始前 24 小时」的较晚者之后就停了——
    那之后转正的人只剩不到一天准备，且他再退选还要扣分。要不要补人由
    负责人判断，但来不来仍由本人定：和自动递补一样开 2 小时确认窗。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None:
        raise HTTPException(404, "报名记录不存在")
    ensure_activity_manager(db, user, enr.activity_id)
    ensure_not_finished(_act(db, enr.activity_id))
    if enr.status != "waitlisted":
        raise HTTPException(409, "该记录不在候补队列中")
    g = _shift(db, enr.shift_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    with shift_lock(g.id):
        if not logic.has_room(db, g, position):
            where = f"岗位「{position.name}」" if position else f"场次「{g.name}」"
            raise HTTPException(409, f"{where}没有空余名额，"
                                     f"如确需增加请先调整名额")
        target = db.get(User, enr.user_id)
        logic.start_confirm(db, enr, g)
        audit_log(db, actor=user, action="update", target_type="enrollment",
                  target_id=enr.id, target_label=target.name,
                  activity_id=enr.activity_id,
                  summary=f"手动为 {target.name} 开候补转正确认窗"
                          f"（{position.name if position else g.name}）")
        db.commit()
    return {"ok": True, "status": enr.status,
            "confirm_deadline": iso(enr.confirm_deadline),
            "confirm_hours": config.PROMOTE_CONFIRM_HOURS}


class LeaderBody(BaseModel):
    is_leader: bool = True


@router.post("/enrollments/{enrollment_id}/leader")
def set_leader(enrollment_id: str, body: LeaderBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """指定或取消组长。组长只由管理者指定，系统不自动选人。

    一个组只能有一个组长——指定新人时先把原来的摘掉，否则导出表和大屏会
    出现两个组长。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None:
        raise HTTPException(404, "报名记录不存在")
    ensure_activity_manager(db, user, enr.activity_id)
    ensure_not_finished(_act(db, enr.activity_id))
    if enr.status != "enrolled":
        raise HTTPException(409, "只有正式报名的志愿者才能担任组长")
    if not enr.position_id:
        raise HTTPException(409, "该志愿者还没有岗位，请先分配岗位")
    position = db.get(Position, enr.position_id)
    siblings = logic.group_positions(db, position)
    if not logic.group_needs_leader(siblings):
        raise HTTPException(409, f"岗位「{position.name}」所在的组未设置组长，"
                                 f"请先在岗位设置中勾选「需要组长」")
    target = db.get(User, enr.user_id)

    if body.is_leader:
        sibling_ids = [x.id for x in logic.group_positions(db, position)]
        previous = (db.query(Enrollment)
                    .filter(Enrollment.position_id.in_(sibling_ids),
                            Enrollment.is_leader.is_(True),
                            Enrollment.id != enr.id).all())
        for row in previous:
            row.is_leader = False
        enr.is_leader = True
        was = "、".join(db.get(User, r.user_id).name for r in previous)
        scope = logic.leader_scope_label(siblings, position)
        summary = (f"指定 {target.name} 为「{scope}」组长"
                   + (f"（原组长 {was} 已卸任）" if was else ""))
        wecom.send_user(db, target, "leader_appointed",
                        activity=db.get(Activity, enr.activity_id).title,
                        position=scope,
                        position_time=position.time_text(),
                        position_loc=position.location or "待定")
    else:
        enr.is_leader = False
        summary = f"取消 {target.name} 的岗位「{position.name}」组长身份"

    audit_log(db, actor=user, action="update", target_type="enrollment",
              target_id=enr.id, target_label=target.name,
              activity_id=enr.activity_id, summary=summary)
    db.commit()
    # 组长只由管理者指定：空缺了提醒他再指一位
    if not body.is_leader:
        logic.on_leader_vacated(db, position.id)
        db.commit()
    return {"ok": True, "is_leader": enr.is_leader}


# ======================= 一键分配 =======================

class PreviewBody(BaseModel):
    # 跨场次连续性：本活动其它场次已排过同名岗位的人，优先还待在那个岗位
    continuity: bool = True


@router.post("/shifts/{shift_id}/assign/preview")
def assign_preview(shift_id: str, body: PreviewBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    try:
        return assign.preview(db, g, continuity=body.continuity)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class ApplyPlanBody(BaseModel):
    plan: list[dict]
    source: str = "assign"


@router.post("/shifts/{shift_id}/assign/apply")
def assign_apply(shift_id: str, body: ApplyPlanBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    ensure_not_finished(_act(db, g.activity_id))
    if not body.plan:
        raise HTTPException(400, "空方案")
    notify_items: list[dict] = []
    try:
        result = assign.apply_plan(db, g, user, body.plan,
                                   source=body.source,
                                   notify_out=notify_items)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    if notify_items:
        bg.add_task(_notify_assignment_bg, notify_items)
    return result


@router.post("/shifts/{shift_id}/add-walkin")
def add_walkin(shift_id: str, body: dict,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """管理员直接添加志愿者（现场补招）。

    时段冲突、名额超出都拦一道，勾选强制才放行——补招是常规操作，
    不该顺手把场次搞超员；真要超也得是明确按下去的，且留痕。
    """
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    # 补招卡在「时长封账」而不是「活动结束」：现场漏登记的人事后才补进来，
    # 补进来才好给他记时长
    ensure_hours_editable(db, _act(db, g.activity_id))
    sid = (body.get("student_id") or "").strip()
    u = db.query(User).filter(User.student_id == sid).first()
    if u is None:
        raise HTTPException(404, f"学号 {sid} 不存在")
    if u.user_type == "teacher":
        raise HTTPException(400, "老师类型用户不参与志愿报名，无法补招")

    force = bool(body.get("force"))
    reasons: list[str] = []
    with shift_lock(g.id):
        live = (db.query(Enrollment)
                .filter(Enrollment.shift_id == g.id, Enrollment.user_id == u.id)
                .filter((Enrollment.status.in_(logic.ACTIVE))
                        | (Enrollment.seat_held.is_(True))).first())
        if live is not None:
            raise HTTPException(
                409, f"{u.name} 在该场次已有记录"
                     f"（{'临期退选占位' if live.seat_held else live.status}）")

        # 名额要按落点算：pick 模式下名额在岗位上，不传岗位一律判「满」，
        # 补招任何按岗位报名的场次都会被要求勾强制。
        position = None
        if body.get("position_id"):
            position = db.get(Position, body["position_id"])
            if position is None or position.shift_id != g.id:
                raise HTTPException(400, "岗位不属于该场次")

        clashes = logic.time_conflicts(db, u, g)
        if clashes:
            reasons.append(f"时段与{logic.conflict_message(clashes)}冲突")
        if not logic.has_room(db, g, position):
            reasons.append(f"「{position.name}」名额已满（上限{position.quota}人）"
                           if position else
                           f"场次名额已满（上限{g.total_capacity()}人）")
        if reasons and not force:
            raise HTTPException(409, f"{u.name}：{'；'.join(reasons)}。"
                                     f"确认添加请勾选强制")

        enr = Enrollment(id=uid("enr"), activity_id=g.activity_id, shift_id=g.id,
                         user_id=u.id, source="admin", joined_at=now(),
                         status="enrolled",
                         allow_reassign=bool(body.get("allow_reassign", True)))
        if position is not None:
            enr.position_id = position.id
            enr.assigned_at = now()
            enr.assign_forced = bool(reasons)
        db.add(enr)
        audit_log(db, actor=user, action="force_assign" if reasons else "create",
                  target_type="enrollment", target_id=enr.id,
                  target_label=f"{u.name}", activity_id=g.activity_id,
                  summary=f"补招 {u.name}（{sid}）至场次「{g.name}」"
                          + (f"（强制：{'；'.join(reasons)}）" if reasons else ""))
        db.commit()
    return {"ok": True, "enrollment_id": enr.id, "forced_reasons": reasons}


# ======================= 图片上传 / 媒体访问 =======================

UPLOAD_DIR = Path(__file__).resolve().parents[1] / "data" / "uploads"
IMAGE_EXTS = {"png": "image/png", "jpg": "image/jpeg",
              "jpeg": "image/jpeg", "webp": "image/webp"}
MAX_IMAGE_BYTES = 2 * 1024 * 1024


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...),
                       user: User = Depends(require_manager)):
    """上传答疑群二维码等图片（png/jpg/jpeg/webp，≤2MB），返回访问链接。"""
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in IMAGE_EXTS:
        raise HTTPException(400, "仅支持 png/jpg/jpeg/webp 格式的图片")
    data = await file.read()
    if len(data) > MAX_IMAGE_BYTES:
        raise HTTPException(400, "图片大小不能超过 2MB")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"qr_{uuid4().hex}.{ext}"
    (UPLOAD_DIR / filename).write_bytes(data)
    return {"url": f"/api/media/{filename}"}


# 媒体路由不带 /api/staff 前缀：单独构建路由对象后把已生成的
# 路由表并入上方 router（main.py 只 include 本模块的 router，无需改动）。
media_router = APIRouter(tags=["staff"])


@media_router.get("/api/media/{name}")
def media_file(name: str):
    """读取上传的图片。文件名仅允许字母数字下划线点，防止目录穿越。"""
    if (not name or ".." in name or name.startswith(".")
            or not re.fullmatch(r"[A-Za-z0-9_.]+", name)):
        raise HTTPException(404, "文件不存在")
    path = UPLOAD_DIR / name
    if not path.is_file():
        raise HTTPException(404, "文件不存在")
    media_type = IMAGE_EXTS.get(path.suffix.lower().lstrip("."),
                                "application/octet-stream")
    return FileResponse(path, media_type=media_type)


router.routes.extend(media_router.routes)
