"""值班、立项书与结项书、志愿北京名称的外联确认。

启用值班的部门（Department.duty_enabled）：审批量大、部长一个人盯不过来时，
由部门骨干轮流值班，这个部门的审批（活动发布、时长锁定/解锁、志愿北京确认）
通知只发给值班的人，值班的人和部长一样能拍板；部长每晚收一份日报。
没人值班就回落给部长——审批不能因为排班空档没人收。哪些部门启用值班是部门
维护里的开关，由会长团/管理员打开或关闭。

「对外活动」= 归口开了 external 的部门的活动：提交审批要带立项书，时长报批要带
盖章立项书和盖章结项书。这几份文件都挂在 Attachment 上（owner_type = proposal /
stamped / closing，owner_id = 活动 id），存储与一次性票据下载沿用学习材料那一套。
立项书、结项书的空白模板由志愿北京对接部门的部长上传，发起方下载了照着填
（owner_type = template，owner_id = 格子名，不挂活动）。

志愿北京确认不分内外：所有活动提交时长审核时都要填志愿北京上的活动名称，
时长审核通过（锁定）后名称推给志愿北京对接部门核对，确认了活动才算「已完成」。
活动本身归口哪个部门都一样——志愿北京那边的账只有对接部门对。哪个部门对接
志愿北京也是部门维护里的开关（Department.vb_liaison，全平台至多一个）。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .models import Activity, Attachment, Department, User, VbConfirm, now, uid

# 能值班的：部门骨干与部长。部门成员是学习权限，拍不了板。
ELIGIBLE_POSTS = ("core", "head")

SLOTS = {"proposal": "立项书", "stamped": "盖章立项书", "closing": "盖章结项书"}
# 时长审核要附的盖章材料：对外活动两份都齐了才能提交
STAMPED_SLOTS = ("stamped", "closing")
# 各格能传的文件类型。立项书是提交审批时交的电子稿；盖章件是扫描件或照片，
# 一格只留一份，多页的要合成一个 PDF
SLOT_EXTS: dict[str, tuple[str, ...]] = {
    "proposal": ("docx", "pdf"),
    "stamped": ("pdf", "png", "jpg", "jpeg"),
    "closing": ("pdf", "png", "jpg", "jpeg"),
}

# 立项书、结项书的空白模板：发起方下载了照着填
DOC_TEMPLATES = {"proposal": "立项书模板", "closing": "结项书模板"}
DOC_TEMPLATE_EXTS = ("docx",)


# ======================= 部门与值班 =======================

def duty_enabled(dept: Department | None) -> bool:
    return dept is not None and dept.duty_enabled


def vb_dept(db: OrmSession) -> Department | None:
    """志愿北京对接部门。全平台至多一个（部门表上有条件唯一索引）。"""
    return db.query(Department).filter(Department.vb_liaison.is_(True)).first()


def vb_dept_name(db: OrmSession) -> str:
    """志愿北京对接部门的名字，给通知和报错文案用。没设就笼统称「校青协」。"""
    dept = vb_dept(db)
    return dept.name if dept is not None else "校青协"


def dept_heads(db: OrmSession, dept_id: str) -> list[User]:
    return (db.query(User)
            .filter(User.department_id == dept_id,
                    User.qingxie_post == "head")
            .order_by(User.student_id).all())


def dept_head(db: OrmSession, dept_id: str) -> User | None:
    heads = dept_heads(db, dept_id)
    return heads[0] if heads else None


def is_head_of(user: User, dept: Department | None) -> bool:
    """当前操作者是不是这个部门的部长（跟当前身份：切到别的身份就不算）。"""
    return dept is not None and user.department_id == dept.id and user.is_head()


def eligible(user: User | None, dept: Department, *, real: bool = True) -> bool:
    """能不能在这个部门值班：本部门的骨干或部长。

    默认按真实身份：这是这个人的资格，值班人是谁、能交班给谁都看它。
    同一个请求里查出来的值班人可能正是当前用户本人（带着身份视角），按视角
    算的话，他切到管理员身份去操作，自己的值班就「失效」了、通知改发给部长。
    问的是「当前操作者此刻能不能以值班人身份办事」时传 real=False。
    """
    return (user is not None and user.department_id == dept.id
            and user.post(real=real) in ELIGIBLE_POSTS)


def eligible_users(db: OrmSession, dept: Department) -> list[User]:
    rows = (db.query(User).filter(User.department_id == dept.id,
                                  User.qingxie_post.in_(ELIGIBLE_POSTS))
            .order_by(User.student_id).all())
    return [u for u in rows if eligible(u, dept)]


def on_duty(db: OrmSession, dept: Department | None) -> User | None:
    """当前值班人。人被调走或降成成员之后，他的值班自动失效——
    不然通知会一直发给一个已经不在这个部门、也拍不了板的人。"""
    if not duty_enabled(dept) or not dept.duty_user_id:
        return None
    u = db.get(User, dept.duty_user_id)
    return u if eligible(u, dept) else None


def approval_contacts(db: OrmSession, dept_id: str | None) -> list[User]:
    """这个部门的审批该通知谁：启用值班的部门先找值班人，没人值班找全体部长；
    其他部门通知该部门全体部长。"""
    dept = db.get(Department, dept_id) if dept_id else None
    if dept is None:
        return []
    cur = on_duty(db, dept)
    if cur is not None:
        return [cur]
    return dept_heads(db, dept.id)


def approval_contact(db: OrmSession, dept_id: str | None) -> User | None:
    """这个部门的审批该通知谁：启用值班的部门先找值班人，没人值班找部长；
    其他部门照旧是部长。"""
    contacts = approval_contacts(db, dept_id)
    return contacts[0] if contacts else None


def is_dept_handler(db: OrmSession, user: User, dept_id: str | None) -> bool:
    """能以部门的名义拍这个部门的审批：值班人与部长（仅限启用值班的部门）。

    未启用值班的部门返回 False，那边仍然只认「当前审批人」，行为不变。
    """
    dept = db.get(Department, dept_id) if dept_id else None
    if not duty_enabled(dept):
        return False
    if is_head_of(user, dept):
        return True
    return is_on_duty(on_duty(db, dept), user)


def is_on_duty(cur: User | None, user: User) -> bool:
    """当前操作者就是在班的人，且此刻以青协身份在操作。"""
    return (cur is not None and cur.id == user.id
            and user.post() in ELIGIBLE_POSTS)


def handler_dept_ids(db: OrmSession, user: User) -> list[str]:
    """这个人以值班人/部长身份负责的部门。审批列表按它筛。"""
    return [d.id for d in db.query(Department)
            .filter(Department.duty_enabled.is_(True)).all()
            if is_dept_handler(db, user, d.id)]


def duty_eligible(db: OrmSession, user: User, *, real: bool = False) -> bool:
    """在某个启用值班的部门里有值班资格。前端据此露出「活动审批」入口。

    判当前操作者能不能进审批页时跟当前身份；下发给前端的是身份事实，
    session_user 传 real=True。"""
    if not user.department_id or user.post(real=real) not in ELIGIBLE_POSTS:
        return False
    return duty_enabled(db.get(Department, user.department_id))


def session_user(db: OrmSession, user: User) -> dict:
    """登录、刷新个人信息时下发的用户信息：full() 再加上值班资格和对接部门名。

    值班资格要查部门，放不进 User.full()——那个方法在名册列表里逐行调用，
    多一次查询就是多几百次。
    """
    return {**user.full(), "duty_eligible": duty_eligible(db, user, real=True),
            "vb_dept_name": vb_dept_name(db)}


# ======================= 对外活动与立项书 =======================

# 活动正式发布之后的状态。先立项再结项：盖章立项书是活动发布之后才交的
# 材料，草稿和审批中的活动交不了
PUBLISHED_STATES = ("published", "closed", "finished", "vb_pending", "completed")


def is_published(act: Activity) -> bool:
    return act.status in PUBLISHED_STATES


def is_external(db: OrmSession, act: Activity) -> bool:
    """对外活动：归口部门开了「归口活动算对外活动」。"""
    if not act.approval_dept_id:
        return False
    dept = db.get(Department, act.approval_dept_id)
    return dept is not None and dept.external


def project_file(db: OrmSession, activity_id: str, slot: str) -> Attachment | None:
    return (db.query(Attachment)
            .filter(Attachment.owner_type == slot,
                    Attachment.owner_id == activity_id).first())


def drop_attachment(db: OrmSession, row: Attachment | None) -> Attachment | None:
    """删掉一份文件（记录与磁盘文件一起），原样返回传进来的那条。不提交事务。"""
    from .routers.materials import MATERIAL_DIR

    if row is None:
        return None
    (MATERIAL_DIR / row.stored_name).unlink(missing_ok=True)
    db.delete(row)
    return row


def drop_project_file(db: OrmSession, activity_id: str, slot: str) -> Attachment | None:
    """删掉某一格里的文件。返回删掉的那条，没有就 None。不提交事务。"""
    return drop_attachment(db, project_file(db, activity_id, slot))


def missing_stamped(db: OrmSession, act: Activity) -> list[str]:
    """对外活动提交时长审核还缺哪几份盖章材料（名称）；不是对外活动就是空的。"""
    if not is_external(db, act):
        return []
    return [SLOTS[s] for s in STAMPED_SLOTS if project_file(db, act.id, s) is None]


def doc_template(db: OrmSession, slot: str) -> Attachment | None:
    return (db.query(Attachment)
            .filter(Attachment.owner_type == "template",
                    Attachment.owner_id == slot).first())


def can_manage_templates(db: OrmSession, user: User) -> bool:
    """立项书、结项书模板由志愿北京对接部门的部长更换；会长团和管理员同样可以。"""
    return user.has_full_access() or is_head_of(user, vb_dept(db))


def can_see_project_files(db: OrmSession, user: User, act: Activity) -> bool:
    """立项书给谁看：活动管理者，以及审批线上的人（值班人/部长/会长团/管理员、
    当前审批人与批过它的人）。志愿者看不到——活动公开了也不行。"""
    from .perm import is_activity_manager
    from .routers.approvals import can_review

    return (is_activity_manager(db, user, act.id)
            or is_dept_handler(db, user, act.approval_dept_id)
            or can_review(db, user, act))


# ======================= 志愿北京名称与外联确认 =======================

VB_NAME_MAX = 20        # 一场活动最多填几个志愿北京名称


def clean_vb_names(raw) -> list[str]:
    """管理者填的志愿北京活动名称：去掉首尾空白与空行、去重（保留顺序）。
    每个 2~200 字，至少一个、至多 VB_NAME_MAX 个；不合格抛 ValueError，
    消息直接给人看。"""
    out: list[str] = []
    for x in raw or []:
        name = str(x or "").strip()
        if not name or name in out:
            continue
        if len(name) < 2:
            raise ValueError(f"志愿北京活动名称「{name}」太短，请写全称")
        if len(name) > 200:
            raise ValueError("志愿北京活动名称不能超过 200 字")
        out.append(name)
    if not out:
        raise ValueError("请填写志愿北京上的活动名称（至少一个）")
    if len(out) > VB_NAME_MAX:
        raise ValueError(f"志愿北京活动名称最多 {VB_NAME_MAX} 个")
    return out


def vb_decider(db: OrmSession, user: User) -> bool:
    """谁来确认志愿北京：对接部门的值班人与部长（跟当前身份），会长团、管理员。"""
    if user.has_full_access():
        return True
    dept = vb_dept(db)
    return dept is not None and is_dept_handler(db, user, dept.id)


def vb_contacts(db: OrmSession) -> list[User]:
    """外联确认逐条通知谁：对接部门的值班人，没人值班发部长——不看活动本身
    归口哪个部门。部门没了、也没设部长就兜底发会长团（按真实身份），
    不然这一条没人知道。"""
    dept = vb_dept(db)
    contacts = approval_contacts(db, dept.id) if dept else []
    if contacts:
        return contacts
    return [u for u in db.query(User).filter(User.qingxie_post == "council").all()
            if u.is_council(real=True)]


def claim_contacts(db: OrmSession, submitter: User) -> list[User]:
    """校外时长补登逐条通知谁：同 vb_contacts，只是补登是提交人自己的事，
    他批不了自己的（见 hours_claims.decide_claim）。轮到的正好是提交人时往上
    找一层：值班人往部长，部长往会长团——不然这一条只通知了一个拍不了板的人。"""
    out = [u for u in vb_contacts(db) if u.id != submitter.id]
    if out:
        return out
    dept = vb_dept(db)
    heads = [h for h in (dept_heads(db, dept.id) if dept else []) if h.id != submitter.id]
    if heads:
        return heads
    return [u for u in db.query(User).filter(User.qingxie_post == "council").all()
            if u.is_council(real=True) and u.id != submitter.id]


def latest_vb_confirm(db: OrmSession, activity_id: str) -> VbConfirm | None:
    return (db.query(VbConfirm).filter(VbConfirm.activity_id == activity_id)
            .order_by(VbConfirm.submitted_at.desc()).first())


def names_text(names: list[str]) -> str:
    return "、".join(f"「{n}」" for n in names or [])


def same_names(a, b) -> bool:
    """两次填的名称算不算同一组。只调顺序不算改。"""
    return set(a or []) == set(b or [])


def push_vb_confirm(db: OrmSession, act: Activity, actor: User | None,
                    kind: str, reason: str = "") -> VbConfirm:
    """把活动当前的志愿北京名称推给志愿北京对接部门核对。不提交事务。

    kind=lock：时长审核通过、刚锁定；kind=resubmit：被退回后管理者改了名称重提；
    kind=repush：活动已完成，管理者再推一次，reason 是他写的原因，随通知发给对接部门。
    """
    from .notify import notify

    row = VbConfirm(id=uid("vbc"), activity_id=act.id, kind=kind,
                    names=list(act.vb_names or []),
                    submitted_by=actor.id if actor else "system",
                    submitted_at=now(), reason=reason)
    db.add(row)
    act.vb_reminded_on = None           # 重提了，「迟迟不重提」的提醒从头算
    if kind == "resubmit":
        lead = "，管理者按退回意见改了志愿北京活动名称，重新提交。"
    elif kind == "repush":
        lead = "，活动已完成，管理者重新提交了志愿北京活动名称。"
    else:
        lead = "。"
    for u in vb_contacts(db):
        notify(db, u, f"志愿北京待确认｜{act.title}",
               f"「{act.title}」（{act.organizer_text()}）的志愿时长已锁定" + lead
               + f"\n志愿北京活动名称：{names_text(row.names)}\n"
               + (f"推送原因：{reason}\n" if reason else "")
               + f"请到志愿北京核对后，在「活动审批」确认或退回。",
               data={"activity_id": act.id, "route": "approvals"})
    return row


def void_pending_vb_confirms(db: OrmSession, act: Activity) -> int:
    """把还没处理的待确认作废：名称要换掉时，旧的不能再让外联照着确认。
    不提交事务。"""
    rows = (db.query(VbConfirm)
            .filter(VbConfirm.activity_id == act.id,
                    VbConfirm.status == "pending").all())
    for r in rows:
        r.status = "void"
    return len(rows)


def settle_vb_on_lock(db: OrmSession, act: Activity, actor: User | None) -> None:
    """时长锁定后接上志愿北京这一步：解锁重新锁定时，名称没变的沿用上一次的结果，
    不再让外联重复核对。不提交事务。

    名称变了才推一条新的待确认；旧的待确认同时作废，免得外联照着旧名称确认。
    """
    last = latest_vb_confirm(db, act.id)
    if last is not None and same_names(last.names, act.vb_names):
        if last.status == "confirmed":
            act.status = "completed"
            return
        if last.status in ("pending", "returned"):
            # 待确认的接着等外联；已退回的等管理者改名称重提
            act.status = "vb_pending"
            return
    void_pending_vb_confirms(db, act)
    act.status = "vb_pending"
    push_vb_confirm(db, act, actor, "lock")
