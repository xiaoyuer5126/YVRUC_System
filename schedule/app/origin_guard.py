from __future__ import annotations

from urllib.parse import urlparse

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import get_settings

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """照抄社区 app/core/csrf.py 的那一份。

    这个服务认的是社区那张 cookie，所以社区有的 CSRF 防线它一条都不能少。
    cookie 本身是 SameSite=Lax，跨站 POST 已经带不上；这层是纵深防御，
    挡的是同站但非本页发起的写请求。

    ⚠️ **这层管不到 WebSocket。** Starlette 的 BaseHTTPMiddleware 只处理
    http scope，websocket 握手直接穿过去。而 WebSocket 握手没有预检、
    SameSite 的保护也不完整——跨站 WebSocket 劫持是真实存在的攻击。
    所以进度推送那个 WS 端点必须**在处理函数里自己校验 Origin**，
    照 check_origin() 用。加 WS 端点时不要忘。
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method.upper() not in WRITE_METHODS:
            return await call_next(request)

        origin = request.headers.get("origin")
        referer = request.headers.get("referer")
        if not origin and not referer:
            return await call_next(request)

        allowed = set(get_settings().allowed_origins)
        candidate = origin or _origin_from_referer(referer or "")
        if not candidate or candidate not in allowed:
            # 回 JSON 而不是纯文本：前端的 parseError 只认 detail，
            # 否则界面上只会显示「请求失败（403）」，看不出是白名单的问题。
            # 把当前来源回显出来，照着往 CORS_ORIGINS 里加就行。
            return JSONResponse(
                {"detail": f"来源 {candidate or '(空)'} 不在白名单里，请检查 .env 的 CORS_ORIGINS"},
                status_code=status.HTTP_403_FORBIDDEN,
            )
        return await call_next(request)


def check_origin(origin: str | None) -> bool:
    """WebSocket 握手用。没有 Origin 头的一律拒绝——浏览器一定会带上，
    不带的是脚本客户端，而这个端点只服务浏览器。
    """
    if not origin:
        return False
    return origin in set(get_settings().allowed_origins)


def _origin_from_referer(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"
