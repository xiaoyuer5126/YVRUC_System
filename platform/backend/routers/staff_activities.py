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
from .. import assign
from ..audit import diff_changes, log as audit_log
from ..database import db_session
from ..deps import require_manager
from ..locks import shift_lock
from ..notify import notify_enrolled
from ..perm import (activity_manager_ids, add_manager,
                     ensure_activity_manager, managed_activity_ids,
                     remove_manager)
from ..integrations import wecom
from ..models import (Activity, ActivityManager, CheckinRecord, Enrollment,
                      Shift, Position, User, iso, now, uid)
from ..serializers import activity_brief

router = APIRouter(prefix="/api/staff", tags=["staff"])

ACTIVITY_FIELDS = {
    "title": "名称", "type": "类型", "intro": "简介", "location": "地点",
    "contact_info": "公示联系方式", "contacts": "公示联系人",
    "qa_group_qr": "答疑群二维码",
    "reg_start": "报名开始", "reg_end": "报名截止",
}


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


def _group(db: OrmSession, gid: str) -> Shift:
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
                            group=g.name, position=st.name,
                            position_time=st.time_text(),
                            position_loc=st.location or "待定")
        db.commit()
    finally:
        db.close()


# ======================= 活动列表与基本信息 =======================

@router.get("/activities")
def list_all(db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    managed = managed_activity_ids(db, user)
    q = db.query(Activity).order_by(Activity.created_at.desc())
    if managed is not None:
        q = q.filter(Activity.id.in_(managed or ['__none__']))
    acts = q.all()
    out = []
    for a in acts:
        item = activity_brief(db, a)
        wl = 0
        for g in a.shifts:
            wl += logic.waitlist_rows(db, g.id).__len__()
        item["waitlist_count"] = wl
        out.append(item)
    return out


class ActivityBody(BaseModel):
    title: str = Field(max_length=128)
    type: str = "A"
    intro: str = ""
    location: str = ""
    contact_info: str = ""
    contacts: list[dict] = []
    qa_group_qr: str = ""
    cover_hue: int = 0
    reg_start: str | None = None
    reg_end: str | None = None


def _clean_contacts(raw: list[dict]) -> list[dict]:
    """清洗公示联系人名单：至少一位，且每位的姓名与电话均非空。"""
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
        raise HTTPException(400, "请至少添加一位公示联系人（姓名+电话）")
    return contacts


@router.post("/activities")
def create_activity(body: ActivityBody, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    # 创建活动信息完整性校验：全部必填
    if not body.title.strip():
        raise HTTPException(400, "请填写活动名称")
    if body.type not in config_types():
        raise HTTPException(400, "活动类型必须是 A/B/C/D")
    if not body.location.strip():
        raise HTTPException(400, "请填写活动地点")
    contacts = _clean_contacts(body.contacts)
    if not body.intro.strip():
        raise HTTPException(400, "请填写活动简介")
    if not body.qa_group_qr.strip():
        raise HTTPException(400, "请上传答疑群二维码")
    reg_start, reg_end = _parse_dt(body.reg_start), _parse_dt(body.reg_end)
    if reg_start is None or reg_end is None:
        raise HTTPException(400, "请选择报名开始与截止时间")
    act = Activity(id=uid("act"), title=body.title.strip(), type=body.type,
                   intro=body.intro.strip(), location=body.location.strip(),
                   contact_info=body.contact_info.strip(),
                   qa_group_qr=body.qa_group_qr, cover_hue=body.cover_hue,
                   reg_start=reg_start, reg_end=reg_end,
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


def config_types():
    from .. import config
    return set(config.ACTIVITY_TYPES)


@router.patch("/activities/{activity_id}")
def update_activity(activity_id: str, body: dict, bg: BackgroundTasks,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    before = {k: getattr(act, k) for k in ACTIVITY_FIELDS}
    before["type"] = act.type
    for key in ACTIVITY_FIELDS:
        if key not in body:
            continue
        value = body[key]
        if key in ("reg_start", "reg_end"):
            setattr(act, key, _parse_dt(value))
        elif key == "contacts":
            act.contacts = _clean_contacts(value)
        else:
            if key == "type" and value not in config_types():
                raise HTTPException(400, "活动类型必须是 A/B/C/D")
            setattr(act, key, value)
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
              changes=changes)
    db.commit()
    return {"ok": True, "changes": changes}


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


# ======================= 状态流转 =======================

@router.post("/activities/{activity_id}/publish")
def publish(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not act.shifts:
        raise HTTPException(400, "请先至少创建一个场次（场次）再发布")
    if act.status != "draft":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能发布")
    act.status = "published"
    if act.reg_start is None:
        act.reg_start = now()
    audit_log(db, actor=user, action="publish", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="发布活动，开始报名", visible=False)
    db.commit()
    return {"ok": True}


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
                    change_summary="活动已结束，感谢您的参与！时长将陆续录入。")
    return {"ok": True}


@router.post("/activities/{activity_id}/close")
def close(activity_id: str, bg: BackgroundTasks,
          db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    return _transition(activity_id, "closed", "close", "截止报名",
                       ("published",), db, user, bg)


@router.post("/activities/{activity_id}/finish")
def finish(activity_id: str, bg: BackgroundTasks,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    return _transition(activity_id, "finished", "finish", "结束活动",
                       ("published", "closed"), db, user, bg)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """仅草稿可删——发布过的活动承载着报名记录与留痕，不允许物理删除。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status != "draft":
        raise HTTPException(409, "仅「筹备中」的活动可以删除；"
                                 "已发布的活动请使用「结束活动」归档")
    label = act.title
    db.delete(act)
    audit_log(db, actor=user, action="delete", target_type="activity",
              target_id=activity_id, target_label=label, activity_id=None,
              summary=f"删除草稿活动「{label}」", visible=False)
    db.commit()
    return {"ok": True}


# ======================= 一级场次 =======================

SHIFT_MODES = ("pick", "assign")


class ShiftBody(BaseModel):
    name: str = Field(max_length=128)
    start_time: str
    end_time: str
    mode: str = "pick"          # pick 报名即选岗位 / assign 截止后统一分配
    # 只有 assign 模式用得上：报名时还没有岗位可选，名额只能记在场次上
    capacity: int = Field(default=0, ge=0)
    note: str = ""
    waitlist_quota: int = Field(default=3, ge=0)   # 候补队列人数上限


def _validate_shift_times(start: datetime, end: datetime):
    if end <= start:
        raise HTTPException(400, "结束时间必须晚于开始时间")


def _validate_shift_capacity(db: OrmSession, g: Shift):
    """assign 模式的场次总名额校验。pick 模式名额在岗位上，这里不管。"""
    if not g.needs_assign():
        return
    if g.capacity <= 0:
        raise HTTPException(400, "「截止后统一分配」的场次必须填写人数上限")
    used = logic.shift_occupied_count(db, g.id)
    if g.capacity < used:
        raise HTTPException(
            400, f"人数上限不能小于已占用名额（{used} 人：含已确认报名与"
                 f"临近退选扣留的名额）")


def _apply_shift_mode(db: OrmSession, g: Shift, mode: str):
    """切换场次模式，并把与新模式矛盾的既有分配清理干净。"""
    if mode not in SHIFT_MODES:
        raise HTTPException(400, "场次模式必须是 pick 或 assign")
    if mode == "assign" and g.mode != "assign":
        # 二级待定：岗位由调配期统一分配，先前报名时选定的岗位一律作废，
        # 否则分配面板里会混着一批"已经有岗位"的人，谁也说不清是谁定的
        (db.query(Enrollment)
         .filter(Enrollment.shift_id == g.id,
                 Enrollment.position_id.isnot(None))
         .update({"position_id": None, "assigned_at": None,
                  "assigned_by": None, "assign_forced": False},
                 synchronize_session=False))
    g.mode = mode


@router.post("/activities/{activity_id}/shifts")
def create_shift(activity_id: str, body: ShiftBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    start, end = _parse_dt(body.start_time), _parse_dt(body.end_time)
    _validate_shift_times(start, end)
    g = Shift(id=uid("g"), activity_id=act.id, name=body.name.strip(),
                      start_time=start, end_time=end, capacity=body.capacity,
                      note=body.note.strip(), sort=len(act.shifts),
                      waitlist_quota=body.waitlist_quota)
    _apply_shift_mode(db, g, body.mode)
    _validate_shift_capacity(db, g)
    db.add(g)
    db.flush()
    audit_log(db, actor=user, action="create", target_type="group",
              target_id=g.id, target_label=f"{act.title}·{g.name}",
              activity_id=act.id,
              summary=f"新建场次「{g.name}」（{g.span_text()}，"
                      f"{g.mode_label()}，上限{body.capacity}人）")
    db.commit()
    if act.status != "draft":
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title,
                    change_summary=f"新增场次「{g.name}」{g.span_text()}")
    return {"id": g.id}


SHIFT_FIELDS = {"name": "场次名", "start_time": "开始时间", "end_time": "结束时间",
                "capacity": "人数上限", "mode": "岗位模式", "note": "备注",
                "waitlist_quota": "候补名额"}


@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: str, body: dict, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(g, k) for k in SHIFT_FIELDS}
    for key in SHIFT_FIELDS:
        if key not in body:
            continue
        v = body[key]
        if key in ("start_time", "end_time"):
            setattr(g, key, _parse_dt(v))
        elif key == "mode":
            _apply_shift_mode(db, g, v)
        else:
            setattr(g, key, v)
    _validate_shift_times(g.start_time, g.end_time)
    _validate_shift_capacity(db, g)

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
                      f"{'、'.join(c['label'] for c in changes) or '无实质变化'}",
              changes=changes)
    db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: str, force: int = 0, bg: BackgroundTasks = None,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    n_active = db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status.in_(logic.ACTIVE)).count()
    if n_active and not force:
        raise HTTPException(409, f"该场次已有 {n_active} 人有效报名，"
                                 f"删除需强制执行（force=1），全部记录将被取消")
    if n_active and force:
        # 场次没了，扣留的占位名额也一并失效——这不是志愿者的责任
        (db.query(Enrollment).filter(Enrollment.shift_id == g.id)
         .update({"status": "cancelled", "cancel_type": "group_deleted",
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
                      (f"（强制，含{n_active}条报名记录）" if n_active else ""))
    db.commit()
    return {"ok": True}


# ======================= 岗位 =======================

class PositionBody(BaseModel):
    """岗位没有时间——时间随场次。名额、候补、条件、组长都在这一层。"""
    name: str = Field(max_length=128)
    location: str = ""
    quota: int = Field(gt=0)
    waitlist_quota: int = Field(default=3, ge=0)
    requirement_tags: dict = {}
    requirement_note: str = ""
    training_note: str = ""
    needs_leader: bool = False


POSITION_FIELDS = {"name": "岗位名", "location": "地点", "quota": "名额",
                   "waitlist_quota": "候补名额", "needs_leader": "需要组长",
                   "requirement_tags": "条件要求",
                   "requirement_note": "要求原文", "training_note": "培训内容"}


def _validate_position(db: OrmSession, g: Shift, s: Position):
    """岗位自带名额，不再受场次总数约束——场次只管时间。

    assign 模式例外：那时名额记在场次上，各岗位之和不能超过它，
    否则一键分配会分出比报名人数更多的坑位。
    """
    if s.quota <= 0:
        raise HTTPException(400, "岗位名额必须大于 0")
    used = logic.position_occupied_count(db, s.id) if s.id else 0
    if s.quota < used:
        raise HTTPException(400, f"岗位名额不能小于已占用的 {used} 人")
    if g.needs_assign():
        quota_sum = sum(x.quota for x in g.positions if x.id != s.id) + s.quota
        if quota_sum > g.capacity:
            raise HTTPException(
                400, f"各岗位名额之和({quota_sum})超过场次人数上限({g.capacity})")


@router.post("/shifts/{shift_id}/positions")
def create_position(shift_id: str, body: PositionBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    s = Position(id=uid("s"), shift_id=g.id, name=body.name.strip(),
                location=body.location.strip(), quota=body.quota,
                waitlist_quota=body.waitlist_quota,
                needs_leader=body.needs_leader,
                requirement_tags=body.requirement_tags or {},
                requirement_note=body.requirement_note.strip(),
                training_note=body.training_note.strip(),
                sort=len(g.positions))
    _validate_position(db, g, s)
    db.add(s)
    db.flush()   # 让 group 关系可用，供 time_text() 使用
    audit_log(db, actor=user, action="create", target_type="position",
              target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
              activity_id=act.id,
              summary=f"新建岗位「{s.name}」（{s.time_text()}，"
                      f"{s.location or '地点待定'}，{s.quota}人）")
    db.commit()
    return {"id": s.id}


@router.patch("/positions/{position_id}")
def update_position(position_id: str, body: dict, bg: BackgroundTasks,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _group(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    before_raw = {k: getattr(s, k) for k in POSITION_FIELDS}
    for key in POSITION_FIELDS:
        if key not in body:
            continue
        setattr(s, key, body[key])
    _validate_position(db, g, s)

    def fmt(v):
        if isinstance(v, datetime):
            return v.strftime("%m-%d %H:%M")
        return v
    changes = diff_changes({k: fmt(v) for k, v in before_raw.items()},
                           {k: fmt(getattr(s, k)) for k in POSITION_FIELDS},
                           POSITION_FIELDS)
    if changes and act.status != "draft":
        affected = (db.query(Enrollment.user_id)
                    .filter(Enrollment.position_id == s.id,
                            Enrollment.status == "confirmed").all())
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
              changes=changes)
    db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/positions/{position_id}")
def delete_position(position_id: str,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _group(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    n = db.query(Enrollment).filter(Enrollment.position_id == s.id,
                                    Enrollment.status == "confirmed").count()
    if n:
        raise HTTPException(409, f"该岗位仍有 {n} 人已确认，请先在分配面板调整")
    label = f"{act.title}·{g.name}·{s.name}"
    db.delete(s)
    audit_log(db, actor=user, action="delete", target_type="position",
              target_id=s.id, target_label=label, activity_id=act.id,
              summary=f"删除岗位「{s.name}」")
    db.commit()
    return {"ok": True}


# ======================= 活动管理者 =======================

@router.get("/activities/{activity_id}/managers")
def list_managers(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ids = activity_manager_ids(db, activity_id)
    users = [db.get(User, i) for i in ids]
    return [{
        "id": u.id, "name": u.name, "student_id": u.student_id,
        "college": u.college, "roles": u.roles or [],
        "role_label": u.active_role_label(),
        "is_creator": u.id == act.created_by,
    } for u in users if u]


class ManagerBody(BaseModel):
    student_id: str


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
              summary=f"添加活动管理者：{target.name}（{target.student_id}）",
              visible=False)
    db.commit()
    return {"ok": True, "id": row.id}


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
              summary=f"移除活动管理者：{target.name if target else user_id}",
              visible=False)
    db.commit()
    return {"ok": True}


# ======================= 志愿者通知 =======================

class NotifyBody(BaseModel):
    scope: str = "all"      # all 全部志愿者 / group 指定场次 / not_checked_in 场次未到岗
    shift_id: str = ""
    title: str = Field(max_length=128)
    content: str


@router.post("/activities/{activity_id}/notify")
def notify_volunteers(activity_id: str, body: NotifyBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """向该活动 status=confirmed 的报名者发送自定义通知。

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
        g = _group(db, body.shift_id)
        if g.activity_id != act.id:
            raise HTTPException(400, "所选场次不属于该活动")
        shift_id = g.id
    # 两步查询：先 DISTINCT user_id（JSON 列在 PG 上无相等运算符，不能整行去重）
    ids = (db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status == "confirmed"))
    if shift_id:
        ids = ids.filter(Enrollment.shift_id == shift_id)
    ids = [r[0] for r in ids.distinct().all()]
    users = [db.get(User, i) for i in ids]
    users = [u for u in users if u]
    if body.scope == "not_checked_in":
        checked = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == shift_id).all()}
        users = [u for u in users if u.id not in checked]
    for u in users:
        wecom.send_user(db, u, "custom", title=title, content=content)
    n = len(users)
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=act.id, target_label=act.title,
              activity_id=act.id,
              summary=f"向{n}位志愿者发送通知：{title}", visible=False)
    db.commit()
    return {"ok": True, "count": n}


# ======================= 报名名单 =======================

@router.get("/shifts/{shift_id}/roster")
def roster(shift_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    positions = sorted(g.positions, key=lambda s: s.sort)
    checked_ids = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == g.id).all()}

    def user_row(e: Enrollment) -> dict:
        u = db.get(User, e.user_id)
        st = db.get(Position, e.position_id) if e.position_id else None
        return {
            "enrollment_id": e.id,
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "gender": u.gender, "phone": u.phone,
                     "grade": u.grade, "credit_score": u.credit_score},
            "position_id": e.position_id,
            "position_name": st.name if st else "",
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

    confirmed = [user_row(e) for e in db.query(Enrollment)
                 .filter(Enrollment.shift_id == g.id,
                         Enrollment.status == "confirmed")
                 .order_by(Enrollment.joined_at).all()]
    waitlist = []
    for i, e in enumerate(logic.waitlist_rows(db, g.id), start=1):
        row = user_row(e)
        row["queue_position"] = i
        waitlist.append(row)
    cancelled = [user_row(e) for e in db.query(Enrollment)
                 .filter(Enrollment.shift_id == g.id,
                         Enrollment.status == "cancelled")
                 .order_by(Enrollment.joined_at.desc()).limit(20).all()]
    return {
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
                       "confirmed": logic.position_confirmed_count(db, p.id)}
                      for p in positions],
        "confirmed": confirmed,
        "waitlist": waitlist,
        "cancelled": cancelled,
        "checked_in_count": len(checked_ids),
    }


# ======================= 一键分配 =======================

class PreviewBody(BaseModel):
    # 跨场次连续性：本活动其它场次已排过同名岗位的人，优先还待在那个岗位
    continuity: bool = True


@router.post("/shifts/{shift_id}/assign/preview")
def assign_preview(shift_id: str, body: PreviewBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _group(db, shift_id)
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
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
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
    """管理员直接添加志愿者（现场补录）。

    时段冲突、名额超出都拦一道，勾选强制才放行——补录是常规操作，
    不该顺手把场次搞超员；真要超也得是明确按下去的，且留痕。
    """
    g = _group(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    sid = (body.get("student_id") or "").strip()
    u = db.query(User).filter(User.student_id == sid).first()
    if u is None:
        raise HTTPException(404, f"学号 {sid} 不存在")
    if u.user_type == "teacher":
        raise HTTPException(400, "老师类型用户不参与志愿报名，无法补录")

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
                     f"（{'临近退选占位' if live.seat_held else live.status}）")

        clashes = logic.time_conflicts(db, u, g)
        if clashes:
            reasons.append(f"时段与{logic.conflict_message(clashes)}冲突")
        if not logic.has_room(db, g):
            reasons.append(f"场次名额已满（上限{g.capacity}人）")
        if reasons and not force:
            raise HTTPException(409, f"{u.name}：{'；'.join(reasons)}。"
                                     f"确认添加请勾选强制")

        enr = Enrollment(id=uid("enr"), activity_id=g.activity_id, shift_id=g.id,
                         user_id=u.id, source="admin", joined_at=now(),
                         status="confirmed",
                         allow_reassign=bool(body.get("allow_reassign", True)))
        if body.get("position_id"):
            st = db.get(Position, body["position_id"])
            if st is None or st.shift_id != g.id:
                raise HTTPException(400, "岗位无效")
            enr.position_id = st.id
            enr.assigned_at = now()
            enr.assign_forced = bool(reasons)
        db.add(enr)
        audit_log(db, actor=user, action="force_assign" if reasons else "create",
                  target_type="enrollment", target_id=enr.id,
                  target_label=f"{u.name}", activity_id=g.activity_id,
                  summary=f"管理员补录 {u.name}（{sid}）至场次「{g.name}」"
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
