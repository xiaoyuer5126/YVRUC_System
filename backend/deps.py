"""FastAPI 依赖：会话鉴权与权限。

令牌只从 Authorization 头读，一处例外都没有。浏览器直链下载（导出 Excel、
学习材料）发不出请求头，走一次性票据：前端先用带头的请求换一张票，票绑定
这一个网址、一分钟有效、用一次即作废（见 locks.issue_ticket）。地址栏里
再也没有会话令牌，日志、浏览器历史、Referer 里捡到的票也早已作废。

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

from . import config, locks
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


def _release(db: OrmSession, user: User) -> User:
    """鉴权读完就把**数据库连接还回池子**，再交给处理函数。

    同步的依赖和同步的处理函数各在线程池里跑一次，中间要重新排队等一条线程。
    不还的话，这段排队是「占着连接等线程」：抢报时线程全被等连接的请求占着，
    连接全被等线程的请求占着，两边互相等到连接池 30 秒超时（2026-09-26 压测：
    300 人抢 200 个名额，大半请求卡 30 秒以上）。expire_on_commit=False，
    commit 之后 user 照样能读。
    """
    db.commit()
    return user


def current_user(request: Request,
                 db: OrmSession = Depends(db_session)) -> User:
    return _release(db, _authorize(db, _bearer(request)))


def current_user_download(request: Request,
                          db: OrmSession = Depends(db_session)) -> User:
    """浏览器直链下载：认 Authorization 头，或一次性票据 ?ticket=。"""
    token = _bearer(request)
    if token:
        return _release(db, _authorize(db, token))
    ticket = request.query_params.get("ticket", "").strip()
    user_id = locks.redeem_ticket(ticket, request.url.path)
    if user_id is None:
        raise HTTPException(401, "下载链接已失效，请回到页面重新点一次")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return _release(db, user)


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
                              "message": "部门成员为学习权限，本次操作不会保存"})


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


def require_approver(user: User = Depends(current_user),
                     db: OrmSession = Depends(db_session)) -> User:
    """能进「活动审批」页的人：管理员 / 会长团 / 部长，加上启用值班的部门里
    有值班资格的骨干（不在班时列表是空的，但要看得到现在谁在班、接得了班）。"""
    from .liaison import duty_eligible
    if not (user.has_full_access() or user.is_head() or duty_eligible(db, user)):
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
