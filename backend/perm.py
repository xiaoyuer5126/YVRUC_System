"""活动级权限：多人共管。

  · 创建者自动成为管理者（created_by 仅作记录，不产生额外权限）；
  · 管理者之间权限完全相同，可互相添加/移除（创建者不可被移除）；
  · 被添加者必须持有 活动发起方/青协 身份；管理员无需添加，天然可见/可操作全部。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session as OrmSession

from .models import ActivityManager, User, uid


def managed_activity_ids(db: OrmSession, user: User) -> list[str] | None:
    """该用户可见的活动 id 列表；None = 不限（管理员）。"""
    if user.is_admin():
        return None
    rows = (db.query(ActivityManager.activity_id)
            .filter(ActivityManager.user_id == user.id).all())
    return [r[0] for r in rows]


def is_activity_manager(db: OrmSession, user: User, activity_id: str) -> bool:
    if user.is_admin():
        return True
    return db.query(ActivityManager).filter_by(
        activity_id=activity_id, user_id=user.id).first() is not None


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
