"""活动审批：发起方提交 → 校青协部长审批 → 通过后由发起方自己发布。

状态流：
    草稿 →[提交]→ 待审批 →[通过]→ 待发布 →[发布]→ 报名中 …
                     ↓[驳回，带意见]
                    草稿

当前审批人挂在 Activity 上（一条活动只有一条正在进行的审批），转交、通过、
驳回各留一条 ApprovalEvent。审批**永不自动通过**：没人看就一直等着，
超时只催人，不放行。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user, require_admin, require_manager
from ..integrations import wecom
from ..models import (Activity, ApprovalEvent, Department, User, iso, now, uid)
from ..perm import ensure_activity_manager

router = APIRouter(prefix="/api/staff", tags=["approvals"])

# 12 个部门；对外联络部置顶，二级团体归它。种子数据与首次启动都用这份。
DEFAULT_DEPARTMENTS = [
    ("对外联络部", "【二级志愿团体请选择此部门审批】"),
    ("办公室", ""), ("新媒体技术部", ""), ("组织部", ""),
    ("支教项目部", ""), ("红十字项目部", ""), ("环保项目部", ""),
    ("青春健康项目部", ""), ("社区服务部", ""), ("特殊关爱项目部", ""),
    ("校园服务部", ""), ("首都服务部", ""),
]


def ensure_departments(db: OrmSession) -> None:
    """首次启动把 12 个部门建出来。已存在的不动，改过的名字也不覆盖。"""
    if db.query(Department).count():
        return
    for i, (name, note) in enumerate(DEFAULT_DEPARTMENTS):
        db.add(Department(id=uid("dep"), name=name, note=note, sort=i))
    db.commit()


def _dept_head(db: OrmSession, dept_id: str) -> User | None:
    return (db.query(User)
            .filter(User.department_id == dept_id,
                    User.qingxie_post == "head").first())


def _presidents(db: OrmSession) -> list[User]:
    return db.query(User).filter(User.qingxie_post == "president").all()


def _can_decide(db: OrmSession, user: User, act: Activity) -> bool:
    """能不能拍这一条：当前审批人、会长、或平台管理员。"""
    return (act.approval_assignee_id == user.id
            or user.is_president() or user.is_admin())


def can_review(db: OrmSession, user: User, act: Activity) -> bool:
    """能不能**看**这条活动的完整安排（审批线上的人）。

    比 _can_decide 多一类：批过或驳过这条的人。活动通过之后到发起方点发布
    之前还是不公开的，审批人回头想核对自己批了什么，不该被挡在门外。
    """
    if _can_decide(db, user, act):
        return True
    return db.query(ApprovalEvent).filter(
        ApprovalEvent.activity_id == act.id,
        ApprovalEvent.actor_id == user.id).first() is not None


def _event(db: OrmSession, act: Activity, kind: str, actor: User, **kw) -> None:
    db.add(ApprovalEvent(id=uid("apv"), activity_id=act.id, kind=kind,
                         actor_id=actor.id, **kw))


def _notify(db: OrmSession, who: User | None, title: str, content: str,
            act: Activity, *, need_ack: bool = False) -> None:
    if who is None:
        return
    wecom.send_user(db, who, "custom", data={"activity_id": act.id},
                    title=title, content=content, need_ack=need_ack,
                    batch_id=uid("bat") if need_ack else None)


# ======================= 部门维护 =======================

@router.get("/departments")
def list_departments(db: OrmSession = Depends(db_session),
                     user: User = Depends(require_manager)):
    """部门下拉。带上部长姓名，提交时看得见这条会交到谁手上。"""
    ensure_departments(db)
    out = []
    for d in db.query(Department).order_by(Department.sort).all():
        head = _dept_head(db, d.id)
        out.append({"id": d.id, "name": d.name, "note": d.note,
                    "head_name": head.name if head else "",
                    "has_head": head is not None})
    return out


class DeptBody(BaseModel):
    name: str = ""
    note: str = ""
    head_student_id: str | None = None      # 指定部长；空串=清空


@router.patch("/departments/{dept_id}")
def update_department(dept_id: str, body: DeptBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_admin)):
    """改部门名/说明、指定部长。换届时用。"""
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(404, "部门不存在")
    changed = []
    if body.name.strip() and body.name.strip() != d.name:
        changed.append(f"名称 {d.name}→{body.name.strip()}")
        d.name = body.name.strip()
    if body.note.strip() != d.note:
        d.note = body.note.strip()
        changed.append("说明")
    if body.head_student_id is not None:
        for old in db.query(User).filter(User.department_id == d.id,
                                         User.qingxie_post == "head").all():
            old.qingxie_post = "member"
        sid = body.head_student_id.strip()
        if sid:
            who = db.query(User).filter(User.student_id == sid).first()
            if who is None:
                raise HTTPException(404, f"学号 {sid} 不存在")
            if not who.has_role("qingxie"):
                raise HTTPException(400, f"{who.name} 不是青协身份，不能当部长")
            who.department_id = d.id
            who.qingxie_post = "head"
            changed.append(f"部长→{who.name}")
        else:
            changed.append("清空部长")
    audit_log(db, actor=user, action="update", target_type="department",
              target_id=d.id, target_label=d.name, activity_id=None,
              summary=f"修改部门「{d.name}」：{'、'.join(changed) or '无变化'}")
    db.commit()
    return {"ok": True}


# ======================= 提交与审批 =======================

@router.get("/activities/{activity_id}/submit-check")
def submit_check(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """结构够不够格提交。前端在**点「提交审批」按钮时**先问这一下——
    让人选完部门、写完备注才被拦，白填一遍。"""
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "draft":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能提交审批")
    from .staff_activities import ensure_ready_to_publish
    ensure_ready_to_publish(act)
    return {"ok": True}


class SubmitBody(BaseModel):
    department_id: str
    note: str = ""


@router.post("/activities/{activity_id}/submit")
def submit(activity_id: str, body: SubmitBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """提交审批。草稿或被驳回的活动都走这里。"""
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "draft":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能提交审批")
    from .staff_activities import ensure_ready_to_publish
    ensure_ready_to_publish(act)
    dept = db.get(Department, body.department_id)
    if dept is None:
        raise HTTPException(400, "请选择审批部门")
    # 自己部门的活动不能自己批；会长的活动没人能批，允许自批但留痕
    if (user.has_role("qingxie") and user.department_id == dept.id
            and not user.is_president()):
        raise HTTPException(400, "不能选择自己所在的部门审批，请另选一个部门")
    head = _dept_head(db, dept.id)
    if head is None:
        raise HTTPException(400, f"「{dept.name}」还没有指定部长，请联系管理员")

    act.status = "pending"
    act.approval_dept_id = dept.id
    act.approval_assignee_id = head.id
    act.approval_comment = ""
    act.submitted_at = now()
    act.approval_reminded_at = None
    _event(db, act, "submit", user, dept_id=dept.id, to_user_id=head.id,
           comment=body.note.strip())
    audit_log(db, actor=user, action="submit", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"提交「{dept.name}」审批（审批人 {head.name}）")
    _notify(db, head, f"待审批｜{act.title}",
            f"{user.name} 提交了「{act.title}」，组织单位：{act.organizer}。\n"
            f"归口部门：{dept.name}。\n"
            + (f"备注：{body.note.strip()}\n" if body.note.strip() else "")
            + "请到「活动审批」处理。", act, need_ack=True)
    db.commit()
    return {"ok": True, "assignee": head.name}


class DecideBody(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/activities/{activity_id}/decide")
def decide(activity_id: str, body: DecideBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(current_user)):
    """通过或驳回。通过后活动回到发起方手里，由他自己点发布。"""
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "pending":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，无需审批")
    if not _can_decide(db, user, act):
        raise HTTPException(403, "这条审批不在您名下")
    comment = body.comment.strip()
    if not body.approve and len(comment) < 2:
        raise HTTPException(400, "驳回必须写明理由，发起方才知道改什么")

    act.approval_comment = comment
    if body.approve:
        act.status = "approved"
        act.approved_at = now()
        _event(db, act, "approve", user, comment=comment)
        summary = f"审批通过（{user.name}）" + (f"：{comment}" if comment else "")
        title, body_text = (
            f"审批通过｜{act.title}",
            f"「{act.title}」已通过校青协审批，现在可以在活动管理页点「发布」"
            f"开始报名。" + (f"\n审批意见：{comment}" if comment else ""))
    else:
        act.status = "draft"
        act.approval_assignee_id = None
        _event(db, act, "reject", user, comment=comment)
        summary = f"审批驳回（{user.name}）：{comment}"
        title, body_text = (
            f"审批未通过｜{act.title}",
            f"「{act.title}」未通过校青协审批。\n意见：{comment}\n"
            f"修改后可以重新提交。")
    audit_log(db, actor=user, action="approve" if body.approve else "reject",
              target_type="activity", target_id=act.id, target_label=act.title,
              activity_id=act.id, summary=summary)
    # 通知全部管理者：发起方可能不止一个人
    from ..models import ActivityManager
    for row in db.query(ActivityManager).filter(
            ActivityManager.activity_id == act.id).all():
        _notify(db, db.get(User, row.user_id), title, body_text, act)
    db.commit()
    return {"ok": True, "status": act.status}


class TransferBody(BaseModel):
    department_id: str = ""
    student_id: str = ""
    reason: str


@router.post("/activities/{activity_id}/transfer-approval")
def transfer(activity_id: str, body: TransferBody,
             db: OrmSession = Depends(db_session),
             user: User = Depends(current_user)):
    """把审批转交给别的部门或别的人。必须写理由，三方都会收到通知。"""
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "pending":
        raise HTTPException(409, "只有待审批的活动可以转交")
    if not _can_decide(db, user, act):
        raise HTTPException(403, "这条审批不在您名下")
    reason = body.reason.strip()
    if len(reason) < 2:
        raise HTTPException(400, "请写明转交理由")

    dept = act.approval_dept_id and db.get(Department, act.approval_dept_id)
    if body.student_id.strip():
        target = db.query(User).filter(
            User.student_id == body.student_id.strip()).first()
        if target is None:
            raise HTTPException(404, "该学号不存在")
        if not target.has_role("qingxie"):
            raise HTTPException(400, f"{target.name} 不是青协身份，不能审批")
        if target.department_id:
            dept = db.get(Department, target.department_id)
    elif body.department_id:
        dept = db.get(Department, body.department_id)
        if dept is None:
            raise HTTPException(400, "目标部门不存在")
        target = _dept_head(db, dept.id)
        if target is None:
            raise HTTPException(400, f"「{dept.name}」还没有指定部长")
    else:
        raise HTTPException(400, "请选择要转交的部门或指定审批人")
    if target.id == act.approval_assignee_id:
        raise HTTPException(400, "转交对象就是当前审批人")

    before = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    act.approval_dept_id = dept.id if dept else None
    act.approval_assignee_id = target.id
    act.approval_reminded_at = None
    _event(db, act, "transfer", user, dept_id=dept.id if dept else None,
           to_user_id=target.id, comment=reason)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"审批转交：{before.name if before else '—'} → {target.name}"
                      f"（{dept.name if dept else '未归口'}）；理由：{reason}")
    _notify(db, target, f"转来一条待审批｜{act.title}",
            f"{user.name} 把「{act.title}」的审批转交给您。\n理由：{reason}",
            act, need_ack=True)
    _notify(db, before, f"审批已转出｜{act.title}",
            f"您名下的「{act.title}」已由 {user.name} 转交给 {target.name}。\n"
            f"理由：{reason}", act)
    from ..models import ActivityManager
    for row in db.query(ActivityManager).filter(
            ActivityManager.activity_id == act.id).all():
        _notify(db, db.get(User, row.user_id), f"审批已转交｜{act.title}",
                f"「{act.title}」的审批已转交给 {target.name}"
                f"（{dept.name if dept else '未归口'}）。", act)
    db.commit()
    return {"ok": True, "assignee": target.name}


@router.get("/approvals")
def my_approvals(db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """待我审批的活动。会长与管理员看全部待审批。"""
    q = db.query(Activity).filter(Activity.status == "pending")
    if not (user.is_president() or user.is_admin()):
        q = q.filter(Activity.approval_assignee_id == user.id)
    out = []
    for act in q.order_by(Activity.submitted_at).all():
        dept = db.get(Department, act.approval_dept_id) if act.approval_dept_id else None
        who = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
        creator = db.get(User, act.created_by)
        span = act.time_span()
        # 提交时写的说明。以前只存进流水、发进通知，审批人做决定的这个页面
        # 反而看不到——最该看到那句话的地方。
        submit = (db.query(ApprovalEvent)
                  .filter(ApprovalEvent.activity_id == act.id,
                          ApprovalEvent.kind == "submit")
                  .order_by(ApprovalEvent.created_at.desc()).first())
        out.append({
            "id": act.id, "title": act.title, "organizer": act.organizer,
            "type_label": f"{act.type}类", "location": act.location,
            "time_text": (f"{span[0]:%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}"
                          if span else "待定"),
            "shifts_count": len(act.main_shifts()),
            "submitted_at": iso(act.submitted_at),
            "department": dept.name if dept else "",
            "assignee": who.name if who else "",
            "mine": act.approval_assignee_id == user.id,
            "creator": creator.name if creator else "",
            "submit_note": submit.comment if submit else "",
        })
    return out


@router.get("/activities/{activity_id}/approval")
def approval_detail(activity_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """一条活动的审批流水，管理者与审批人都能看。"""
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if not (_can_decide(db, user, act) or user.is_admin()):
        ensure_activity_manager(db, user, activity_id)
    rows = (db.query(ApprovalEvent)
            .filter(ApprovalEvent.activity_id == activity_id)
            .order_by(ApprovalEvent.created_at).all())
    dept = db.get(Department, act.approval_dept_id) if act.approval_dept_id else None
    who = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    return {
        "status": act.status, "status_label": act.label_status(),
        "department": dept.name if dept else "",
        "assignee": who.name if who else "",
        "can_decide": _can_decide(db, user, act) and act.status == "pending",
        "comment": act.approval_comment,
        "events": [{
            "kind": e.kind, "kind_label": e.kind_label(),
            "actor": (db.get(User, e.actor_id).name
                      if db.get(User, e.actor_id) else "?"),
            "to": (db.get(User, e.to_user_id).name
                   if e.to_user_id and db.get(User, e.to_user_id) else ""),
            "comment": e.comment, "created_at": iso(e.created_at),
        } for e in rows],
    }
