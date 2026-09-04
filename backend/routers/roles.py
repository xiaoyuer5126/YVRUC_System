"""角色与层级管理。这一页改的是权限本身。

谁能改什么：
  · 管理员、会长团：所有人的身份与青协层级。
  · 部长：只能把人设为**本部门**的骨干或成员、或把本部门的骨干/成员撤下；
    动不了部长、会长团、管理员、活动发起方这些身份。
  · 用户类型（老师/学生）由数智人大下发，平台不改。
  · 管理员不能移除自己的管理员身份（见 _guard_self_demotion）；会长团改自己
    的层级不设护栏。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user
from ..models import (Activity, ActivityManager, Department, ROLES,
                      ROLE_LABELS, User)
from ..perm import writing_manager_ids

router = APIRouter(prefix="/api/staff", tags=["roles"])

POSTS = ("council", "head", "core", "member")
# 部长能授予的层级
HEAD_GRANTABLE = ("core", "member")
# 已结束的活动是终态、只读，没人管也不要紧；未结束的才需要有人盯着
LIVE_STATUSES = ("draft", "pending", "approved", "published", "closed")


def require_role_manager(user: User = Depends(current_user)) -> User:
    """进得了角色管理页的人：管理员、会长团、部长。"""
    if not (user.has_full_access() or user.is_head()):
        raise HTTPException(403, "需要 管理员/会长团/部长 身份")
    return user


def _guard_self_demotion(actor: User, target: User, roles: list[str]) -> None:
    """不许把自己的管理员身份去掉。

    这一条就足以保证系统里永远至少有一个管理员：要让管理员归零，必然有人
    得移除自己。所以不必再算「是不是最后一个管理员」那种复杂条件。
    必须拦在后端——前端不显示按钮挡不住直接调接口。
    """
    if actor.id == target.id and actor.is_admin() and "admin" not in roles:
        raise HTTPException(400, "不能移除自己的管理员身份；"
                                 "如需交接，请先由另一位管理员操作")


def _guard_head_scope(actor: User, target: User, roles: list[str],
                      post: str, dept_id: str | None) -> None:
    """部长的权限只覆盖本部门的骨干与成员。

    能做的只有两件事：把一个人设成本部门的骨干/成员，或把本部门的骨干/成员
    撤下青协身份。目标已经是部长、会长团、管理员的，动不了；管理员/发起方
    这些身份也不能跟着加减。
    """
    if actor.has_full_access():
        return
    if target.is_admin() or target.post() in ("council", "head"):
        raise HTTPException(403, "部长只能调整本部门的骨干与成员")
    if target.department_id not in (None, actor.department_id):
        raise HTTPException(403, "这位同学属于别的部门")
    keep = set(roles) - {"qingxie", "student"}
    had = set(target.roles or []) - {"qingxie", "student"}
    if keep != had:
        raise HTTPException(403, "部长只能调整青协身份，管理员/活动发起方身份请找会长团")
    if "qingxie" in roles:
        if post not in HEAD_GRANTABLE:
            raise HTTPException(403, "部长只能设骨干或成员")
        if dept_id != actor.department_id:
            raise HTTPException(403, "只能设到自己所在的部门")


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
    if boss.post() != "core" or boss.department_id != dept_id:
        raise HTTPException(400, f"{boss.name} 不是这个部门的骨干")
    return boss.id


def _row(u: User, me: User, depts: dict[str, str]) -> dict:
    return {
        "id": u.id, "student_id": u.student_id, "name": u.name,
        "college": u.college, "user_type": u.user_type,
        "roles": u.roles or [], "role_label": u.active_role_label(),
        "qingxie_post": u.post() or "",
        "post_label": u.post_label() if u.post() else "",
        "department_id": u.department_id or "",
        "department": depts.get(u.department_id, ""),
        "supervisor_id": u.supervisor_id or "",
        "is_me": u.id == me.id,
    }


@router.get("/roles/cores")
def list_cores(department_id: str = "", db: OrmSession = Depends(db_session),
               user: User = Depends(require_role_manager)):
    """某部门的骨干名单，给「部门成员归属谁」这个选择框用。"""
    dept_id = department_id.strip() or user.department_id
    rows = (db.query(User)
            .filter(User.department_id == dept_id,
                    User.qingxie_post == "core")
            .order_by(User.name).all())
    return [{"id": u.id, "name": u.name, "student_id": u.student_id}
            for u in rows if u.has_role("qingxie")]


@router.get("/roles")
def list_role_users(q: str = "", role: str = "",
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_role_manager)):
    """可授予/已持有管理类身份的人，加上按学号或姓名的搜索。

    默认只列**已经有非学生身份的人**：全校几万人平铺出来没法用，要给某个
    学生加身份就搜他的学号。部长默认只看本部门的人，搜索不受限——他要从
    全校学生里挑人进部门。
    """
    query = db.query(User)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((User.name.ilike(like)) |
                             (User.student_id.ilike(like)))
    elif not user.has_full_access():
        query = query.filter(User.department_id == user.department_id)
    else:
        # roles 是 JSON 而不是 JSONB，没有包含运算符可用；按序列化后的文本
        # 匹配 "admin" 这种带引号的完整词，不会误命中别的值。
        def holds(r):
            return cast(User.roles, Text).like(f'%"{r}"%')
        wanted = [role] if role in ROLES else ["admin", "qingxie", "initiator"]
        query = query.filter(or_(*[holds(r) for r in wanted]))
    rows = query.order_by(User.student_id).limit(200).all()
    depts = {d.id: d.name for d in db.query(Department).all()}
    return [_row(u, user, depts) for u in rows]


class RoleBody(BaseModel):
    roles: list[str]
    qingxie_post: str = "member"
    department_id: str = ""
    supervisor_id: str = ""     # 部门成员归属哪位骨干


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
        target.department_id = dept_id
        target.supervisor_id = (_check_supervisor(db, target, body, dept_id)
                                if body.qingxie_post == "member" else None)
        if body.qingxie_post == "head":
            for old in (db.query(User)
                        .filter(User.department_id == dept_id,
                                User.qingxie_post == "head",
                                User.id != target.id).all()):
                old.qingxie_post = "member"
    else:
        # 不是青协了，层级与部门一并清掉，免得留下「非青协的部长」
        target.qingxie_post = "member"
        target.department_id = None
        target.supervisor_id = None

    after = (target.roles, target.qingxie_post, target.department_id)
    if before != after:
        label = lambda rs: "、".join(ROLE_LABELS.get(r, r) for r in rs) or "无"
        parts = [f"身份 {label(before[0])} → {label(after[0])}"]
        if before[1] != after[1]:
            parts.append(f"青协层级 {User.POST_LABELS.get(before[1], before[1])}"
                         f" → {User.POST_LABELS.get(after[1], after[1])}")
        if before[2] != after[2]:
            names = {d.id: d.name for d in db.query(Department).all()}
            parts.append(f"部门 {names.get(before[2], '无')} → "
                         f"{names.get(after[2], '无')}")
        audit_log(db, actor=user, action="update", target_type="user",
                  target_id=target.id,
                  target_label=f"{target.name}（{target.student_id}）",
                  summary="调整身份：" + "；".join(parts))
    db.commit()
    return {"ok": True}
