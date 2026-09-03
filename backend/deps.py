"""FastAPI 依赖：会话鉴权与权限。

令牌只从 Authorization 头读。浏览器直链下载（导出 Excel）没法带头，
单独用 current_user_download 依赖，只有那一个路由接受 ?token=——
放开全站会让令牌进 nginx 访问日志、浏览器历史和外链 Referer。

权限模型：
  · 身份（User.roles，可多个）：管理员 / 青协 / 活动发起方 / 学生
  · 青协层级（User.qingxie_post）：会长团 / 部长 / 部门骨干 / 部门成员
      - 集成设置：仅管理员
      - 角色管理、数据总览：管理员、会长团（部长只管本部门骨干/成员）
      - 活动审批、志愿者档案：管理员、会长团、部长
      - 部门成员：能进工作台、能看，**任何写请求都在这里被拦**（学习权限）
  · 活动级（activity_managers，见 perm.py）：谁能看、能操作哪个活动
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from . import config
from .database import db_session
from .models import Session, User, now


def _authorize(db: OrmSession, token: str) -> User:
    if not token:
        raise HTTPException(401, "未登录")
    row = db.get(Session, token)
    if row is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    if row.expires_at <= now():
        db.delete(row)
        db.commit()
        raise HTTPException(401, "登录已过期，请重新登录")
    user = db.get(User, row.user_id)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return user


def _bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    return auth[7:].strip() if auth.startswith("Bearer ") else ""


def current_token(request: Request) -> str:
    """当前请求所用的令牌（退出登录时只吊销这一个会话）。"""
    return _bearer(request)


def current_user(request: Request,
                 db: OrmSession = Depends(db_session)) -> User:
    return _authorize(db, _bearer(request))


def current_user_download(request: Request,
                          db: OrmSession = Depends(db_session)) -> User:
    """仅供浏览器直链下载的路由使用：额外接受 ?token=。"""
    return _authorize(db, _bearer(request)
                      or request.query_params.get("token", "").strip())


# 部门成员的学习权限：非 GET 一律拦，下面两张表是例外。
# 试算类 POST 不写库，恰恰最该让新人看；导出是 GET，方法判断拦不住，要点名禁。
# 按**处理函数名**匹配而不是拼 URL：函数名是路由表里的常量，URL 里有 id 会变。
TRAINEE_ALLOWED_WRITES = {"assign_preview"}
TRAINEE_DENIED_READS = {"export"}
TRAINEE_READONLY = "TRAINEE_READONLY"


def _guard_trainee(request: Request, user: User) -> None:
    """部门成员可以走完整个界面流程，但请求到这里就停：一个字节都不会写库。

    放在权限依赖里而不是各个接口里——管理端写接口有五十多个，逐个加判断
    必然漏，漏一个就是让不该写的人写进去了。依赖在处理函数之前执行，
    抛异常时事务里什么都没有，安全性由结构保证。
    """
    if not user.is_trainee():
        return
    handler = getattr(request.scope.get("endpoint"), "__name__", "")
    if request.method == "GET":
        if handler in TRAINEE_DENIED_READS:
            raise HTTPException(403, {"code": TRAINEE_READONLY,
                                      "message": "部门成员为学习权限，不能导出"})
        return
    if handler in TRAINEE_ALLOWED_WRITES:
        return
    raise HTTPException(403, {"code": TRAINEE_READONLY,
                              "message": "部门成员为学习权限：可以走完流程，"
                                         "但这一步不会真正保存"})


def require_manager(request: Request,
                    user: User = Depends(current_user)) -> User:
    """工作台入口：活动发起方 / 青协 / 管理员身份。"""
    if not user.is_manager_identity():
        raise HTTPException(403, "需要 活动发起方/青协/管理员 身份")
    _guard_trainee(request, user)
    return user


def require_manager_download(request: Request,
                             user: User = Depends(current_user_download)) -> User:
    if not user.is_manager_identity():
        raise HTTPException(403, "需要 活动发起方/青协/管理员 身份")
    _guard_trainee(request, user)
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    """仅系统管理员：集成设置。"""
    if not user.is_admin():
        raise HTTPException(403, "需要管理员身份")
    return user


def require_council(user: User = Depends(current_user)) -> User:
    """管理员 / 会长团：数据总览、部门维护。"""
    if not user.has_full_access():
        raise HTTPException(403, "需要 管理员/会长团 身份")
    return user


def require_approver(user: User = Depends(current_user)) -> User:
    """能进「活动审批」页的人：管理员 / 会长团 / 部长。"""
    if not (user.has_full_access() or user.is_head()):
        raise HTTPException(403, "需要 管理员/会长团/部长 身份")
    return user


def require_archive_page(user: User = Depends(current_user)) -> User:
    """志愿者档案（全校名册）：管理员 / 会长团 / 部长。"""
    if not (user.has_full_access() or user.is_head()):
        raise HTTPException(403, "需要 管理员/会长团/部长 身份")
    return user


def create_session(db: OrmSession, user: User) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(token=token, user_id=user.id,
                   expires_at=now() + timedelta(hours=config.SESSION_HOURS)))
    db.commit()
    return token
