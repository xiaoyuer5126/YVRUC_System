"""活动审批：发起方提交 → 校青协部长审批 → 通过即发布（可在提交时关掉）。

状态流：
    草稿 →[提交]→ 待审批 →[通过]→ 待发布 →[发布]→ 报名中 …
                     ↓[驳回，带意见]
                    草稿

当前审批人挂在 Activity 上（一条活动只有一条正在进行的审批），转交、通过、
驳回各留一条 ApprovalEvent。审批**永不自动通过**：没人看就一直等着，
超时只催人，不放行。

对外联络部启用值班（见 liaison.py）：审批人仍挂部长，但通知发给值班的人，
值班人与部长都能拍这个部门的审批；没人值班就回落给部长。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import liaison
from ..audit import log as audit_log
from ..database import db_session
from ..deps import (current_user, require_approver, require_council,
                    require_manager)
from ..integrations import msgcenter
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
    """建库时把 12 个部门铺进去，由 seed 调用一次。

    之后部门的增删改都在页面上做（会长团 / 管理员），所以这里不能在每次
    读列表时兜底——真把某个部门删了，下次打开页面它又长回来。
    """
    if db.query(Department).count():
        return
    for i, (name, note) in enumerate(DEFAULT_DEPARTMENTS):
        db.add(Department(id=uid("dep"), name=name, note=note, sort=i))
    db.commit()


_dept_head = liaison.dept_head


def _in_dept(db: OrmSession, act: Activity) -> bool:
    """这条审批眼下还在归口部门手里：审批人是本部门的人。
    转给会长团（没有部门）时归口不动，但事情已经交出去了，部门不再插手。"""
    who = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    return who is not None and who.department_id == act.approval_dept_id


def _can_decide(db: OrmSession, user: User, act: Activity) -> bool:
    """能不能拍这一条：当前审批人、会长团、或平台管理员；启用值班的部门
    另加值班人与部长（转给部门里某个人的也一样，部长随时能直接拍）。"""
    if act.approval_assignee_id == user.id or user.has_full_access():
        return True
    return (liaison.is_dept_handler(db, user, act.approval_dept_id)
            and _in_dept(db, act))


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
            act: Activity, *, need_ack: bool = False,
            route: str = "") -> None:
    """route 指明这条消息点开该去哪儿：待办的审批要进审批页，
    不是进活动详情——审批人在那儿看不到通过/驳回的按钮。"""
    if who is None:
        return
    data = {"activity_id": act.id}
    if route:
        data["route"] = route
    msgcenter.send_user(db, who, "custom", data=data,
                    title=title, content=content, need_ack=need_ack,
                    batch_id=uid("bat") if need_ack else None)


# ======================= 部门维护 =======================

@router.get("/departments")
def list_departments(db: OrmSession = Depends(db_session),
                     user: User = Depends(require_manager)):
    """部门下拉。带上部长姓名，提交时看得见这条会交到谁手上。"""
    out = []
    for d in db.query(Department).order_by(Department.sort).all():
        head = _dept_head(db, d.id)
        out.append({"id": d.id, "name": d.name, "note": d.note,
                    "head_name": head.name if head else "",
                    "has_head": head is not None})
    return out


class DeptCreateBody(BaseModel):
    name: str
    note: str = ""


@router.post("/departments")
def create_department(body: DeptCreateBody, db: OrmSession = Depends(db_session),
                      user: User = Depends(require_council)):
    """新增部门。部长另行指定——没有部长的部门不能被选为归口。"""
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "请填写部门名称")
    if db.query(Department).filter(Department.name == name).first():
        raise HTTPException(409, f"已经有一个部门叫「{name}」")
    last = db.query(Department).order_by(Department.sort.desc()).first()
    d = Department(id=uid("dep"), name=name, note=body.note.strip(),
                   sort=(last.sort + 1) if last else 0)
    db.add(d)
    audit_log(db, actor=user, action="create", target_type="department",
              target_id=d.id, target_label=d.name, activity_id=None,
              summary=f"新增部门「{d.name}」")
    db.commit()
    return {"id": d.id}


@router.delete("/departments/{dept_id}")
def delete_department(dept_id: str, db: OrmSession = Depends(db_session),
                      user: User = Depends(require_council)):
    """删除部门。名下还有人或还挂着活动的不给删——历史活动的归口不能指空。"""
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(404, "部门不存在")
    people = db.query(User).filter(User.department_id == d.id).count()
    if people:
        raise HTTPException(409, f"「{d.name}」名下还有 {people} 人，"
                                 "请先在角色管理里把他们转到别的部门")
    acts = db.query(Activity).filter(Activity.approval_dept_id == d.id).count()
    if acts:
        raise HTTPException(409, f"「{d.name}」归口着 {acts} 场活动，不能删除")
    audit_log(db, actor=user, action="delete", target_type="department",
              target_id=d.id, target_label=d.name, activity_id=None,
              summary=f"删除部门「{d.name}」")
    db.delete(d)
    db.commit()
    return {"ok": True}


class DeptBody(BaseModel):
    name: str = ""
    note: str = ""
    head_student_id: str | None = None      # 指定部长；空串=清空


@router.patch("/departments/{dept_id}")
def update_department(dept_id: str, body: DeptBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_council)):
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
            if not who.has_role("qingxie", real=True):
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
    department_id: str = ""      # 留空 = 沿用活动的归口部门
    note: str = ""
    auto_publish: bool = True    # 通过即发布；不勾就停在「待发布」


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
    # 归口部门：这活动归哪个部门，就由那个部门的部长审。青协的人建活动时
    # 已经按所在部门预填了，提交时可以改；发起方建的在这里选。
    dept_id = body.department_id.strip() or act.approval_dept_id or ""
    dept = db.get(Department, dept_id) if dept_id else None
    if dept is None:
        raise HTTPException(400, "请选择归口部门")
    head = _dept_head(db, dept.id)
    if head is None:
        raise HTTPException(400, f"「{dept.name}」还没有指定部长，请联系管理员")
    # 对外活动要带立项书：审批人看的就是这份东西，没有它批不了
    if dept.name == liaison.LIAISON_DEPT \
            and liaison.project_file(db, act.id, "proposal") is None:
        raise HTTPException(400, f"提交「{dept.name}」审批须先上传立项书")
    # 审批人仍挂部长（部门的责任人），通知发给值班的人
    contact = liaison.approval_contact(db, dept.id) or head
    via = f"，值班 {contact.name}" if contact.id != head.id else ""

    act.status = "pending"
    act.auto_publish = body.auto_publish
    act.approval_dept_id = dept.id
    act.approval_assignee_id = head.id
    act.approval_comment = ""
    act.submitted_at = now()
    act.approval_reminded_at = None
    _event(db, act, "submit", user, dept_id=dept.id, to_user_id=head.id,
           comment=body.note.strip())
    audit_log(db, actor=user, action="submit", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"提交「{dept.name}」审批（审批人 {head.name}{via}）")
    _notify(db, contact, f"待审批｜{act.title}",
            f"{user.name} 提交了「{act.title}」，组织单位：{act.organizer}。\n"
            f"归口部门：{dept.name}。\n"
            + (f"备注：{body.note.strip()}\n" if body.note.strip() else "")
            + "请到「活动审批」处理。", act, need_ack=True, route="approvals")
    db.commit()
    return {"ok": True, "assignee": contact.name}


class DecideBody(BaseModel):
    approve: bool
    comment: str = ""


@router.post("/activities/{activity_id}/decide")
def decide(activity_id: str, body: DecideBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """通过或驳回。

    通过即发布：报名什么时候开放由「报名开始时间」决定，发布只是让活动出现在
    广场上，多一步手动发布只是让发起方再登录一次点个按钮。提交审批时可以关掉
    这一项，那就停在「待发布」等发起方自己点。
    """
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "pending":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，无需审批")
    if not _can_decide(db, user, act):
        raise HTTPException(403, "这条审批不在您名下")
    comment = body.comment.strip()
    if not body.approve and len(comment) < 2:
        raise HTTPException(400, "驳回必须写明理由，会发给发起方")
    # 转交进对外联络部的活动可能还没传立项书：批之前得有
    if body.approve and liaison.is_external(db, act) \
            and liaison.project_file(db, act.id, "proposal") is None:
        raise HTTPException(400, "这场对外活动还没有上传立项书，请驳回让发起方补传")

    # 部长自己发起的活动归口自己部门、由自己审——允许，但流水里标明是本人
    submit = (db.query(ApprovalEvent)
              .filter(ApprovalEvent.activity_id == act.id,
                      ApprovalEvent.kind == "submit")
              .order_by(ApprovalEvent.created_at.desc()).first())
    self_review = submit is not None and submit.actor_id == user.id
    who = f"{user.name}·本人审批" if self_review else user.name
    if self_review:
        comment = f"【本人审批】{comment}" if comment else "【本人审批】"

    act.approval_comment = comment
    if body.approve and act.is_record():
        # 补记录：活动线下已经办完，不发布、不招人，通过即「已结束」，
        # 接下来就是导入名单、结时长——和别的活动结束之后走同一条路
        act.status = "finished"
        act.approved_at = now()
        _event(db, act, "approve", user, comment=comment)
        summary = f"审批通过（{who}），补记录活动可以导入名单" + (
            f"：{comment}" if comment else "")
        title, body_text = (
            f"审批通过｜{act.title}",
            f"「{act.title}」已通过校青协审批。请在活动管理页「名单导入」"
            f"导入参与者的学号与时长，再按时长管理的流程核准、提交审核。"
            + (f"\n审批意见：{comment}" if comment else ""))
    elif body.approve:
        act.status = "approved"
        act.approved_at = now()
        _event(db, act, "approve", user, comment=comment)
        published = False
        if act.auto_publish:
            from .staff_activities import publish_approved
            publish_approved(db, act, user)
            published = True
        summary = (f"审批通过（{who}）" + ("并发布" if published else "")
                   + (f"：{comment}" if comment else ""))
        title, body_text = (
            f"审批通过｜{act.title}",
            f"「{act.title}」已通过校青协审批"
            + (f"并发布，报名于 {iso(act.reg_start)} 开始。" if published
               else "，请在活动管理页点「发布」开始报名。")
            + (f"\n审批意见：{comment}" if comment else ""))
    else:
        act.status = "draft"
        act.approval_assignee_id = None
        _event(db, act, "reject", user, comment=comment)
        summary = f"审批驳回（{who}）：{comment}"
        # 驳回即作废立项书：改完重交要带新的一份，免得旧版本混过去
        dropped = liaison.drop_project_file(db, act.id, "proposal")
        if dropped is not None:
            summary += f"；立项书《{dropped.title}》作废"
        title, body_text = (
            f"审批未通过｜{act.title}",
            f"「{act.title}」未通过校青协审批。\n意见：{comment}\n"
            f"修改后可以重新提交。"
            + ("\n原立项书已作废，重新提交时请上传新的立项书。"
               if dropped is not None else ""))
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
             user: User = Depends(require_manager)):
    """把审批转交给别的部门或别的人。必须写理由，三方都会收到通知。

    转交审批就是改归口：转给哪个部门（或哪个部门的人），活动就归那个部门，
    部长的活动列表、部门成员的可见范围都跟着变。转给会长团（没有部门）时
    归口不动，只是换个人拍板。
    """
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
        # 转给谁：看对方的真实身份（可能是当前用户本人）
        if not target.has_role("qingxie", real=True):
            raise HTTPException(400, f"{target.name} 不是青协身份，不能审批")
        if not target.can_write(real=True):
            raise HTTPException(400, f"{target.name} 是部门成员（学习权限），不能审批")
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
    # 转给部门的（审批人落在部长身上）通知该部门的值班人；点名转给某人的就是他
    contact = target
    if not body.student_id.strip() and dept is not None:
        contact = liaison.approval_contact(db, dept.id) or target
    _notify(db, contact, f"转来一条待审批｜{act.title}",
            f"{user.name} 把「{act.title}」的审批转交给您。\n理由：{reason}",
            act, need_ack=True, route="approvals")
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
    return {"ok": True, "assignee": contact.name}


@router.get("/approvals")
def my_approvals(db: OrmSession = Depends(db_session),
                 user: User = Depends(require_approver)):
    """待我审批的活动。会长团与管理员看全部待审批，部长看自己名下的；
    启用值班的部门，值班人与部长看这个部门的全部。"""
    from sqlalchemy import or_
    q = db.query(Activity).filter(Activity.status == "pending")
    if not user.has_full_access():
        depts = liaison.handler_dept_ids(db, user)
        q = q.filter(or_(Activity.approval_assignee_id == user.id,
                         Activity.approval_dept_id.in_(depts)))
    out = []
    for act in q.order_by(Activity.submitted_at).all():
        if not _can_decide(db, user, act):
            continue
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
        duty = liaison.on_duty(db, dept) if dept else None
        proposal = liaison.project_file(db, act.id, "proposal")
        out.append({
            "id": act.id, "title": act.title, "organizer": act.organizer,
            "recruit_label": act.recruit_label(), "location": act.location,
            "time_text": (f"{span[0]:%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}"
                          if span else "待定"),
            "shifts_count": len(act.main_shifts()),
            "record_only": act.is_record(),
            "submitted_at": iso(act.submitted_at),
            "department": dept.name if dept else "",
            "assignee": who.name if who else "",
            # 在我名下（会长团接管别人名下的那种不算）：前端据此标「当前审批人」
            "mine": (act.approval_assignee_id == user.id
                     or (liaison.is_dept_handler(db, user, act.approval_dept_id)
                         and _in_dept(db, act))),
            "on_duty": duty.name if duty else "",
            "creator": creator.name if creator else "",
            "submit_note": submit.comment if submit else "",
            # 对外活动的立项书：审批人就在这一屏拍板，有没有、点开看都在这儿
            "external": bool(dept and dept.name == liaison.LIAISON_DEPT),
            "proposal": ({"id": proposal.id, "title": proposal.title}
                         if proposal else None),
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
        # 启用值班的部门：通知实际发给了谁（管理者看「等谁处理」要看这个）
        "on_duty": (d.name if (d := liaison.on_duty(db, dept)) else ""),
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
