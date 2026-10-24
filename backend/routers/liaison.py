"""部门里的几件事：值班交接、立项书与盖章材料、模板、志愿北京名称的外联确认
（所有活动收尾都要过的一步：时长锁定后对接部门在志愿北京核对名称，确认即完成）。

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
                      User, VbConfirm, iso, now, uid)
from ..notify import notify, notify_managers
from ..perm import ensure_activity_manager, is_activity_manager
from .staff_ops import save_vb_names, vb_names_block
from .materials import MATERIAL_DIR

router = APIRouter(prefix="/api/staff", tags=["liaison"])


def _person(u: User | None) -> dict | None:
    if u is None:
        return None
    return {"id": u.id, "name": u.name, "student_id": u.student_id,
            "post_label": u.post_label()}


# ======================= 值班 =======================

def _duty_out(db: OrmSession, dept: Department, user: User) -> dict:
    cur = liaison.on_duty(db, dept)
    heads = liaison.dept_heads(db, dept.id)
    head = heads[0] if heads else None
    rows = (db.query(DutyHandover).filter(DutyHandover.dept_id == dept.id)
            .order_by(DutyHandover.created_at.desc()).limit(30).all())

    def name(uid_):
        u = db.get(User, uid_) if uid_ else None
        return u.name if u else ""
    return {
        "dept_id": dept.id, "dept_name": dept.name,
        "on_duty": _person(cur), "head": _person(head),
        "heads": [_person(h) for h in heads],
        "head_name": "、".join(h.name for h in heads),
        "eligible": [_person(u) for u in liaison.eligible_users(db, dept)],
        # 部长（与会长团、管理员）指定；在班的人自己交班
        "can_assign": user.has_full_access() or liaison.is_head_of(user, dept),
        "can_handover": liaison.is_on_duty(cur, user),
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
                 .filter(Department.duty_enabled.is_(True))
                 .order_by(Department.sort).all()):
        if user.has_full_access() or liaison.eligible(user, dept, real=False):
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
    handing = liaison.is_on_duty(cur, user)
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
               data={"route": "approvals"})
    if cur is not None and cur.id != user.id:
        notify(db, cur, f"您的值班已结束｜{dept.name}",
               f"{user.name} 把{dept.name}的值班改为："
               f"{target.name if target else '暂无人值班'}。",
               data={"route": "approvals"})
    # 值班人自己交班时告诉部长一声；部长不收逐条审批，但得知道现在是谁在班
    for head in liaison.dept_heads(db, dept.id):
        if head.id != user.id and head.id != (target.id if target else ""):
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
    if dept.vb_liaison:
        n += (db.query(VbConfirm)
              .filter(VbConfirm.status == "pending").count())
    return n


# ======================= 志愿北京名称与外联确认 =======================

def _name(db: OrmSession, uid_: str | None) -> str:
    if uid_ == "system":
        return "系统"
    u = db.get(User, uid_) if uid_ else None
    return u.name if u else ""


def _vbc_out(db: OrmSession, r: VbConfirm) -> dict:
    return {"id": r.id, "activity_id": r.activity_id, "names": r.names,
            "kind": r.kind, "kind_label": r.kind_label(),
            "status": r.status, "status_label": r.status_label(),
            "submitted_by": _name(db, r.submitted_by),
            "submitted_at": iso(r.submitted_at),
            "handled_by": _name(db, r.handled_by), "handled_at": iso(r.handled_at),
            "reply": r.reply, "reason": r.reason}


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


def _can_resubmit(db: OrmSession, act: Activity) -> bool:
    """被外联退回、还没重提：管理者可以改名称重新提交。"""
    last = liaison.latest_vb_confirm(db, act.id)
    return (act.status == "vb_pending" and last is not None
            and last.status == "returned")


def _can_repush(act: Activity) -> bool:
    """活动已完成：管理者可以改名称再次推送给外联（志愿北京上没来得及录、
    建错了重建一个之类）。活动保持已完成。"""
    return act.status == "completed"


@router.get("/activities/{activity_id}/vb-confirms")
def activity_vb_confirms(activity_id: str, db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    """这场活动的志愿北京名称与外联确认的全部往来，新的在前。审核页签用：
    管理者、外联值班人/部长、审批线上的人都能看。"""
    act = _act(db, activity_id)
    manager = is_activity_manager(db, user, act.id)
    decider = liaison.vb_decider(db, user)
    if not (manager or decider or liaison.can_see_project_files(db, user, act)):
        raise HTTPException(403, "您无权查看该活动的志愿北京确认")
    rows = (db.query(VbConfirm).filter(VbConfirm.activity_id == act.id)
            .order_by(VbConfirm.submitted_at.desc()).all())
    writer = manager and user.can_write()
    block = vb_names_block(db, act)
    contacts = liaison.vb_contacts(db)
    dept = liaison.vb_dept(db)
    duty = liaison.on_duty(db, dept) if dept else None
    return {"names": list(act.vb_names or []), "status": act.status,
            "items": [_vbc_out(db, r) for r in rows],
            "manager": manager, "can_decide": decider,
            # 提交时长审核之前随时可以先存；锁定后只能在被退回时重提
            "can_edit": writer and not block, "edit_block": block if manager else "",
            "can_resubmit": writer and _can_resubmit(db, act),
            # 已完成之后：管理者可以改名称再推一次，活动状态不动
            "can_repush": writer and _can_repush(act),
            "handler": (f"{duty.name}（值班）" if duty
                        else "、".join(u.name for u in contacts))}


class VbNamesBody(BaseModel):
    names: list[str]


@router.put("/activities/{activity_id}/vb-names")
def save_names(activity_id: str, body: VbNamesBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """提交时长审核之前先把志愿北京活动名称存下来（申诉期满自动提交要用）。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    block = vb_names_block(db, act)
    if block:
        raise HTTPException(409, block)
    names = save_vb_names(db, act, user, body.names)
    db.commit()
    return {"ok": True, "names": names}


@router.post("/activities/{activity_id}/vb-confirms/resubmit")
def resubmit_vb(activity_id: str, body: VbNamesBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """外联退回之后，管理者改好名称重新提交给外联。只重提名称：时长保持锁定，
    不重走时长审核。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not _can_resubmit(db, act):
        raise HTTPException(409, f"只有被{liaison.vb_dept_name(db)}退回的"
                                 "志愿北京名称才需要重新提交")
    try:
        names = liaison.clean_vb_names(body.names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    before = liaison.names_text(act.vb_names)
    act.vb_names = names
    row = liaison.push_vb_confirm(db, act, user, "resubmit")
    audit_log(db, actor=user, action="vb_confirm", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"重新提交志愿北京活动名称给{liaison.vb_dept_name(db)}："
                       f"{liaison.names_text(names)}"
                       + (f"（原为 {before}）" if before != liaison.names_text(names)
                          else "（名称未改）")))
    db.commit()
    return _vbc_out(db, row)


@router.post("/activities/{activity_id}/vb-confirms/repush")
def repush_vb(activity_id: str, body: VbRepushBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """活动已完成之后，管理者改好名称再推一次给外联。

    志愿北京是时长锁定之后的附加步骤，这一步只动名称和记录，活动保持已完成：
    外联确认或退回都只落在那条记录上。没处理的旧待确认一并作废，免得外联照着
    旧名称确认。原因必填，随通知给外联：已完成的活动又推过来，外联得知道
    是志愿北京上漏录了、还是建错了要重建。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not _can_repush(act):
        raise HTTPException(409, "只有已完成的活动可以再次推送志愿北京名称")
    try:
        names = liaison.clean_vb_names(body.names)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    reason = body.reason.strip()
    if not reason:
        raise HTTPException(400,
                            f"请填写再次推送的原因，供{liaison.vb_dept_name(db)}查看")
    if len(reason) > 500:
        raise HTTPException(400, "推送原因不能超过 500 字")
    before = liaison.names_text(act.vb_names)
    dropped = liaison.void_pending_vb_confirms(db, act)
    act.vb_names = names
    row = liaison.push_vb_confirm(db, act, user, "repush", reason)
    audit_log(db, actor=user, action="vb_confirm", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"活动已完成，再次推送志愿北京活动名称给"
                       f"{liaison.vb_dept_name(db)}：{liaison.names_text(names)}"
                       + (f"（原为 {before}）" if before != liaison.names_text(names)
                          else "（名称未改）")
                       + (f"；作废 {dropped} 条待确认" if dropped else "")
                       + f"；原因：{reason}"))
    db.commit()
    return _vbc_out(db, row)


class VbRepushBody(VbNamesBody):
    reason: str = ""


class VbDecideBody(BaseModel):
    approve: bool
    reply: str = ""


@router.post("/vb-confirms/{confirm_id}/decide")
def decide_vb_confirm(confirm_id: str, body: VbDecideBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """外联在志愿北京上核对过了：确认（意见选填）或退回（必须写理由，通知
    管理者改名称重提）。

    志愿北京是时长锁定之后的附加步骤，确认和退回都只落在这条记录上，不回头
    动时长：活动正在「待外联确认」时确认才把它落「已完成」；时长解锁期间、
    或活动已完成后再次推送的，活动状态不动。
    """
    if not liaison.vb_decider(db, user):
        raise HTTPException(403, f"只有{liaison.vb_dept_name(db)}的值班人或部长"
                                 "可以确认志愿北京")
    # 两位审批人可能同时打开同一条待办。锁住记录再判状态：后一位等前一位
    # 提交后会读到最终状态，不能把「已确认」盖成「已退回」。
    r = (db.query(VbConfirm).filter(VbConfirm.id == confirm_id)
         .with_for_update().one_or_none())
    if r is None:
        raise HTTPException(404, "记录不存在")
    if r.status != "pending":
        raise HTTPException(409, f"该条{r.status_label()}")
    reply = body.reply.strip()[:512]
    if not body.approve and len(reply) < 2:
        raise HTTPException(400, "退回必须写明理由，会发给活动管理者")
    act = _act(db, r.activity_id)
    r.status = "confirmed" if body.approve else "returned"
    r.handled_by, r.handled_at, r.reply = user.id, now(), reply
    names = liaison.names_text(r.names)
    if body.approve and act.status == "vb_pending":
        act.status = "completed"
    audit_log(db, actor=user, action="vb_confirm", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=((f"外联确认志愿北京活动名称 {names}"
                        + ("，活动已完成" if act.status == "completed" else "")
                        if body.approve else f"外联退回志愿北京活动名称 {names}")
                       + (f"：{reply}" if reply else "")))
    notify_managers(
        db, act.id,
        f"志愿北京{'已确认' if body.approve else '被退回'}｜{act.title}",
        (f"{liaison.vb_dept_name(db)}已在志愿北京核对 {names}"
         + ("，「" + act.title + "」已完成。" if act.status == "completed" else "。")
         if body.approve else
         f"{liaison.vb_dept_name(db)}退回了 {names}。\n请到活动管理的「审核」页签修改志愿北京"
         f"活动名称后重新提交（时长保持锁定，不用重新审核）。")
        + (f"\n{'意见' if body.approve else '理由'}：{reply}" if reply else ""),
        duty="hours", tab="review")
    db.commit()
    return _vbc_out(db, r)


@router.get("/vb-confirms")
def pending_vb_confirms(db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """待外联确认的志愿北京名称，挂在「活动审批」页上。"""
    if not liaison.vb_decider(db, user):
        return []
    rows = (db.query(VbConfirm).filter(VbConfirm.status == "pending")
            .order_by(VbConfirm.submitted_at).all())
    out = []
    for r in rows:
        act = db.get(Activity, r.activity_id)
        # 上一次退回的理由：重提的那条，外联要看改没改到点子上
        prev = (db.query(VbConfirm)
                .filter(VbConfirm.activity_id == r.activity_id,
                        VbConfirm.status == "returned",
                        VbConfirm.submitted_at < r.submitted_at)
                .order_by(VbConfirm.submitted_at.desc()).first())
        out.append({**_vbc_out(db, r), "activity_title": act.title if act else "",
                    "organizer": act.organizer if act else "",
                    "co_organizer": (act.co_organizer or "") if act else "",
                    "organizer_text": act.organizer_text() if act else "",
                    "locked_at": iso(act.hours_locked_at) if act else "",
                    "last_return": prev.reply if prev else ""})
    return out


# ======================= 立项书与盖章材料 =======================

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
    label = liaison.SLOTS[slot]
    if not liaison.is_external(db, act):
        return f"{label}只用于归口部门要求交立项书的对外活动"
    if not liaison.is_published(act):
        return f"活动正式发布之后才能上传{label}"
    if act.hours_locked():
        return f"时长已锁定，{label}不能再更换"
    if hours_flow.pending_lock(db, act.id) is not None:
        return f"时长正在等待审核，审核期间不能更换{label}"
    return ""


@router.get("/activities/{activity_id}/project-files")
def project_files(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    act = _act(db, activity_id)
    if not liaison.can_see_project_files(db, user, act):
        raise HTTPException(403, "您无权查看该活动的立项材料")
    manager = is_activity_manager(db, user, act.id) and user.can_write()
    out = {"external": liaison.is_external(db, act), "manager": manager,
           "status": act.status}
    for slot in liaison.SLOTS:
        block = _slot_block(db, act, slot)
        out[slot] = _file_out(liaison.project_file(db, act.id, slot))
        out[f"{slot}_block"] = block
        out[f"can_upload_{slot}"] = manager and not block
        out[f"{slot}_exts"] = list(liaison.SLOT_EXTS[slot])
    return out


@router.post("/activities/{activity_id}/project-files/{slot}")
async def upload_project_file(activity_id: str, slot: str,
                              file: UploadFile = File(...),
                              db: OrmSession = Depends(db_session),
                              user: User = Depends(require_manager)):
    """上传立项书 / 盖章立项书 / 盖章结项书。每格只留一份，传新的就替换旧的。"""
    if slot not in liaison.SLOTS:
        raise HTTPException(404, "没有这一项")
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    block = _slot_block(db, act, slot)
    if block:
        raise HTTPException(409, block)
    label = liaison.SLOTS[slot]
    ext, data = await _read_upload(file, label, liaison.SLOT_EXTS[slot])
    stored = _store(data, ext)
    old = liaison.drop_project_file(db, act.id, slot)
    row = Attachment(id=uid("att"), owner_type=slot, owner_id=act.id,
                     activity_id=act.id, kind="file",
                     title=(file.filename or f"{label}.{ext}")[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"{'更换' if old else '上传'}{label}《{row.title}》"
                       f"（{len(data) // 1024}KB）"))
    # 缺盖章材料而停下的自动报批，补上之后下一轮清扫照常提交，不另行通知
    db.commit()
    return _file_out(row)


async def _read_upload(file: UploadFile, label: str,
                       allowed: tuple[str, ...]) -> tuple[str, bytes]:
    """校验上传的文件：类型、非空、大小。返回 (扩展名, 内容)。"""
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in allowed:
        raise HTTPException(400, f"{label}只能上传 {'/'.join(allowed)} 文件")
    data = await file.read()
    if not data:
        raise HTTPException(400, "文件是空的")
    if len(data) > config.MAX_MATERIAL_BYTES:
        raise HTTPException(400, f"文件不能超过 {config.MAX_MATERIAL_BYTES // (1024 * 1024)}MB")
    return ext, data


def _store(data: bytes, ext: str) -> str:
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (MATERIAL_DIR / stored).write_bytes(data)
    return stored


# ======================= 立项书、结项书模板 =======================

@router.get("/doc-templates")
def doc_templates(db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """模板清单。发起方下载了照着填；外联部长（会长团、管理员）能上传和更换。"""
    return {"can_manage": liaison.can_manage_templates(db, user) and user.can_write(),
            "exts": list(liaison.DOC_TEMPLATE_EXTS),
            "items": [{"slot": slot, "label": label,
                       "file": _file_out(liaison.doc_template(db, slot))}
                      for slot, label in liaison.DOC_TEMPLATES.items()]}


@router.post("/doc-templates/{slot}")
async def upload_doc_template(slot: str, file: UploadFile = File(...),
                              db: OrmSession = Depends(db_session),
                              user: User = Depends(require_manager)):
    """上传或更换一份模板。每种只留一份，传新的就替换旧的。"""
    if slot not in liaison.DOC_TEMPLATES:
        raise HTTPException(404, "没有这一项")
    if not liaison.can_manage_templates(db, user):
        raise HTTPException(403, f"模板由{liaison.vb_dept_name(db)}部长更换")
    label = liaison.DOC_TEMPLATES[slot]
    ext, data = await _read_upload(file, label, liaison.DOC_TEMPLATE_EXTS)
    stored = _store(data, ext)
    old = liaison.drop_attachment(db, liaison.doc_template(db, slot))
    row = Attachment(id=uid("att"), owner_type="template", owner_id=slot,
                     activity_id="", kind="file",
                     title=(file.filename or f"{label}.{ext}")[:160],
                     stored_name=stored, size=len(data), uploaded_by=user.id)
    db.add(row)
    audit_log(db, actor=user, action="material", target_type="template",
              target_id=slot, target_label=label,
              summary=f"{'更换' if old else '上传'}{label}《{row.title}》"
                      f"（{len(data) // 1024}KB）")
    db.commit()
    return _file_out(row)
