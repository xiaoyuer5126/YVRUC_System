"""角色与职务管理。仅管理员可见可用——这一页能改的是权限本身。

能改什么：
  · 身份 roles（管理员/青协/活动发起方/学生，可多选）
  · 青协职务 qingxie_post（会长/部长/干事）与所属部门

不能改什么：
  · 用户类型（老师/学生）——由数智人大下发，平台不是它的源头；
  · 自己的管理员身份——见 _guard_self_demotion。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Text, cast, or_
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from ..database import db_session
from ..deps import require_admin
from ..models import (Activity, ActivityManager, Department, ROLES,
                      ROLE_LABELS, User)

router = APIRouter(prefix="/api/staff", tags=["roles"])

POSTS = ("president", "head", "member")
# 已结束的活动是终态、只读，没人管也不要紧；未结束的才需要有人盯着
LIVE_STATUSES = ("draft", "pending", "approved", "published", "closed")


def _guard_self_demotion(actor: User, target: User, roles: list[str]) -> None:
    """不许把自己的管理员身份去掉。

    这一条就足以保证系统里永远至少有一个管理员：要让管理员归零，必然有人
    得移除自己。所以不必再算「是不是最后一个管理员」那种复杂条件。
    但必须拦在后端——前端不显示按钮挡不住直接调接口。
    """
    if actor.id == target.id and actor.is_admin() and "admin" not in roles:
        raise HTTPException(400, "不能移除自己的管理员身份；"
                                 "如需交接，请先由另一位管理员操作")


def _orphaned_activities(db: OrmSession, target: User,
                         roles: list[str]) -> list[str]:
    """撤掉管理身份后，哪些未结束的活动会变成没人管。

    活动管理者必须持有 发起方/青协 身份（见 perm.add_manager）。身份一撤，
    require_manager 第一道闸就把他挡在外面，名下所有活动一个都进不去——
    共管记录还躺在库里但已经没用了。他若是某个还在跑的活动的唯一管理者，
    那个活动当场没人管，而操作的人完全看不出自己制造了这个后果。
    """
    still_manager = "admin" in roles or bool(set(roles) & {"initiator", "qingxie"})
    if still_manager:
        return []
    mine = [r.activity_id for r in db.query(ActivityManager)
            .filter(ActivityManager.user_id == target.id).all()]
    orphans = []
    for aid in mine:
        act = db.get(Activity, aid)
        if act is None or act.status not in LIVE_STATUSES:
            continue
        others = (db.query(ActivityManager)
                  .filter(ActivityManager.activity_id == aid,
                          ActivityManager.user_id != target.id).count())
        if not others:
            orphans.append(act.title)
    return orphans


@router.get("/roles")
def list_role_users(q: str = "", role: str = "",
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_admin)):
    """可授予/已持有管理类身份的人，加上按学号或姓名的搜索。

    默认只列**已经有非学生身份的人**：全校几万人平铺出来没法用，
    要给某个学生加身份就搜他的学号。
    """
    query = db.query(User)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((User.name.ilike(like)) |
                             (User.student_id.ilike(like)))
    else:
        # roles 是 JSON 而不是 JSONB，没有包含运算符可用；按序列化后的文本
        # 匹配 "admin" 这种带引号的完整词，不会误命中别的值。
        def holds(r):
            return cast(User.roles, Text).like(f'%"{r}"%')
        wanted = [role] if role in ROLES else ["admin", "qingxie", "initiator"]
        query = query.filter(or_(*[holds(r) for r in wanted]))
    rows = query.order_by(User.student_id).limit(200).all()
    depts = {d.id: d.name for d in db.query(Department).all()}
    return [{
        "id": u.id, "student_id": u.student_id, "name": u.name,
        "college": u.college, "user_type": u.user_type,
        "roles": u.roles or [], "role_label": u.active_role_label(),
        "qingxie_post": u.qingxie_post,
        "post_label": u.post_label() if u.has_role("qingxie") else "",
        "department_id": u.department_id or "",
        "department": depts.get(u.department_id, ""),
        "is_me": u.id == user.id,
    } for u in rows]


class RoleBody(BaseModel):
    roles: list[str]
    qingxie_post: str = "member"
    department_id: str = ""


@router.put("/roles/{user_id}")
def set_roles(user_id: str, body: RoleBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_admin)):
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(404, "用户不存在")

    roles = [r for r in dict.fromkeys(body.roles) if r]
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise HTTPException(400, f"未知身份：{'、'.join(bad)}")
    if not roles:
        raise HTTPException(400, "至少要保留一个身份")
    _guard_self_demotion(user, target, roles)

    orphans = _orphaned_activities(db, target, roles)
    if orphans:
        raise HTTPException(
            409, f"{target.name} 还是这些未结束活动的唯一管理者："
                 f"{'、'.join(orphans[:5])}"
                 + (f" 等 {len(orphans)} 个" if len(orphans) > 5 else "")
                 + "。撤销身份后这些活动将无人管理，请先为它们添加其他管理者。")

    if body.qingxie_post not in POSTS:
        raise HTTPException(400, "青协职务不合法")

    dept_id = body.department_id.strip() or None
    if dept_id and db.get(Department, dept_id) is None:
        raise HTTPException(404, "部门不存在")

    before = (list(target.roles or []), target.qingxie_post, target.department_id)
    target.roles = roles
    if "qingxie" in roles:
        target.qingxie_post = body.qingxie_post
        target.department_id = dept_id
        # 部长是「本部门的」，没有部门的部长不成立，审批也找不到人
        if body.qingxie_post == "head" and not dept_id:
            raise HTTPException(400, "部长必须指定所属部门")
        if body.qingxie_post == "head":
            for old in (db.query(User)
                        .filter(User.department_id == dept_id,
                                User.qingxie_post == "head",
                                User.id != target.id).all()):
                old.qingxie_post = "member"
    else:
        # 不是青协了，职务与部门一并清掉，免得留下「非青协的部长」
        target.qingxie_post = "member"
        target.department_id = None

    after = (target.roles, target.qingxie_post, target.department_id)
    if before != after:
        label = lambda rs: "、".join(ROLE_LABELS.get(r, r) for r in rs) or "无"
        parts = [f"身份 {label(before[0])} → {label(after[0])}"]
        if before[1] != after[1]:
            parts.append(f"青协职务 {User.POST_LABELS.get(before[1], before[1])}"
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
