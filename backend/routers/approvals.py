"""活动审批：发起方提交 → 校青协部长审批 → 通过即发布（可在提交时关掉）。

状态流：
    草稿 →[提交]→ 待审批 →[通过]→ 待发布 →[发布]→ 报名中 …
                     ↓[驳回，带意见]
                    草稿

当前审批人挂在 Activity 上（一条活动只有一条正在进行的审批），转交、通过、
驳回各留一条 ApprovalEvent。审批**永不自动通过**：没人看就一直等着，
超时只催人，不放行。

启用值班的部门（见 liaison.py）：审批人仍挂部长，但通知发给值班的人，
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
from ..models import (Activity, ApprovalEvent, Department, DutyHandover, User,
                      iso, now, uid)
from ..perm import ensure_activity_manager
from ..serializers import published_hint
from .roles import _release_members

router = APIRouter(prefix="/api/staff", tags=["approvals"])

_dept_head = liaison.dept_head

# 提交审批时归口选它：归口存空，按内部活动审批，不走部门部长，也不收立项书
COUNCIL_DEPT = "council"


def _council_contacts(db: OrmSession) -> list[User]:
    """会长团审批的待办通知对象：标了会长只发他；没标（换届空档）发全体会长团。
    其他会长团成员照样能在审批页看到并批。"""
    from .staff_ops import council_members
    rows = council_members(db)
    return [u for u in rows if u.is_president] or rows


def _dept_text(act: Activity, dept: Department | None) -> str:
    """归口怎么说。归口存空 = 会长团（见 submit）；还没提交过的草稿不显示。"""
    if dept is not None:
        return dept.name
    return ("会长团" if act.approval_dept_id is None
            and (act.status != "draft" or act.submitted_at) else "")


def assignee_text(db: OrmSession, act: Activity) -> str:
    """「等谁审批」显示的名字。审批挂在归口部门的部长名下时，本部门任一部长都能拍，
    列出全部在岗部长（顿号连接）；只写挂名的那一位，别的部长会以为这事不归自己。"""
    who = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    if who is None:
        # 会长团名下没有具体审批人：显示整个组织，不能留白
        return ("会长团" if act.approval_dept_id is None
                and (act.status != "draft" or act.submitted_at) else "")
    if (act.approval_dept_id and who.department_id == act.approval_dept_id
            and who.qingxie_post == "head"):
        heads = liaison.dept_heads(db, act.approval_dept_id)
        if heads:
            return "、".join(h.name for h in heads)
    return who.name


def _in_dept(db: OrmSession, act: Activity) -> bool:
    """这条审批眼下还在归口部门手里：审批人是本部门的人。
    转给会长团（没有部门）时归口不动，但事情已经交出去了，部门不再插手。"""
    who = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    return who is not None and who.department_id == act.approval_dept_id


def _can_decide(db: OrmSession, user: User, act: Activity) -> bool:
    """能不能拍这一条：当前审批人、本部门任一部长、会长团、或平台管理员；启用值班的部门
    另加值班人与部长（转给部门里某个人的也一样，部长随时能直接拍）。"""
    if act.approval_assignee_id == user.id or user.has_full_access():
        return True
    dept = db.get(Department, act.approval_dept_id) if act.approval_dept_id else None
    if liaison.is_head_of(user, dept) and _in_dept(db, act):
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


def _event_to(db: OrmSession, e: ApprovalEvent, dept: Department | None,
              dept_names: dict[str, str]) -> str:
    """流水里「交给谁」怎么写。提交写归口（空 = 会长团），转交写具体人；
    转回会长团没有具体人，写会长团。"""
    if e.kind == "submit":
        return dept_names.get(e.dept_id, "") if e.dept_id else "会长团"
    if e.to_user_id:
        who = db.get(User, e.to_user_id)
        return who.name if who else ""
    if e.kind == "transfer" and e.dept_id is None:
        return "会长团"
    return ""


def _event(db: OrmSession, act: Activity, kind: str, actor: User, **kw) -> None:
    db.add(ApprovalEvent(id=uid("apv"), activity_id=act.id, kind=kind,
                         actor_id=actor.id, **kw))


def _notify(db: OrmSession, who: User | None, title: str, content: str,
            act: Activity, *, route: str = "", tab: str = "") -> None:
    """route 指明这条消息点开该去哪儿：待办的审批要进审批页，
    不是进活动详情——审批人在那儿看不到通过/驳回的按钮；审批结果发给
    活动管理者，进活动管理页（route="manage"，tab 指定页签）。"""
    if who is None:
        return
    data = {"activity_id": act.id}
    if route:
        data["route"] = route
    if tab:
        data["tab"] = tab
    msgcenter.send_user(db, who, "custom", data=data,
                    title=title, content=content)


# ======================= 部门维护 =======================

@router.get("/departments")
def list_departments(db: OrmSession = Depends(db_session),
                     user: User = Depends(require_manager)):
    """部门下拉。带上部长姓名，提交时看得见这条会交到谁手上。"""
    out = []
    for d in db.query(Department).order_by(Department.sort).all():
        heads = liaison.dept_heads(db, d.id)
        out.append({
            "id": d.id, "name": d.name, "note": d.note,
            "head_name": "、".join(h.name for h in heads),
            "heads": [{"id": h.id, "student_id": h.student_id, "name": h.name}
                      for h in heads],
            "has_head": bool(heads),
            "duty_enabled": d.duty_enabled,
            "external": d.external,
            "vb_liaison": d.vb_liaison,
        })
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
    if d.vb_liaison:
        raise HTTPException(409, f"「{d.name}」是志愿北京对接部门，"
                                 "请先把对接部门换成别的部门再删除")
    # 会长团的催办范围里去掉这个部门；去掉后空了就是接收全部部门
    for u in db.query(User).filter(User.qingxie_post == "council").all():
        if d.id in (u.remind_dept_ids or []):
            u.remind_dept_ids = [x for x in u.remind_dept_ids if x != d.id]
    audit_log(db, actor=user, action="delete", target_type="department",
              target_id=d.id, target_label=d.name, activity_id=None,
              summary=f"删除部门「{d.name}」")
    db.delete(d)
    db.commit()
    return {"ok": True}


class DeptBody(BaseModel):
    name: str = ""
    note: str | None = None                     # 不传就不动说明
    head_student_id: str | None = None          # 新增/指定部长；空串=清空全部部长
    remove_head_student_id: str | None = None   # 移除某位部长（降为部门成员）
    duty_enabled: bool | None = None            # 不传就不动
    external: bool | None = None                # 不传就不动
    vb_liaison: bool | None = None              # 只能设为 True；换部门时把旧的清掉


@router.patch("/departments/{dept_id}")
def update_department(dept_id: str, body: DeptBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_council)):
    """改部门名/说明、增删部长、开关部门能力。支持一个部门有多位部长。"""
    d = db.get(Department, dept_id)
    if d is None:
        raise HTTPException(404, "部门不存在")
    changed = []
    if body.name.strip() and body.name.strip() != d.name:
        changed.append(f"名称 {d.name}→{body.name.strip()}")
        d.name = body.name.strip()
    if body.note is not None and body.note.strip() != d.note:
        d.note = body.note.strip()
        changed.append("说明")
    if body.remove_head_student_id is not None:
        rm_sid = body.remove_head_student_id.strip()
        if rm_sid:
            who = db.query(User).filter(
                User.student_id == rm_sid,
                User.department_id == d.id,
                User.qingxie_post == "head",
            ).first()
            if who is None:
                raise HTTPException(404, f"该部门未找到学号为 {rm_sid} 的部长")
            who.qingxie_post = "member"
            changed.append(f"移除部长 {who.name}")
    if body.head_student_id is not None:
        sid = body.head_student_id.strip()
        if sid:
            who = db.query(User).filter(User.student_id == sid).first()
            if who is None:
                raise HTTPException(404, f"学号 {sid} 不存在")
            if not who.has_role("qingxie", real=True):
                raise HTTPException(400, f"{who.name} 不是青协身份，不能当部长")
            who.department_id = d.id
            who.qingxie_post = "head"
            # 部长不再是骨干：他本人不再有归属骨干，归到他名下的成员也一并松绑
            who.supervisor_id = None
            released = _release_members(db, who)
            if released:
                changed.append(f"{released} 名成员的归属骨干已清空")
            changed.append(f"部长+{who.name}")
        else:
            for old in db.query(User).filter(User.department_id == d.id,
                                             User.qingxie_post == "head").all():
                old.qingxie_post = "member"
            changed.append("清空部长")
    if body.duty_enabled is not None and body.duty_enabled != d.duty_enabled:
        d.duty_enabled = body.duty_enabled
        changed.append("启用值班" if body.duty_enabled else "关闭值班")
        if not body.duty_enabled and d.duty_user_id:
            # 关了值班，当前值班人一并撤下；交接记录里留一条「关闭值班」
            db.add(DutyHandover(id=uid("dty"), dept_id=d.id,
                                from_user_id=d.duty_user_id, to_user_id=None,
                                actor_id=user.id, note="关闭值班"))
            d.duty_user_id = None
    if body.external is not None and body.external != d.external:
        d.external = body.external
        changed.append("归口活动需交立项书/结项书：" + ("开" if body.external else "关"))
    if body.vb_liaison is not None:
        if not body.vb_liaison and d.vb_liaison:
            raise HTTPException(400, "志愿北京对接部门不能清空，"
                                     "请把它指定给别的部门")
        if body.vb_liaison and not d.vb_liaison:
            # 全平台至多一个对接部门：先清掉原来的，再设这个
            others = db.query(Department).filter(
                Department.vb_liaison.is_(True), Department.id != d.id).all()
            old_name = others[0].name if others else ""
            for other in others:
                other.vb_liaison = False
            db.flush()
            d.vb_liaison = True
            changed.append("设为志愿北京对接部门"
                           + (f"（原：{old_name}）" if old_name else ""))
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
    department_id: str = ""      # 留空 = 沿用活动的归口部门；council = 会长团
    note: str = ""
    auto_publish: bool = True    # 通过即发布；不勾就停在「待发布」


def _submit_council(db: OrmSession, act: Activity, user: User,
                    body: SubmitBody) -> dict:
    """归口会长团：不要求部门部长，归口存空，按内部活动审批——
    立项书、结项书都不收。待办通知只发会长，其他会长团成员照样能批。"""
    contacts = _council_contacts(db)
    if not contacts:
        raise HTTPException(400, "还没有会长团成员，不能提交会长团审批")
    act.status = "pending"
    act.auto_publish = body.auto_publish
    act.approval_dept_id = None
    act.approval_assignee_id = None
    act.approval_comment = ""
    act.submitted_at = now()
    act.approval_reminded_at = None
    note = body.note.strip()
    _event(db, act, "submit", user, dept_id=None, to_user_id=None, comment=note)
    audit_log(db, actor=user, action="submit", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="提交「会长团」审批")
    for c in contacts:
        _notify(db, c, f"志愿活动待审批｜{act.title}",
                f"{user.name} 提交了「{act.title}」，组织单位：{act.organizer_text()}。\n"
                f"归口：会长团。\n"
                + (f"备注：{note}\n" if note else "")
                + "请到「活动审批」处理。", act, route="approvals")
    db.commit()
    return {"ok": True, "assignee": "会长团"}


@router.post("/activities/{activity_id}/submit")
def submit(activity_id: str, body: SubmitBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """提交审批。草稿或被驳回的活动都走这里。"""
    ensure_activity_manager(db, user, activity_id)
    # 行锁读：两次点击同时到，只有一次读到 draft 并提交，另一次等到状态已变后被下面挡下
    act = (db.query(Activity).filter(Activity.id == activity_id)
           .populate_existing().with_for_update().one_or_none())
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "draft":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能提交审批")
    from .staff_activities import ensure_ready_to_publish
    ensure_ready_to_publish(act)
    # 归口部门：这活动归哪个部门，就由那个部门的部长审。青协的人建活动时
    # 已经按所在部门预填了，提交时可以改；发起方建的在这里选。
    dept_id = body.department_id.strip() or act.approval_dept_id or ""
    if dept_id == COUNCIL_DEPT:
        return _submit_council(db, act, user, body)
    dept = db.get(Department, dept_id) if dept_id else None
    if dept is None:
        raise HTTPException(400, "请选择归口部门")
    heads = liaison.dept_heads(db, dept.id)
    if not heads:
        raise HTTPException(400, f"「{dept.name}」还没有指定部长，请联系管理员")
    head = heads[0]
    head_names = "、".join(h.name for h in heads)
    # 对外活动要带立项书：审批人看的就是这份东西，没有它批不了
    if dept.external \
            and liaison.project_file(db, act.id, "proposal") is None:
        raise HTTPException(400, f"提交「{dept.name}」审批须先上传立项书")
    # 审批人仍挂部长（部门的责任人），通知发给值班的人（没人值班发全体部长）
    duty = liaison.on_duty(db, dept)
    contacts = [duty] if duty is not None else heads
    via = f"，值班 {duty.name}" if duty is not None else ""

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
              summary=f"提交「{dept.name}」审批（部长 {head_names}{via}）")
    for c in contacts:
        _notify(db, c, f"志愿活动待审批｜{act.title}",
                f"{user.name} 提交了「{act.title}」，组织单位：{act.organizer_text()}。\n"
                f"归口部门：{dept.name}。\n"
                + (f"备注：{body.note.strip()}\n" if body.note.strip() else "")
                + "请到「活动审批」处理。", act, route="approvals")
    db.commit()
    return {"ok": True, "assignee": "、".join(c.name for c in contacts)}


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
    # 行锁读：通过/驳回同时到，只有一次读到 pending 并处理，另一次等到状态已变后被下面挡下
    act = (db.query(Activity).filter(Activity.id == activity_id)
           .populate_existing().with_for_update().one_or_none())
    if act is None:
        raise HTTPException(404, "活动不存在")
    if act.status != "pending":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，无需审批")
    if not _can_decide(db, user, act):
        raise HTTPException(403, "这条审批不在您名下")
    comment = body.comment.strip()
    if not body.approve and len(comment) < 2:
        raise HTTPException(400, "驳回必须写明理由，会发给发起方")
    # 转交进要求立项书的部门的活动可能还没传：批之前得有
    if body.approve and liaison.is_external(db, act) \
            and liaison.project_file(db, act.id, "proposal") is None:
        raise HTTPException(400, "这场对外活动还没有上传立项书，请驳回让发起方补传")
    # 通过即发布的活动截止已过：发出去就是「已截止」，没人报得上
    if body.approve and act.auto_publish and not act.is_record() \
            and act.reg_end is not None and act.reg_end <= now():
        raise HTTPException(
            400, f"报名截止时间（{act.reg_end:%m-%d %H:%M}）已经过去，通过后没人"
                 f"报得上。请让发起方先改报名时间再审批，或驳回")

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
            + (f"并发布，{published_hint(act.reg_start)}。" if published
               else "，请在活动管理页点「发布」，学生即可看到活动。")
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
        _notify(db, db.get(User, row.user_id), title, body_text, act,
                route="manage", tab="review")
    db.commit()
    return {"ok": True, "status": act.status}


class TransferBody(BaseModel):
    department_id: str = ""
    student_id: str = ""
    to_council: bool = False       # 转回会长团：归口存空，按内部活动审批
    reason: str


@router.post("/activities/{activity_id}/transfer-approval")
def transfer(activity_id: str, body: TransferBody,
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """把审批转交给别的部门或别的人。必须写理由，三方都会收到通知。

    转交审批就是改归口：转给哪个部门（或哪个部门的人），活动就归那个部门，
    部长的活动列表、部门成员的可见范围都跟着变。转回会长团时归口存空，
    按内部活动审批，不收立项书、结项书。

    立项书跟着归口走：从要求立项书的部门转出去就是内部活动，已传的立项书删掉，
    免得发起方以为还要照着它走对外流程；转进要求立项书的部门而还没有立项书的，
    通知活动管理者补传（没有它审批人批不了，见 decide）。
    """
    # 行锁读：两处转交同时到，只有一次读到 pending 并处理，另一次等到状态已变后被下面挡下
    act = (db.query(Activity).filter(Activity.id == activity_id)
           .populate_existing().with_for_update().one_or_none())
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
    council_contacts: list[User] = []
    if body.to_council:
        if act.approval_dept_id is None and act.approval_assignee_id is None:
            raise HTTPException(400, "这条审批已经在会长团名下")
        council_contacts = _council_contacts(db)
        if not council_contacts:
            raise HTTPException(400, "还没有会长团成员，不能转交会长团")
        dept, target, target_name = None, None, "会长团"
    elif body.student_id.strip():
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
        target_name = target.name
    elif body.department_id:
        dept = db.get(Department, body.department_id)
        if dept is None:
            raise HTTPException(400, "目标部门不存在")
        target = _dept_head(db, dept.id)
        if target is None:
            raise HTTPException(400, f"「{dept.name}」还没有指定部长")
        target_name = target.name
    else:
        raise HTTPException(400, "请选择要转交的部门、指定审批人或会长团")
    if target is not None and target.id == act.approval_assignee_id:
        raise HTTPException(400, "转交对象就是当前审批人")

    before = db.get(User, act.approval_assignee_id) if act.approval_assignee_id else None
    was_external = liaison.is_external(db, act)
    act.approval_dept_id = dept.id if dept else None
    act.approval_assignee_id = target.id if target is not None else None
    act.approval_reminded_at = None
    _event(db, act, "transfer", user, dept_id=dept.id if dept else None,
           to_user_id=target.id if target is not None else None, comment=reason)
    external = liaison.is_external(db, act)
    dropped = (liaison.drop_project_file(db, act.id, "proposal")
               if was_external and not external else None)
    need_proposal = external and liaison.project_file(db, act.id, "proposal") is None
    to_text = dept.name if dept else "会长团"
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"审批转交：{before.name if before else '—'} → {target_name}"
                      f"（{to_text}）；理由：{reason}"
                      + (f"；转为内部活动，立项书《{dropped.title}》已删除"
                         if dropped else ""))
    # 转给部门的（审批人落在部长身上）通知该部门的值班人或全体部长；点名转给某人的就是他；
    # 转回会长团的通知同提交：标了会长只发他，没标发全体
    if body.to_council:
        contacts = council_contacts
    elif not body.student_id.strip() and dept is not None:
        contacts = liaison.approval_contacts(db, dept.id) or [target]
    else:
        contacts = [target]
    for c in contacts:
        _notify(db, c, f"转来一条待审批｜{act.title}",
                f"{user.name} 把「{act.title}」的审批转交给您。\n理由：{reason}"
                + ("\n这场活动还没有上传立项书，已通知活动管理者补传。" if need_proposal else ""),
                act, route="approvals")
    _notify(db, before, f"审批已转出｜{act.title}",
            f"您名下的「{act.title}」已由 {user.name} 转交给 {target_name}。\n"
            f"理由：{reason}", act)
    from ..models import ActivityManager
    for row in db.query(ActivityManager).filter(
            ActivityManager.activity_id == act.id).all():
        _notify(db, db.get(User, row.user_id), f"审批已转交｜{act.title}",
                f"「{act.title}」的审批已转交给 {target_name}"
                f"（{to_text}）。"
                + ("\n现在按内部活动审批，已上传的立项书已删除。" if dropped else "")
                + (f"\n{to_text}归口的活动须上传立项书：请到活动管理的「审核」页签上传，"
                   f"传好后{to_text}才能审批。" if need_proposal else ""), act,
                route="manage", tab="review")
    db.commit()
    return {"ok": True,
            "assignee": "会长团" if body.to_council
                        else "、".join(c.name for c in contacts)}


@router.get("/approvals")
def my_approvals(db: OrmSession = Depends(db_session),
                 user: User = Depends(require_approver)):
    """待我审批的活动。会长团与管理员看全部待审批，部长看自己名下的；
    启用值班的部门，值班人与部长看这个部门的全部。"""
    from sqlalchemy import or_
    q = db.query(Activity).filter(Activity.status == "pending")
    if not user.has_full_access():
        depts = list(liaison.handler_dept_ids(db, user))
        if user.is_head() and user.department_id and user.department_id not in depts:
            depts.append(user.department_id)
        q = q.filter(or_(Activity.approval_assignee_id == user.id,
                         Activity.approval_dept_id.in_(depts)))
    out = []
    for act in q.order_by(Activity.submitted_at).all():
        if not _can_decide(db, user, act):
            continue
        dept = db.get(Department, act.approval_dept_id) if act.approval_dept_id else None
        creator = db.get(User, act.created_by)
        # 提交时写的说明。审批人做决定的这个页面是最该看到那句话的地方，
        # 光存进流水、发进通知是不够的。
        submit = (db.query(ApprovalEvent)
                  .filter(ApprovalEvent.activity_id == act.id,
                          ApprovalEvent.kind == "submit")
                  .order_by(ApprovalEvent.created_at.desc()).first())
        duty = liaison.on_duty(db, dept) if dept else None
        proposal = liaison.project_file(db, act.id, "proposal")
        out.append({
            "id": act.id, "title": act.title, "organizer": act.organizer,
            "co_organizer": act.co_organizer or "",
            "organizer_text": act.organizer_text(),
            "recruit_label": act.recruit_label(), "location": act.location,
            "time_text": act.time_text(),
            "shifts_count": len(act.main_shifts()),
            "record_only": act.is_record(),
            "submitted_at": iso(act.submitted_at),
            "department": _dept_text(act, dept),
            "assignee": assignee_text(db, act),
            # 在我名下（会长团接管别人名下的那种不算）：前端据此标「当前审批人」。
            # 归口会长团的没有具体审批人，会长团成员都算「在名下」，不挂这个标签
            "mine": (act.approval_assignee_id == user.id
                     or (act.approval_dept_id is None and user.has_full_access())
                     or ((liaison.is_dept_handler(db, user, act.approval_dept_id)
                          or liaison.is_head_of(user, dept))
                         and _in_dept(db, act))),
            "on_duty": duty.name if duty else "",
            "creator": creator.name if creator else "",
            "submit_note": submit.comment if submit else "",
            # 对外活动的立项书：审批人就在这一屏拍板，有没有、点开看都在这儿
            "external": bool(dept and dept.external),
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
    dept_names = {d.id: d.name for d in db.query(Department).all()}
    return {
        "status": act.status, "status_label": act.label_status(),
        "department": _dept_text(act, dept),
        "assignee": assignee_text(db, act),
        # 启用值班的部门：通知实际发给了谁（管理者看「等谁处理」要看这个）
        "on_duty": (d.name if (d := liaison.on_duty(db, dept)) else ""),
        "can_decide": _can_decide(db, user, act) and act.status == "pending",
        "comment": act.approval_comment,
        "events": [{
            "kind": e.kind, "kind_label": e.kind_label(),
            "actor": (db.get(User, e.actor_id).name
                      if db.get(User, e.actor_id) else "?"),
            "to": _event_to(db, e, dept, dept_names),
            "comment": e.comment, "created_at": iso(e.created_at),
        } for e in rows],
    }
