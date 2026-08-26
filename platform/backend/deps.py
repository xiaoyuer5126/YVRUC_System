"""FastAPI 依赖：会话鉴权与权限。

权限模型（两层）：
  · 身份（User.roles，可多个）：管理员 / 老师 / 青协 / 学生
      - 工作台入口：老师 / 青协 / 管理员
      - 数据总览 / 集成设置：仅管理员
      - 用户与信用：管理员 / 青协
  · 活动级（activity_managers）：非管理员只能操作自己管理的活动
"""
from __future__ import annotations

import secrets

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from .database import db_session
from .models import MANAGER_ROLES, Session, User


def _token_from(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:].strip()
    return request.query_params.get("token", "").strip()


def current_user(request: Request,
                 db: OrmSession = Depends(db_session)) -> User:
    token = _token_from(request)
    if not token:
        raise HTTPException(401, "未登录")
    row = db.get(Session, token)
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return user


def require_manager(user: User = Depends(current_user)) -> User:
    """工作台入口：老师 / 青协 / 管理员身份。"""
    if not user.is_manager_identity():
        raise HTTPException(403, "需要 老师/青协/管理员 身份")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if not user.is_admin():
        raise HTTPException(403, "需要管理员身份")
    return user


def require_users_page(user: User = Depends(current_user)) -> User:
    """用户与信用页：管理员 / 青协。"""
    if not (user.is_admin() or user.has_role("qingxie")):
        raise HTTPException(403, "需要 管理员/青协 身份")
    return user


def create_session(db: OrmSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(token=token, user_id=user.id))
    db.commit()
    return token
