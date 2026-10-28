"""角色与层级管理。这一页改的是权限本身。

谁能改什么：
  · 管理员、会长团：所有人的身份与青协层级。
  · 部长：只能把人设为**本部门**的骨干或成员、或把本部门的骨干/成员撤下；
    动不了部长、会长团、管理员、活动发起方、学生这些身份——青协之外的
    身份一律不碰。
  · 骨干：只能把本部门的成员归到自己名下，别的一概不动。
  · 用户类型（老师/学生）由统一身份认证下发，平台不改。
  · 管理员不能移除自己的管理员身份（见 _guard_self_demotion）；会长团改自己
    的层级不设护栏。

操作者（user/actor）的权限跟当前身份（X-Identity，见 deps）；被操作的人
（target、boss、列表里的每一行）一律按真实身份——改的是自己时，库里查出来
的 target 就是当前用户这个实例，带着身份视角，不能让视角遮住身份事实。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import Text, case, cast, or_
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user
from ..models import (Activity, ActivityManager, Department, ROLES,
                      ROLE_LABELS, User, like_pattern)
from ..perm import writing_manager_ids

router = APIRouter(prefix="/api/staff", tags=["roles"])

POSTS = ("council", "head", "core", "member")
# 部长能授予的层级
HEAD_GRANTABLE = ("core", "member")
# 已结束的活动是终态、只读，没人管也不要紧；未结束的才需要有人盯着
LIVE_STATUSES = ("draft", "pending", "approved", "published", "closed")

# 角色列表里「同部门的人聚在一起」的只有骨干、成员这两档
DEPT_RANKS = (3, 4)


def _holds(role: str):
    """这个人持不持有某个身份。

    roles 是 JSON 而不是 JSONB，没有包含运算符可用；按序列化后的文本匹配
    "admin" 这种带引号的完整词，不会误命中别的值。
    """
    return cast(User.roles, Text).like(f'%"{role}"%')


def _role_order() -> list:
    """角色列表的排序键：身份档 → （骨干、成员两档内）部门 → 学号。

    档从高到低是 管理员 / 会长团 / 部长 / 骨干 / 成员 / 活动发起方 / 志愿者。
    一个人有多种身份时按最高档算——CASE 从高到低写，先命中的就是最高档。
    学号唯一，排序键稳定，翻页不会有人重复或漏掉。
    """
    rank = case(
        (_holds("admin"), 0),
        (_holds("qingxie") & (User.qingxie_post == "council"), 1),
        (_holds("qingxie") & (User.qingxie_post == "head"), 2),
        (_holds("qingxie") & (User.qingxie_post == "core"), 3),
        (_holds("qingxie") & (User.qingxie_post == "member"), 4),
        (_holds("initiator"), 5),
        else_=6,
    )
    in_dept = rank.in_(DEPT_RANKS)
    # 只有那两档带部门键；别的档里部门是空档，同一档内就只剩学号说话
    return [rank,
            case((in_dept, Department.sort), else_=None),
            case((in_dept, Department.name), else_=None),
            User.student_id]


def require_role_manager(user: User = Depends(current_user)) -> User:
    """进得了角色管理页的人：管理员、会长团、部长，以及只管认领成员的骨干。"""
    if not (user.has_full_access() or user.post() in ("head", "core")):
        raise HTTPException(403, "需要 管理员/会长团/部长/部门骨干 身份")
    return user


def _guard_self_demotion(actor: User, target: User, roles: list[str]) -> None:
    """不许把自己的管理员身份去掉。

    这一条就足以保证系统里永远至少有一个管理员：要让管理员归零，必然有人
    得移除自己。所以不必再算「是不是最后一个管理员」那种复杂条件。
    必须拦在后端——前端不显示按钮挡不住直接调接口。
    """
    # 按真实身份：以青协身份操作时，照样不能顺手把自己的管理员身份去掉
    if (actor.id == target.id and actor.is_admin(real=True)
            and "admin" not in roles):
        raise HTTPException(400, "不能移除自己的管理员身份；"
                                 "如需交接，请先由另一位管理员操作")


def _guard_head_scope(actor: User, target: User, roles: list[str],
                      post: str, dept_id: str | None) -> None:
    """部长的权限只覆盖本部门的骨干与成员。

    能做的只有两件事：把一个人设成本部门的骨干/成员，或把本部门的骨干/成员
    撤下青协身份。目标已经是部长、会长团、管理员的，动不了；青协之外的
    身份（管理员、发起方、学生）一个都不能跟着加减。
    """
    if actor.has_full_access():
        return
    if target.is_admin(real=True) or target.post(real=True) in ("council", "head"):
        raise HTTPException(403, "部长只能调整本部门的骨干与成员")
    if target.department_id not in (None, actor.department_id):
        raise HTTPException(403, "这位同学属于别的部门")
    if (set(roles) ^ set(target.roles or [])) - {"qingxie"}:
        raise HTTPException(403, "部长只能调整青协身份，其他身份请找会长团")
    if "qingxie" in roles:
        if post not in HEAD_GRANTABLE:
            raise HTTPException(403, "部长只能设骨干或成员")
        if dept_id != actor.department_id:
            raise HTTPException(403, "只能设到自己所在的部门")


def _guard_core_scope(actor: User, target: User, body: "RoleBody") -> None:
    """骨干只做一件事：把本部门的成员归到自己名下。身份、层级、部门都不能动。"""
    if actor.has_full_access() or actor.post() != "core":
        return
    if (target.post(real=True) != "member"
            or target.department_id != actor.department_id):
        raise HTTPException(403, "骨干只能认领本部门的成员")
    if (set(body.roles) != set(target.roles or []) or body.qingxie_post != "member"
            or (body.department_id.strip() or None) != actor.department_id):
        raise HTTPException(403, "骨干只能设置成员归属谁，不能改身份或层级")
    if body.supervisor_id.strip() != actor.id:
        raise HTTPException(403, "骨干只能把成员归到自己名下")


def _orphaned_activities(db: OrmSession, target: User,
                         roles: list[str], post: str) -> list[str]:
    """撤掉写权限后，哪些未结束的活动会变成没人能操作。

    身份一撤，require_manager 第一道闸就把人挡在外面，名下所有活动一个都
    进不去；降成部门成员也一样——在名单里但只能看。若他是某个还在跑的活动
    唯一能操作的管理者，那个活动当场没人管，而操作的人看不出这个后果。
    """
    still_writer = ("admin" in roles or "initiator" in roles
                    or ("qingxie" in roles and post != "member"))
    if still_writer:
        return []
    mine = [r.activity_id for r in db.query(ActivityManager)
            .filter(ActivityManager.user_id == target.id).all()]
    orphans = []
    for aid in mine:
        act = db.get(Activity, aid)
        if act is None or act.status not in LIVE_STATUSES:
            continue
        others = [i for i in writing_manager_ids(db, aid) if i != target.id]
        if not others:
            orphans.append(act.title)
    return orphans


def _check_supervisor(db: OrmSession, target: User, body: "RoleBody",
                      dept_id: str | None) -> str:
    """部门成员必须归属一位本部门的骨干：他的可见范围就是跟着这个人走的。"""
    boss = db.get(User, body.supervisor_id.strip()) if body.supervisor_id.strip() else None
    if boss is None:
        raise HTTPException(400, "部门成员必须指定归属的部门骨干")
    if boss.id == target.id:
        raise HTTPException(400, "不能归属自己")
    if boss.post(real=True) != "core" or boss.department_id != dept_id:
        raise HTTPException(400, f"{boss.name} 不是这个部门的骨干")
    return boss.id


def _release_members(db: OrmSession, target: User) -> int:
    """把归到目标名下的成员放回「无归属」，返回清空的人数。

    成员继承的是「一位在职骨干」的可见范围。骨干被撤下、升迁或换了部门，
    这份继承当场失效（读权限时 perm._supervisor 也会判），库里指着他的那条
    归属一并清掉——否则名单上只剩一个看不出已经失效的骨干。调用方负责在
    目标不再是本部门骨干时才调它。
    """
    return (db.query(User)
            .filter(User.supervisor_id == target.id)
            .update({"supervisor_id": None}, synchronize_session=False))


def _audit_role_change(db: OrmSession, actor: User, target: User,
                       before: tuple, after: tuple,
                       *, prefix: str = "调整身份：", note: str = "") -> None:
    """身份、层级、部门有变化就留一条审计；没变化不写。"""
    if before == after:
        return
    label = lambda rs: "、".join(ROLE_LABELS.get(r, r) for r in rs) or "无"
    parts = [f"身份 {label(before[0])} → {label(after[0])}"]
    if before[1] != after[1]:
        parts.append(f"（；青协层级 {User.POST_LABELS.get(before[1], before[1])}"
                     f" → {User.POST_LABELS.get(after[1], after[1])}）")
    if before[2] != after[2]:
        names = {d.id: d.name for d in db.query(Department).all()}
        parts.append(f"（；部门 {names.get(before[2], '无')}"
                     f" → {names.get(after[2], '无')}）")
    summary = prefix + "".join(parts)
    if note:
        summary += "；" + note
    audit_log(db, actor=actor, action="update", target_type="user",
              target_id=target.id,
              target_label=f"{target.name}（{target.student_id}）",
              summary=summary)


def _row(u: User, me: User, depts: dict[str, str]) -> dict:
    return {
        "id": u.id, "student_id": u.student_id, "name": u.name,
        "college": u.college, "user_type": u.user_type,
        "roles": u.roles or [], "role_label": u.active_role_label(),
        "qingxie_post": u.post(real=True) or "",
        "post_label": u.post_label() if u.post(real=True) else "",
        "department_id": u.department_id or "",
        "department": depts.get(u.department_id, ""),
        "supervisor_id": u.supervisor_id or "",
        "supervisor": depts.get(u.supervisor_id, ""),
        # 已删掉的部门不往外给：前端没处查它的名字，只会露出一串 id
        "remind_dept_ids": [d for d in (u.remind_dept_ids or []) if d in depts],
        # 会长团里标了「会长」的那一位：待办通知只发他
        "is_president": bool(u.is_president),
        "is_me": u.id == me.id,
    }


@router.get("/roles/cores")
def list_cores(department_id: str = "", db: OrmSession = Depends(db_session),
               user: User = Depends(require_role_manager)):
    """某部门的骨干名单，给「部门成员归属谁」这个选择框用。

    管理员与会长团查哪个部门都行；部长与骨干只认本部门，传了别的部门也
    按本部门给——和 list_role_users 一样静默收窄，不报错。
    """
    if user.has_full_access():
        dept_id = department_id.strip() or user.department_id
    else:
        dept_id = user.department_id
    rows = (db.query(User)
            .filter(User.department_id == dept_id,
                    User.qingxie_post == "core")
            .order_by(User.name).all())
    return [{"id": u.id, "name": u.name, "student_id": u.student_id}
            for u in rows if u.has_role("qingxie", real=True)]


@router.get("/roles")
def list_role_users(q: str = "", role: str = "",
                    page: int = Query(1, ge=1),
                    size: int = Query(20, ge=1, le=100),
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_role_manager)):
    """可授予/已持有管理类身份的人，加上按学号或姓名的搜索。

    默认只列**已经有非学生身份的人**：全校几万人平铺出来没法用，要给某个
    学生加身份就搜他的学号。部长默认只看本部门的人，搜索不受限——他要从
    全校学生里挑人进部门。骨干只看本部门的成员，搜也只在这里面搜。

    分页；先按身份档排，见 _role_order。
    """
    query = db.query(User)
    # 先看是不是管理员/会长团，再看部门层级：管理员兼骨干（以并集判权时）
    # 不该被骨干的范围卡住
    full = user.has_full_access()
    core = not full and user.post() == "core"
    if core:
        query = query.filter(User.department_id == user.department_id,
                             User.qingxie_post == "member")
    if q:
        like = like_pattern(q.strip())
        query = query.filter((User.name.ilike(like, escape="\\")) |
                             (User.student_id.ilike(like, escape="\\")))
    elif not full and not core:
        query = query.filter(User.department_id == user.department_id)
    else:
        wanted = [role] if role in ROLES else ["admin", "qingxie", "initiator"]
        query = query.filter(or_(*[_holds(r) for r in wanted]))
    total = query.count()
    # 档内要按部门聚拢，部门名与部门的固定次序都在部门表里，带上这一跳
    rows = (query.outerjoin(Department, User.department_id == Department.id)
            .order_by(*_role_order())
            .offset((page - 1) * size).limit(size).all())
    names = {d.id: d.name for d in db.query(Department).all()}
    # 归属骨干的名字也从同一张表查：成员行上要写「归属：林骁」
    boss_ids = {u.supervisor_id for u in rows if u.supervisor_id}
    if boss_ids:
        names.update({b.id: b.name for b in db.query(User)
                      .filter(User.id.in_(boss_ids)).all()})
    return {"total": total, "items": [_row(u, user, names) for u in rows]}


class RoleBody(BaseModel):
    roles: list[str]
    qingxie_post: str = "member"
    department_id: str = ""
    supervisor_id: str = ""     # 部门成员归属哪位骨干
    remind_dept_ids: list[str] = []  # 会长团超时催办接收部门范围（空列表=全部部门）
    is_president: bool = False       # 会长团里的会长标记（只标一位）


@router.put("/roles/{user_id}")
def set_roles(user_id: str, body: RoleBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_role_manager)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    roles = [r for r in dict.fromkeys(body.roles) if r]
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise HTTPException(400, f"未知身份：{'、'.join(bad)}")
    if not roles:
        raise HTTPException(400, "至少要保留一个身份")
    if body.qingxie_post not in POSTS:
        raise HTTPException(400, "青协层级不合法")
    dept_id = body.department_id.strip() or None
    if dept_id and db.get(Department, dept_id) is None:
        raise HTTPException(404, "部门不存在")

    _guard_self_demotion(user, target, roles)
    _guard_head_scope(user, target, roles, body.qingxie_post, dept_id)
    _guard_core_scope(user, target, body)
    if target.user_type == "student" and "student" not in roles:
        raise HTTPException(400, "学生的「志愿者」身份不可清除")
    if target.user_type == "teacher" and "initiator" not in roles:
        raise HTTPException(400, "教师的「活动发起方」身份不可清除")

    orphans = _orphaned_activities(db, target, roles, body.qingxie_post)
    if orphans:
        raise HTTPException(
            409, f"{target.name} 还是这些未结束活动里唯一能操作的管理者："
                 f"{'、'.join(orphans[:5])}"
                 + (f" 等 {len(orphans)} 个" if len(orphans) > 5 else "")
                 + "。请先为它们添加其他管理者。")

    before = (list(target.roles or []), target.qingxie_post, target.department_id)
    target.roles = roles
    if "qingxie" in roles:
        # 部长是「本部门的」，没有部门的部长不成立，审批也找不到人；
        # 骨干与成员同样要归属某个部门，否则部长看不到他们
        if body.qingxie_post != "council" and not dept_id:
            raise HTTPException(400, f"{User.POST_LABELS[body.qingxie_post]}必须指定所属部门")
        target.qingxie_post = body.qingxie_post
        target.department_id = None if body.qingxie_post == "council" else dept_id
        target.supervisor_id = (_check_supervisor(db, target, body, dept_id)
                                if body.qingxie_post == "member" else None)
        if body.qingxie_post == "council":
            valid_depts = {d.id for d in db.query(Department).all()}
            target.remind_dept_ids = [
                d for d in dict.fromkeys(body.remind_dept_ids or [])
                if d in valid_depts
            ]
            was_president = target.is_president
            target.is_president = bool(body.is_president)
            if target.is_president:
                # 只标一位：换人时把上一位的标记撤掉
                (db.query(User)
                 .filter(User.is_president.is_(True), User.id != target.id)
                 .update({"is_president": False}, synchronize_session=False))
            if was_president != target.is_president:
                audit_log(db, actor=user, action="update", target_type="user",
                          target_id=target.id,
                          target_label=f"{target.name}（{target.student_id}）",
                          summary=("标为会长" if target.is_president
                                   else "取消会长标记"))
        else:
            target.remind_dept_ids = []
            target.is_president = False
    else:
        # 不是青协了，层级与部门一并清掉，免得留下「非青协的部长」
        target.qingxie_post = "member"
        target.department_id = None
        target.supervisor_id = None
        target.remind_dept_ids = []
        target.is_president = False

    # 不再是本部门骨干（撤下、升迁、换部门）：归到他名下的成员当场松绑
    released = 0
    if target.qingxie_post != "core" or target.department_id != before[2]:
        released = _release_members(db, target)

    after = (target.roles, target.qingxie_post, target.department_id)
    _audit_role_change(db, user, target, before, after,
                       note=(f"{released} 名成员的归属骨干已清空"
                             if released else ""))
    db.commit()
    return {"ok": True}


# ======================= 批量调整身份 =======================

BATCH_MAX = 200
# 批量能设的青协层级：部长决定一个部门的审批由谁来拍，要逐个确认，不批量设
BATCH_GRANTABLE_POSTS = ("council", "core", "member")


class BatchRoleBody(BaseModel):
    action: str                     # add / remove
    role: str                       # qingxie / initiator
    qingxie_post: str = "member"    # 添加青协时：council / core / member
    department_id: str = ""
    supervisor_id: str = ""         # 添加成员时：归属哪位骨干
    student_ids: list[str] = []
    dry_run: bool = False           # 预览：走一遍全部检查，不落库


def _batch_change(db: OrmSession, actor: User, target: User,
                  body: BatchRoleBody) -> tuple[tuple, tuple]:
    """算出批量给这个人的目标状态，并过一遍单条编辑的全部校验。

    校验不过抛 HTTPException——调用方记成「跳过」并写明原因，不连累同批
    其他人。返回 (before, after)，after 的第四项是归属骨干（None = 无）。
    """
    roles = list(target.roles or [])
    if body.action == "add":
        if body.role in roles:
            raise HTTPException(400, f"已经是「{ROLE_LABELS.get(body.role)}」身份")
        roles.append(body.role)
    else:
        if body.role not in roles:
            raise HTTPException(400, f"没有「{ROLE_LABELS.get(body.role)}」身份")
        if target.user_type == "student" and body.role == "student":
            raise HTTPException(400, "学生的「志愿者」身份不可清除")
        if target.user_type == "teacher" and body.role == "initiator":
            raise HTTPException(400, "教师的「活动发起方」身份不可清除")
        roles = [r for r in roles if r != body.role]
        if not roles:
            raise HTTPException(400, "至少要保留一个身份，先给他别的身份再撤")
    post = target.qingxie_post or "member"
    dept_id = target.department_id or None
    supervisor = target.supervisor_id or None
    if body.role == "qingxie" and body.action == "add":
        post = body.qingxie_post
        dept_id = None if post == "council" else (body.department_id.strip() or None)
        supervisor = ((body.supervisor_id.strip() or None)
                      if post == "member" else None)
    elif body.role == "qingxie":
        # 撤下青协：层级、部门、归属一并清掉（同单条编辑）
        post, dept_id, supervisor = "member", None, None

    _guard_self_demotion(actor, target, roles)
    _guard_head_scope(actor, target, roles, post, dept_id)
    rb = RoleBody(roles=roles, qingxie_post=post,
                  department_id=dept_id or "", supervisor_id=supervisor or "")
    _guard_core_scope(actor, target, rb)
    if body.role == "qingxie" and body.action == "add" and post == "member":
        supervisor = _check_supervisor(db, target, rb, dept_id)
    orphans = _orphaned_activities(db, target, roles, post)
    if orphans:
        raise HTTPException(
            409, "还是这些未结束活动里唯一能操作的管理者："
                 + "、".join(orphans[:3])
                 + (f" 等 {len(orphans)} 个" if len(orphans) > 3 else "")
                 + "。请先为它们添加其他管理者")
    before = (list(target.roles or []), target.qingxie_post, target.department_id)
    return before, (roles, post, dept_id, supervisor)


def _batch_text(body: BatchRoleBody, after: tuple, names: dict) -> str:
    """预览里一行「将怎么变」。"""
    _roles, post, dept_id, _sup = after
    if body.action == "remove":
        return f"撤下「{ROLE_LABELS.get(body.role)}」"
    if body.role == "initiator":
        return "添加「活动发起方」"
    text = f"添加「青协」· {User.POST_LABELS.get(post, post)}"
    if dept_id:
        text += f" · {names.get(dept_id, '')}"
    return text


@router.post("/roles/batch")
def batch_roles(body: BatchRoleBody, db: OrmSession = Depends(db_session),
                user: User = Depends(require_role_manager)):
    """按学号批量添加 / 移除身份，一行一个学号。

    先预览（dry_run=true）再执行，两次都逐人过单条编辑的同一套校验：
    不满足条件的（跨部门、目标是部长、会变成孤儿活动等）记进 skipped 并写明
    原因，不连累同批其他人；执行时逐人写审计，同一个事务提交。
    """
    if body.action not in ("add", "remove"):
        raise HTTPException(400, "操作只能是 添加 / 移除")
    if body.role not in ("qingxie", "initiator"):
        raise HTTPException(400, "批量只支持「青协」与「活动发起方」两个身份；"
                                 "管理员、志愿者不批量")
    ids = list(dict.fromkeys(s.strip() for s in body.student_ids if s.strip()))
    if not ids:
        raise HTTPException(400, "请填写学号，一行一个")
    if len(ids) > BATCH_MAX:
        raise HTTPException(400, f"一次最多 {BATCH_MAX} 个学号（收到 {len(ids)} 个）")
    if body.action == "add" and body.role == "qingxie":
        if body.qingxie_post not in BATCH_GRANTABLE_POSTS:
            raise HTTPException(400, "批量只能设 会长团 / 骨干 / 成员；"
                                     "部长请逐个设置")
        dept_id = body.department_id.strip() or None
        if body.qingxie_post != "council" and not dept_id:
            raise HTTPException(400, "请选择部门")
        if dept_id and db.get(Department, dept_id) is None:
            raise HTTPException(404, "部门不存在")

    users = {u.student_id: u for u in
             db.query(User).filter(User.student_id.in_(ids)).all()}
    names = {d.id: d.name for d in db.query(Department).all()}
    updated, skipped = [], []
    for sid in ids:
        u = users.get(sid)
        if u is None:
            continue
        try:
            before, after = _batch_change(db, user, u, body)
        except HTTPException as e:
            skipped.append({"student_id": sid, "name": u.name,
                            "reason": str(e.detail)})
            continue
        if not body.dry_run:
            roles, post, dept_id, supervisor = after
            u.roles = roles
            if "qingxie" in roles:
                u.qingxie_post, u.department_id = post, dept_id
                u.supervisor_id = supervisor
            else:
                u.qingxie_post, u.department_id, u.supervisor_id = "member", None, None
            # 催办部门范围只对会长团有意义；批量不设这个范围，调整后不是会长团的
            # 一律清空，免得以后再设成会长团时带着一份旧范围
            if not ("qingxie" in roles and post == "council"):
                u.remind_dept_ids = []
                u.is_president = False
            # 同单条编辑：不再是本部门骨干就把归到他名下的成员松绑
            released = 0
            if post != "core" or dept_id != before[2]:
                released = _release_members(db, u)
            _audit_role_change(db, user, u, before, after[:3],
                               prefix="批量调整身份：",
                               note=(f"{released} 名成员的归属骨干已清空"
                                     if released else ""))
        updated.append({"student_id": sid, "name": u.name,
                        "change": _batch_text(body, after, names)})
    if not body.dry_run:
        db.commit()
    return {"updated": updated, "skipped": skipped,
            "not_found": [s for s in ids if s not in users]}
