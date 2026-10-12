"""FastAPI 依赖：会话鉴权与权限。

令牌只从 Authorization 头读，一处例外都没有。浏览器直链下载（导出 Excel、
学习材料）发不出请求头，走一次性票据：前端先用带头的请求换一张票，票绑定
这一个网址、一分钟有效、用一次即作废（见 locks.issue_ticket）。地址栏里
再也没有会话令牌，日志、浏览器历史、Referer 里捡到的票也早已作废。

权限模型：
  · 身份（User.roles，可多个）：管理员 / 青协 / 活动发起方 / 学生
  · 青协层级（User.qingxie_post）：会长团 / 部长 / 部门骨干 / 部门成员
      - 平台公告、操作日志、意见反馈处理：仅管理员
      - 角色管理、数据总览：管理员、会长团（部长只管本部门骨干/成员）
      - 活动审批、志愿者档案：管理员、会长团、部长
      - 部门成员：能进工作台、能看，**任何写请求都在这里被拦**（学习权限）
  · 活动级（activity_managers，见 perm.py）：谁能看、能操作哪个活动

权限跟随当前身份：前端每个请求带 X-Identity（当前切到的身份视角），这里
套到 User 上（User.set_view），之后所有判权只认这一个身份。只有确实持有的
身份才采用，所以这个头只能收窄权限；没带、乱写、伪造的一律忽略，按全部
身份的并集。身份事实（/me 下发的身份、不能移除自己的管理员
身份、别人能否值班）按真实身份，见 models.User 的 real=True。
"""
from __future__ import annotations

import secrets
from datetime import timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as OrmSession

from . import config, locks, oplog
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


def _identity(request: Request) -> str:
    return request.headers.get("X-Identity", "").strip()


def _viewed(request: Request, user: User, identity: str) -> User:
    user.set_view(identity)
    oplog.mark_actor(request, user)
    return user


def current_user(request: Request,
                 db: OrmSession = Depends(db_session)) -> User:
    return _release(db, _viewed(request, _authorize(db, _bearer(request)),
                                _identity(request)))


def current_user_download(request: Request,
                          db: OrmSession = Depends(db_session)) -> User:
    """浏览器直链下载：认 Authorization 头，或一次性票据 ?ticket=。

    走这个依赖的都是导出和附件下载，操作日志要记下来。
    """
    oplog.mark_download(request)
    token = _bearer(request)
    if token:
        return _release(db, _viewed(request, _authorize(db, token),
                                    _identity(request)))
    ticket = request.query_params.get("ticket", "").strip()
    redeemed = locks.redeem_ticket(ticket, request.url.path)
    if redeemed is None:
        raise HTTPException(401, "下载链接已失效，请回到页面重新点一次")
    # 票上记着换票时的身份：<a> 直链带不了头，就用签发那一刻的视角
    user_id, identity = redeemed
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(401, "用户不存在")
    return _release(db, _viewed(request, user, identity))


# 部门成员的学习权限：非 GET 一律拦，下面两张表是例外。
# 试算类 POST 不写库，恰恰最该让新人看（一键分配试算、导入排班的预览、智能起草
# 都只回一份草稿）；
# 导出是 GET，方法判断拦不住，要点名禁。
# 按**处理函数名**匹配而不是拼 URL：函数名是路由表里的常量，URL 里有 id 会变。
TRAINEE_ALLOWED_WRITES = {"assign_preview", "assign_import",
                          "activity_draft", "structure_draft", "form_draft"}
# 导出一家子都要列上：只拦排班表的 "export" 的话，时长记录（带身份证号）
# 和报名问卷结果会漏掉，部门成员照样下得到。附件数接口也拦：前端先问数目，
# 在这一步就能提示「不能导出」，不必等直链落到一张报错页上。
TRAINEE_DENIED_READS = {
    "export", "export_hours",
    "export_answers", "export_answer_attachment_count", "export_answer_attachments",
    "export_applications", "export_application_attachment_count",
    "export_application_attachments",
    # 校外时长补登表：含身份证号，只有对外联络部那几个人能导
    "export_claims",
    # 操作日志导出本来只给管理员，列在这里是让「导出都点了名」这件事没有例外
    "export_oplogs",
    # 活动汇总：数据总览那一页的导出，只给管理员/会长团，同样点名
    "export_activities_summary",
}
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
    """仅系统管理员：平台公告、操作日志、意见反馈处理。"""
    if not user.is_admin():
        raise HTTPException(403, "需要管理员身份")
    return user


def require_admin_download(user: User = Depends(current_user_download)) -> User:
    """仅系统管理员的直链下载：操作日志导出。"""
    if not user.is_admin():
        raise HTTPException(403, "需要管理员身份")
    return user


def require_council(user: User = Depends(current_user)) -> User:
    """管理员 / 会长团：数据总览、部门维护。"""
    if not user.has_full_access():
        raise HTTPException(403, "需要 管理员/会长团 身份")
    return user


def require_council_download(user: User = Depends(current_user_download)) -> User:
    """同上的直链下载版：数据总览的活动汇总导出走 <a>，带不了 Authorization 头。"""
    if not user.has_full_access():
        raise HTTPException(403, "需要 管理员/会长团 身份")
    return user


def may_enter_approvals(db: OrmSession, user: User) -> bool:
    """能不能进「活动审批」页：管理员 / 会长团 / 部长，加上启用值班的部门里
    有值班资格的骨干（不在班时列表是空的，但要看得到现在谁在班、接得了班）。"""
    from .liaison import duty_eligible
    return bool(user.has_full_access() or user.is_head() or duty_eligible(db, user))


def require_approver(user: User = Depends(current_user),
                     db: OrmSession = Depends(db_session)) -> User:
    if not may_enter_approvals(db, user):
        raise HTTPException(403, "需要 管理员/会长团/部长 身份")
    return user


def require_claim_decider(user: User = Depends(current_user),
                          db: OrmSession = Depends(db_session)) -> User:
    """审批校外时长补登：对外联络部那摊事的同一批人（值班人/部长 + 会长团/管理员）。"""
    from .liaison import vb_decider
    if not vb_decider(db, user):
        raise HTTPException(403, "需要 对外联络部（值班人或部长）身份")
    return user


def require_claim_decider_download(user: User = Depends(current_user_download),
                                   db: OrmSession = Depends(db_session)) -> User:
    """同上的直链下载版：补登表导出走 <a>，带不了 Authorization 头。"""
    from .liaison import vb_decider
    if not vb_decider(db, user):
        raise HTTPException(403, "需要 对外联络部（值班人或部长）身份")
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
