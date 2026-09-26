"""对外联络部的三件事：值班交接、志愿北京登记、立项书与盖章立项书。

规则本身在 backend/liaison.py，这里只是接口。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, hours as hours_flow, liaison
from ..audit import log as audit_log
from ..database import db_session
from ..deps import require_manager
from ..models import (Activity, Attachment, Department, DutyHandover, HoursRequest,
                      User, VbRegistration, iso, now, uid)
from ..notify import notify
from ..perm import ensure_activity_manager, is_activity_manager
from .materials import EXT_TYPES, MATERIAL_DIR

router = APIRouter(prefix="/api/staff", tags=["liaison"])


def _person(u: User | None) -> dict | None:
    if u is None:
        return None
    return {"id": u.id, "name": u.name, "student_id": u.student_id,
            "post_label": u.post_label()}


# ======================= 值班 =======================

def _duty_out(db: OrmSession, dept: Department, user: User) -> dict:
    cur = liaison.on_duty(db, dept)
    head = liaison.dept_head(db, dept.id)
    rows = (db.query(DutyHandover).filter(DutyHandover.dept_id == dept.id)
            .order_by(DutyHandover.created_at.desc()).limit(30).all())

    def name(uid_):
        u = db.get(User, uid_) if uid_ else None
        return u.name if u else ""
    return {
        "dept_id": dept.id, "dept_name": dept.name,
        "on_duty": _person(cur), "head": _person(head),
        "eligible": [_person(u) for u in liaison.eligible_users(db, dept)],
        # 部长（与会长团、管理员）指定；在班的人自己交班
        "can_assign": user.has_full_access() or liaison.is_head_of(user, dept),
        "can_handover": cur is not None and cur.id == user.id,
        "history": [{"from": name(h.from_user_id), "to": name(h.to_user_id),
                     "actor": name(h.actor_id), "note": h.note,
                     "created_at": iso(h.created_at)} for h in rows],
    }


@router.get("/duty")
def duty_status(db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """启用值班的部门里，与我有关的那几个（我能值班的、或我是会长团/管理员）。
    与我无关就是空列表，前端据此决定要不要显示值班卡片。"""
    out = []
    for dept in (db.query(Department)
                 .filter(Department.name.in_(liaison.DUTY_DEPTS))
                 .order_by(Department.sort).all()):
        if user.has_full_access() or liaison.eligible(user, dept):
            out.append(_duty_out(db, dept, user))
    return out


class DutyBody(BaseModel):
    user_id: str = ""          # 空 = 撤下值班（没人在班，通知回落给部长）
    note: str = ""


@router.post("/duty/{dept_id}")
def set_duty(dept_id: str, body: DutyBody, db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """指定或交接值班。部长（会长团、管理员）随时可以指定；在班的人可以交给
    本部门另一位骨干或部长。一个部门同一时间只有一个人在班。"""
    dept = db.get(Department, dept_id)
    if dept is None or not liaison.duty_enabled(dept):
        raise HTTPException(404, "该部门没有启用值班")
    cur = liaison.on_duty(db, dept)
    boss = user.has_full_access() or liaison.is_head_of(user, dept)
    handing = cur is not None and cur.id == user.id
    if not (boss or handing):
        raise HTTPException(403, "只有部长或当前值班人可以安排值班")
    target = None
    if body.user_id.strip():
        target = db.get(User, body.user_id.strip())
        if not liaison.eligible(target, dept):
            raise HTTPException(400, f"只有{dept.name}的部门骨干或部长可以值班")
    elif not boss:
        # 在班的人不能把班撂下就走：要交给一个具体的人
        raise HTTPException(400, "请选择交班给谁")
    if (target.id if target else None) == (cur.id if cur else None):
        raise HTTPException(400, "值班人没有变化")

    note = body.note.strip()[:256]
    dept.duty_user_id = target.id if target else None
    db.add(DutyHandover(id=uid("dty"), dept_id=dept.id,
                        from_user_id=cur.id if cur else None,
                        to_user_id=target.id if target else None,
                        actor_id=user.id, note=note))
    what = (f"{cur.name if cur else '无人'} → {target.name if target else '无人值班'}"
            + (f"；{note}" if note else ""))
    audit_log(db, actor=user, action="duty", target_type="department",
              target_id=dept.id, target_label=dept.name, activity_id=None,
              summary=f"{'交班' if handing and not boss else '安排值班'}：{what}")
    # 接班的人要知道手上已经压着几条
    if target is not None and target.id != user.id:
        pending = _dept_pending_count(db, dept)
        notify(db, target, f"您开始值班｜{dept.name}",
               f"{user.name} 安排您在{dept.name}值班。之后本部门的审批通知会发给您。"
               + (f"\n目前有 {pending} 条待处理，请到「活动审批」查看。" if pending
                  else "")
               + (f"\n备注：{note}" if note else ""),
               data={"route": "approvals"}, need_ack=True)
    if cur is not None and cur.id != user.id:
        notify(db, cur, f"您的值班已结束｜{dept.name}",
               f"{user.name} 把{dept.name}的值班改为："
               f"{target.name if target else '暂无人值班'}。",
               data={"route": "approvals"})
    # 值班人自己交班时告诉部长一声；部长不收逐条审批，但得知道现在是谁在班
    head = liaison.dept_head(db, dept.id)
    if head is not None and head.id != user.id \
            and head.id != (target.id if target else ""):
        notify(db, head, f"值班交接｜{dept.name}", f"{user.name}：{what}",
               data={"route": "approvals"})
    db.commit()
    return _duty_out(db, dept, user)


def _dept_pending_count(db: OrmSession, dept: Department) -> int:
    acts = [a for (a,) in db.query(Activity.id)
            .filter(Activity.approval_dept_id == dept.id).all()]
    n = (db.query(Activity).filter(Activity.approval_dept_id == dept.id,
                                   Activity.status == "pending").count())
    if acts:
        n += (db.query(HoursRequest)
              .filter(HoursRequest.activity_id.in_(acts),
                      HoursRequest.status == "pending").count())
    if dept.name == liaison.LIAISON_DEPT:
        n += (db.query(VbRegistration)
              .filter(VbRegistration.status == "submitted").count())
    return n


# ======================= 志愿北京登记 =======================

def _vb_decider(db: OrmSession, user: User) -> bool:
    """谁来核对志愿北京：对外联络部的值班人与部长，会长团、管理员。"""
    if user.has_full_access():
        return True
    dept = liaison.liaison_dept(db)
    return dept is not None and liaison.is_dept_handler(db, user, dept.id)


def _vb_out(db: OrmSession, r: VbRegistration) -> dict:
    def name(uid_):
        u = db.get(User, uid_) if uid_ else None
        return u.name if u else ""
    return {"id": r.id, "activity_id": r.activity_id, "vb_name": r.vb_name,
            "status": r.status, "status_label": r.status_label(),
            "submitted_by": name(r.submitted_by),
            "submitted_at": iso(r.submitted_at),
            "handled_by": name(r.handled_by), "handled_at": iso(r.handled_at),
            "reply": r.reply}


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


@router.get("/activities/{activity_id}/vb-registrations")
def list_vb(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    act = _act(db, activity_id)
    manager = is_activity_manager(db, user, act.id)
    decider = _vb_decider(db, user)
    if not (manager or decider):
        raise HTTPException(403, "您不是该活动的管理者，无法查看")
    rows = (db.query(VbRegistration)
            .filter(VbRegistration.activity_id == act.id)
            .order_by(VbRegistration.submitted_at.desc()).all())
    return {"items": [_vb_out(db, r) for r in rows],
            "can_submit": manager and user.can_write() and liaison.is_published(act),
            "can_decide": decider, "manager": manager,
            "published": liaison.is_published(act)}


class VbBody(BaseModel):
    vb_name: str


@router.post("/activities/{activity_id}/vb-registrations")
def submit_vb(activity_id: str, body: VbBody, db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """「我在志愿北京建好了活动，名称是 X」。条数不限，建错了作废再报。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not liaison.is_published(act):
        raise HTTPException(409, "活动正式发布之后才能登记志愿北京")
    name = body.vb_name.strip()
    if len(name) < 2:
        raise HTTPException(400, "请填写志愿北京上的活动名称")
    r = VbRegistration(id=uid("vbr"), activity_id=act.id, vb_name=name[:200],
                       submitted_by=user.id)
    db.add(r)
    audit_log(db, actor=user, action="vb_register", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"登记志愿北京活动「{r.vb_name}」")
    dept = liaison.liaison_dept(db)
    contact = liaison.approval_contact(db, dept.id) if dept else None
    if contact is not None:
        notify(db, contact, f"志愿北京待核对｜{act.title}",
               f"{user.name} 登记：「{act.title}」已在志愿北京建好活动，"
               f"名称「{r.vb_name}」。\n请到志愿北京核对后，在「活动审批」通过或驳回。",
               data={"activity_id": act.id, "route": "approvals"}, need_ack=True)
    db.commit()
    return _vb_out(db, r)


@router.post("/vb-registrations/{reg_id}/void")
def void_vb(reg_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    """作废一条登记（报错了名称、建重了）。记录留着，只是不再算数。"""
    r = db.get(VbRegistration, reg_id)
    if r is None:
        raise HTTPException(404, "登记不存在")
    ensure_activity_manager(db, user, r.activity_id)
    if r.status not in ("submitted", "approved"):
        raise HTTPException(409, f"该登记{r.status_label()}，无需作废")
    r.status, r.handled_by, r.handled_at = "void", user.id, now()
    act = _act(db, r.activity_id)
    audit_log(db, actor=user, action="vb_register", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"作废志愿北京登记「{r.vb_name}」")
    db.commit()
    return _vb_out(db, r)


class VbDecideBody(BaseModel):
    approve: bool
    reply: str = ""


@router.post("/vb-registrations/{reg_id}/decide")
def decide_vb(reg_id: str, body: VbDecideBody, db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """在志愿北京上核对过了：通过或驳回（驳回写明原因，发给登记人）。"""
    r = db.get(VbRegistration, reg_id)
    if r is None:
        raise HTTPException(404, "登记不存在")
    if not _vb_decider(db, user):
        raise HTTPException(403, "只有对外联络部的值班人或部长可以核对志愿北京登记")
    if r.status != "submitted":
        raise HTTPException(409, f"该登记{r.status_label()}")
    reply = body.reply.strip()[:512]
    if not body.approve and len(reply) < 2:
        raise HTTPException(400, "驳回必须写明理由")
    r.status = "approved" if body.approve else "rejected"
    r.handled_by, r.handled_at, r.reply = user.id, now(), reply
    act = _act(db, r.activity_id)
    audit_log(db, actor=user, action="vb_register", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"{'通过' if body.approve else '驳回'}志愿北京登记「{r.vb_name}」"
                       + (f"：{reply}" if reply else "")))
    who = db.get(User, r.submitted_by)
    if who is not None:
        notify(db, who, f"志愿北京登记{'已通过' if body.approve else '未通过'}"
                        f"｜{act.title}",
               f"「{r.vb_name}」" + ("已核对通过。" if body.approve
                                    else "未通过核对，可作废后重新登记。")
               + (f"\n意见：{reply}" if reply else ""),
               data={"activity_id": act.id})
    db.commit()
    return _vb_out(db, r)


@router.get("/vb-registrations")
def pending_vb(db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """待核对的志愿北京登记，挂在「活动审批」页上。"""
    if not _vb_decider(db, user):
        return []
    rows = (db.query(VbRegistration)
            .filter(VbRegistration.status == "submitted")
            .order_by(VbRegistration.submitted_at).all())
    out = []
    for r in rows:
        act = db.get(Activity, r.activity_id)
        out.append({**_vb_out(db, r), "activity_title": act.title if act else "",
                    "organizer": act.organizer if act else ""})
    return out


# ======================= 立项书与盖章立项书 =======================

def _file_out(row: Attachment | None) -> dict | None:
    if row is None:
        return None
    return {"id": row.id, "title": row.title, "size": row.size,
            "uploaded_at": iso(row.created_at)}


def _slot_block(db: OrmSession, act: Activity, slot: str) -> str:
    """这一格现在为什么不能换；空串 = 能换。"""
    if slot == "proposal":
        if act.status not in ("draft", "pending"):
            return "审批已通过，立项书不能再更换"
        return ""
    if not liaison.is_external(db, act):
        return "盖章立项书只用于对外联络部归口的活动"
    if not liaison.is_published(act):
        return "活动正式发布之后才能上传盖章立项书"
    if act.hours_locked():
        return "时长已锁定，盖章立项书不能再更换"
    if hours_flow.pending_lock(db, act.id) is not None:
        return "时长正在等待审核，审核期间不能更换盖章立项书"
    return ""


@router.get("/activities/{activity_id}/project-files")
def project_files(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    act = _act(db, activity_id)
    if not liaison.can_see_project_files(db, user, act):
        raise HTTPException(403, "您无权查看该活动的立项材料")
    manager = is_activity_manager(db, user, act.id) and user.can_write()
    out = {"external": liaison.is_external(db, act), "manager": manager}
    for slot in liaison.SLOTS:
        block = _slot_block(db, act, slot)
        out[slot] = _file_out(liaison.project_file(db, act.id, slot))
        out[f"{slot}_block"] = block
        out[f"can_upload_{slot}"] = manager and not block
    return out


@router.post("/activities/{activity_id}/project-files/{slot}")
async def upload_project_file(activity_id: str, slot: str,
                              file: UploadFile = File(...),
                              db: OrmSession = Depends(db_session),
                              user: User = Depends(require_manager)):
    """上传立项书 / 盖章立项书。每格只留一份，传新的就替换旧的。"""
    if slot not in liaison.SLOTS:
        raise HTTPException(404, "没有这一项")
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    block = _slot_block(db, act, slot)
    if block:
        raise HTTPException(409, block)
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 {'/'.join(sorted(EXT_TYPES))}）")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件是空的")
    if len(data) > config.MAX_MATERIAL_BYTES:
        raise HTTPException(400, f"文件不能超过 {config.MAX_MATERIAL_BYTES // (1024 * 1024)}MB")
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (MATERIAL_DIR / stored).write_bytes(data)
    old = liaison.drop_project_file(db, act.id, slot)
    label = liaison.SLOTS[slot]
    row = Attachment(id=uid("att"), owner_type=slot, owner_id=act.id,
                     activity_id=act.id, kind="file",
                     title=(file.filename or f"{label}.{ext}")[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"{'更换' if old else '上传'}{label}《{row.title}》"
                       f"（{len(data) // 1024}KB）"))
    # 缺盖章立项书而停下的自动报批，补上之后下一轮清扫照常提交，不另行通知
    db.commit()
    return _file_out(row)
