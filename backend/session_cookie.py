"""登录会话 cookie 与跨站请求防护。

会话令牌只放在 HttpOnly cookie 里：页面脚本读不到它，就算有人往页面里注入了
脚本，也偷不走登录态。代价是浏览器会自动带上 cookie，别的网站诱导用户的浏览器
发请求时也会带（跨站请求伪造，CSRF）。挡这一招靠两层：

  · cookie 设 SameSite=Lax：别的网站用表单、图片、脚本发来的请求都不带它，
    只有用户在别处点链接、顶层打开本站地址时才带——那只会是 GET。
  · 所有写请求（POST/PUT/PATCH/DELETE）必须带自定义头 X-YVRUC。别的网站的
    页面要发带自定义头的跨域请求，浏览器先发预检，本站不回 CORS 许可，请求
    就发不出去；表单提交根本加不了请求头。缺头的写请求在这里直接 403，
    连处理函数都进不去。

用 Lax 不用 Strict：Strict 下从企业微信消息、别的站点点链接进来的那一次顶层
请求不带 cookie。前端是单页应用，页面本身不要登录，这一层影响不大；但导出、
附件这类直链下载是顶层导航，Strict 会让「从外面点进来马上下载」这类路径时灵
时不灵，各家浏览器（尤其旧版 Safari、微信内置浏览器）的处理也不一致。写请求
已经有自定义头兜着，Strict 多挡的那一点用不上。

cookie 的 Path 是 /api：只有接口请求带它，静态资源、页面都不带。
"""
from __future__ import annotations

import json
from urllib.parse import urlparse

from starlette.responses import Response

from . import config

SESSION_COOKIE = "yvruc_session"
CSRF_HEADER = "X-YVRUC"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
COOKIE_PATH = "/api"
MAX_AGE = int(config.SESSION_TTL.total_seconds())

CSRF_REJECTED = "请求缺少必要的标识，请刷新页面后重试"


def _https_hosts() -> frozenset[str]:
    return frozenset(urlparse(u).hostname or ""
                     for u in (config.SITE_URL, *config.PUBLIC_BASE_URLS)
                     if u.startswith("https://"))


def cookie_secure(scheme: str, host: str) -> bool:
    """cookie 要不要带 Secure。

    请求是 https，或者请求的域名就是配置里登记的 https 站点（YVRUC_SITE_URL、
    YVRUC_PUBLIC_BASE_URL），就带。只看请求协议的话，全靠反代一路把
    X-Forwarded-Proto 传对：内测形态前面还有一层宿主机 nginx，它漏传这个头，
    后端就以为是 http，令牌 cookie 不带 Secure，浏览器在任何 http 请求上都会
    把它明文发出去。
    用局域网 IP、localhost 走 http 调试时域名对不上，按请求协议回 False：
    浏览器不会存 Secure cookie，写死 True 就登不进去。
    """
    if scheme == "https":
        return True
    name = host.rsplit(":", 1)[0].strip("[]").lower() if host else ""
    return bool(name) and name in _https_hosts()


def set_session_cookie(response: Response, token: str, *, secure: bool) -> None:
    """下发会话 cookie。到期时间和服务端会话一致，续期时再下发一次。
    secure 由 cookie_secure 算。"""
    response.set_cookie(SESSION_COOKIE, token, max_age=MAX_AGE, path=COOKIE_PATH,
                        httponly=True, samesite="lax", secure=secure)


def clear_session_cookie(response: Response, *, secure: bool) -> None:
    response.delete_cookie(SESSION_COOKIE, path=COOKIE_PATH,
                           httponly=True, samesite="lax", secure=secure)


def _header(scope, name: bytes) -> bytes | None:
    for k, v in scope.get("headers") or ():
        if k == name:
            return v
    return None


class SessionCookieMiddleware:
    """纯 ASGI 中间件，管两件事：

    1. 写请求缺 X-YVRUC 头的，直接回 403。
    2. 鉴权依赖给会话续了期（request.state.session_renew），就在响应上重新
       下发 cookie。放在这里而不是各接口里：接口返回的有 JSON、文件、流，
       FastAPI 的「依赖里改 Response」对直接返回 Response 对象的接口不生效，
       漏掉的那些续期后 cookie 照旧按老时间过期，用着用着就被踢下线。
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            await self.app(scope, receive, send)
            return
        if (scope["method"] not in SAFE_METHODS
                and not _header(scope, CSRF_HEADER.lower().encode())):
            body = json.dumps({"detail": CSRF_REJECTED}, ensure_ascii=False).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                token = (scope.get("state") or {}).get("session_renew")
                if token:
                    r = Response()
                    host = (_header(scope, b"host") or b"").decode("latin-1")
                    set_session_cookie(r, token,
                                       secure=cookie_secure(scope.get("scheme", ""), host))
                    message = {**message, "headers": [
                        *message.get("headers", []),
                        *[(k, v) for k, v in r.raw_headers if k == b"set-cookie"]]}
            await send(message)

        await self.app(scope, receive, send_wrapper)
