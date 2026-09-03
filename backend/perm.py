"""活动级权限：谁看得到、管得了哪个活动。

  · 管理员、会长团：全部活动。
  · 部长、部门成员：本部门（归口部门 = 自己的部门）的全部活动，加上自己
    被加进共管名单的。部门成员只能看，写请求在 deps._guard_trainee 被拦。
  · 部门骨干、活动发起方：只有自己被加进共管名单的。
  · 共管名单：创建者自动进入（不可被移除）；管理者之间权限相同，可互相
    添加/移除；被添加者必须持有 活动发起方/青协 身份。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session as OrmSession

from .models import Activity, ActivityManager, User, uid


def _dept_scoped(user: User) -> bool:
    """按归口部门看活动的人：部长与部门成员。"""
    return user.department_id is not None and user.post() in ("head", "member")


def managed_activity_ids(db: OrmSession, user: User) -> list[str] | None:
    """该用户可见的活动 id 列表；None = 不限。"""
    if user.has_full_access():
        return None
    ids = {r[0] for r in db.query(ActivityManager.activity_id)
           .filter(ActivityManager.user_id == user.id).all()}
    if _dept_scoped(user):
        ids |= {r[0] for r in db.query(Activity.id)
                .filter(Activity.approval_dept_id == user.department_id).all()}
    return list(ids)


def is_activity_manager(db: OrmSession, user: User, activity_id: str) -> bool:
    if user.has_full_access():
        return True
    if db.query(ActivityManager).filter_by(
            activity_id=activity_id, user_id=user.id).first() is not None:
        return True
    if _dept_scoped(user):
        act = db.get(Activity, activity_id)
        return act is not None and act.approval_dept_id == user.department_id
    return False


def writing_manager_ids(db: OrmSession, activity_id: str) -> list[str]:
    """共管名单里**真能操作**的人。部门成员在名单里也只能看，不算数。"""
    rows = (db.query(User)
            .join(ActivityManager, ActivityManager.user_id == User.id)
            .filter(ActivityManager.activity_id == activity_id).all())
    return [u.id for u in rows if u.can_write()]


def ensure_activity_manager(db: OrmSession, user: User, activity_id: str) -> None:
    if not is_activity_manager(db, user, activity_id):
        raise HTTPException(403, "您不是该活动的管理者，无法操作")


def activity_manager_ids(db: OrmSession, activity_id: str) -> list[str]:
    rows = (db.query(ActivityManager.user_id)
            .filter(ActivityManager.activity_id == activity_id).all())
    return [r[0] for r in rows]


def add_manager(db: OrmSession, activity_id: str, user: User,
                added_by: User) -> ActivityManager:
    if not user.is_manager_identity():
        raise HTTPException(400, "活动管理者仅限 活动发起方/青协 身份"
                                 f"（{user.name} 当前身份不含活动发起方/青协）")
    exists = db.query(ActivityManager).filter_by(
        activity_id=activity_id, user_id=user.id).first()
    if exists:
        raise HTTPException(409, f"{user.name} 已是本活动管理者")
    row = ActivityManager(id=uid("mgr"), activity_id=activity_id,
                          user_id=user.id, added_by=added_by.id)
    db.add(row)
    return row


def remove_manager(db: OrmSession, activity_id: str, user_id: str,
                   creator_id: str) -> None:
    if user_id == creator_id:
        raise HTTPException(409, "活动创建者不可被移除（可移除其他管理者）")
    row = db.query(ActivityManager).filter_by(
        activity_id=activity_id, user_id=user_id).first()
    if row is None:
        raise HTTPException(404, "该用户不是本活动管理者")
    db.delete(row)
