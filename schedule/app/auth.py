from __future__ import annotations

import time
import uuid
from dataclasses import dataclass

import httpx
from fastapi import HTTPException, Request, status

from app.config import get_settings

# 社区里被视为管理员的角色。和社区代码里那几十处 `role in {"admin", "dev"}` 对齐。
COMMUNITY_STAFF_ROLES = frozenset({"admin", "dev"})

# **能看别人名单的角色，只有 dev。**
#
# 刻意比 COMMUNITY_STAFF_ROLES 窄一档：排班项目里存着全员的姓名、身份证号和
# 联系方式，这是全站接触面最敏感的一份数据。admin 的职责是内容治理（处理举报、
# 禁言、审推广），不需要读志愿者名单；把它做成 is_staff 只会让接触这批身份证号
# 的人无谓地多一倍。
COMMUNITY_DEV_ROLE = "dev"


@dataclass(frozen=True)
class Identity:
    """社区确认过的身份。这个服务对「你是谁」的唯一信息来源。"""

    user_id: uuid.UUID
    nickname: str
    email: str
    role: str

    @property
    def is_staff(self) -> bool:
        return self.role in COMMUNITY_STAFF_ROLES

    @property
    def is_dev(self) -> bool:
        """能不能跨用户读写项目。见 COMMUNITY_DEV_ROLE 上面那段。"""
        return self.role == COMMUNITY_DEV_ROLE


# cookie 值 -> (到期时间戳, Identity)。
# 缓存的是**校验结果**而不是身份本身：过期后重新问社区，所以被封号的人
# 最多在 identity_cache_seconds 之后失效。上限防止内存被无效 cookie 撑爆。
_CACHE: dict[str, tuple[float, Identity]] = {}
_CACHE_MAX = 512


def _cache_get(token: str) -> Identity | None:
    hit = _CACHE.get(token)
    if not hit:
        return None
    expires_at, identity = hit
    if expires_at <= time.monotonic():
        _CACHE.pop(token, None)
        return None
    return identity


def _cache_put(token: str, identity: Identity, ttl: int) -> None:
    if len(_CACHE) >= _CACHE_MAX:
        # 满了就整个清空。这里不需要 LRU：条目最多活 ttl 秒，清空的代价
        # 只是几次多余的内网请求。
        _CACHE.clear()
    _CACHE[token] = (time.monotonic() + ttl, identity)


async def resolve_identity(token: str) -> Identity | None:
    """拿 cookie 向社区 API 换身份。换不到就是没登录。

    这个服务**不持有 AUTH_SECRET_KEY**，也就无法自己解 JWT——这是有意的：
    它只能问「这是谁」，不能签发身份。即使这个工具被完全攻破，攻击者也造不出
    一个社区管理员，更碰不到社区的密码哈希。
    """
    if not token:
        return None
    cached = _cache_get(token)
    if cached:
        return cached

    settings = get_settings()
    url = f"{settings.community_api_url.rstrip('/')}/api/me"
    try:
        # trust_env=False：这是 compose 网络内部的调用，绝不能被宿主机的
        # HTTP_PROXY/HTTPS_PROXY 环境变量劫持。httpx 默认读这些变量，
        # 一旦机器上配了代理，用户的会话 cookie 就会被送到代理服务器去。
        async with httpx.AsyncClient(timeout=5.0, trust_env=False) as client:
            resp = await client.get(url, cookies={settings.auth_cookie_name: token})
    except httpx.HTTPError as exc:
        # 社区 API 不可达时**不能当成「未登录」**放行任何东西，也不该把它
        # 说成 401 让人以为是自己登录过期了。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="暂时无法校验登录状态，请稍后再试",
        ) from exc

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        return None
    if resp.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="暂时无法校验登录状态，请稍后再试",
        )

    data = resp.json()
    try:
        identity = Identity(
            user_id=uuid.UUID(str(data["id"])),
            nickname=str(data.get("nickname") or ""),
            email=str(data.get("email") or ""),
            role=str(data.get("role") or ""),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="登录状态返回格式异常",
        ) from exc

    _cache_put(token, identity, settings.identity_cache_seconds)
    return identity


def session_token(request: Request) -> str:
    return request.cookies.get(get_settings().auth_cookie_name, "")


async def require_login(request: Request) -> Identity:
    """**登录就能用，没有白名单。**

    和 bv2008 不一样：那个工具会代你去操作志愿北京上的团体数据，动的是别人的
    平台，所以要审批；这个工具只在你自己的项目里算数，算错了也只影响你自己
    的那张表。所以这里只要求「是本站用户」。

    注意它**不是**「谁都能看谁的项目」——每个项目按 owner 隔离，见 store.py。
    """
    identity = await resolve_identity(session_token(request))
    if identity is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="请先登录")
    return identity
