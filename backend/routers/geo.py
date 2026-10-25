"""管理端签到地图的瓦片转发：浏览器 → 本站 → 天地图。

为什么不让浏览器直连天地图：浏览器端 tk 靠 Referer 域名白名单保护，而 AdGuard
隐身模式之类的插件会改写或去掉第三方请求的 Referer，天地图一律 403，地图整块
灰掉、页面上不报任何错。由服务器去取，Referer 就不经过用户的浏览器，前端也
拿不到任何 tk。

tk（见 config）：
· 只用**服务端 tk**，天地图按服务器出口 IP 白名单校验，与浏览器无关；
· 没配 = 不画地图，前端只画点和圈。浏览器端 tk 不能拿来兜底：天地图对它只认
  真浏览器，服务器发出的请求带什么 Referer 都被拒（见 _upstream_key）。

额度（每个 tk 每天 10000 次）：
· 同一张瓦片只向天地图取一次，存在磁盘上 TILE_CACHE_DAYS 天。签到点就那么几个
  校区，来回看的是同一批瓦片，缓存命中之后不再花额度；
· Redis 按天数真正打到天地图的请求，到九成记警告，到上限直接 503、不再打上游。
  过期的缓存在取不到新图时（超额、上游挂了）照旧拿出来用：旧图总比灰块强。

防滥用：瓦片是 Leaflet 用 <img> 拉的，带不上登录令牌（令牌在 localStorage），
所以管理端打开地图时先领一张短期签名令牌（HMAC，含过期时间，不落库），挂在
瓦片地址的查询串上。缓存命中也要验令牌——不然这就是一个谁都能用的免费瓦片源，
额度虽然有缓存兜着，出口带宽和磁盘是我们的。

缓存放在后端而不是 nginx（proxy_cache + auth_request）：那样每张瓦片仍要为验
令牌打一次后端，省下的只是读一个小文件；而「缓存命中不计数、过期缓存兜底、
超额 503」这几条放在一处写、一处测，比拆在 nginx 配置和后端两边好维护。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response

from .. import config, locks
from ..deps import require_manager
from ..models import User, now

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/geo", tags=["geo"])

TILE_BASE = "/api/geo/tiles"
# 只放 GeoMap 实际用到的两层：矢量底图 + 矢量注记。别的层要用再加，
# 放开了就是替别人转发影像图。
LAYERS = ("vec", "cva")
MIN_ZOOM, MAX_ZOOM = 1, 18          # 天地图 _w 的级别从 1 开始
UPSTREAM = "https://t{n}.tianditu.gov.cn/{layer}_w/wmts"
# 浏览器拿到的缓存时长：瓦片地址里带着令牌，令牌一换地址就变，浏览器缓存
# 自然跟着失效，所以这里可以给长
CACHE_CONTROL = f"public, max-age={config.TILE_CACHE_DAYS * 86400}"

# 同步客户端、进程内复用：瓦片一次来十几张，每张都重新握一遍 TLS 太浪费。
#
# **别给上游设浏览器样的 User-Agent。** 天地图靠 UA 判断来的是浏览器还是服务器，
# 再要求「浏览器就用浏览器端 tk、服务器就用服务端 tk」：带服务端 tk 却用
# Mozilla 开头的 UA → 403 `301013「Key权限类型为:服务器端，请使用服务器端访问」`
# （2026-09-30 生产上就是这么 502 的，换成 httpx 默认 UA 立刻 200）。
_http = httpx.Client(
    timeout=config.TILE_UPSTREAM_TIMEOUT,
    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
# httpx 在 INFO 级别会把完整网址写进日志，tk 就在查询串里
logging.getLogger("httpx").setLevel(logging.WARNING)


# ======================= tk =======================

def _upstream_key() -> tuple[str, dict[str, str]] | None:
    """(tk, 额外请求头)；没配可用的 tk 返回 None。

    **只用服务端 tk，不拿浏览器端那把兜底。** 浏览器端 tk 认的是「真浏览器 +
    来源域名白名单」，由服务器发出去的请求必然被拒（403 `301012
    「Key权限类型为:浏览器端，请使用浏览器访问」`，带什么 Referer 都一样，
    2026-09-30 实测三种 Referer 全拒）。留着这条兜底只会把「没配」伪装成
    502，不如如实说没配——前端会退化成只画点和圈，功能照旧。
    """
    if config.TIANDITU_TK_SERVER:
        return config.TIANDITU_TK_SERVER, {}
    return None


def tiles_configured() -> bool:
    return _upstream_key() is not None


# ======================= 令牌 =======================

def _signing_key() -> bytes:
    # 复用服务端已有的密钥，按用途派生一把：同一把原始密钥不直接拿去干两件事，
    # 这里泄露的签名也推不回身份证号加密用的那把
    return hmac.new(config.ID_CARD_KEY.encode(), b"yvruc:geo-tiles:v1",
                    hashlib.sha256).digest()


def _sign(exp: int, user_id: str) -> str:
    return hmac.new(_signing_key(), f"{exp}.{user_id}".encode(),
                    hashlib.sha256).hexdigest()[:32]


def issue_token(user_id: str, at: float | None = None) -> tuple[str, int]:
    """签一张瓦片令牌，返回 (令牌, 过期的 Unix 时间戳)。

    令牌里带着签发对象的 user_id，签名同时覆盖它和过期时间：同一个系统里
    每个管理者拿到的地址不一样，一个人的令牌用的额度记在他自己头上，刷屏
    不会把共用的那把 tk 额度一起耗光。

    过期时间对齐到整点：同一个人、同一个小时里领到的令牌一模一样，瓦片地址
    也就一样，浏览器缓存能在几次打开之间共用。代价是有效期在
    TILE_TOKEN_HOURS 到 TILE_TOKEN_HOURS+1 小时之间浮动，无所谓。
    """
    at = time.time() if at is None else at
    exp = (int(at) // 3600 + config.TILE_TOKEN_HOURS + 1) * 3600
    return f"{exp}.{user_id}.{_sign(exp, user_id)}", exp


def verify_token(token: str) -> str | None:
    """验签，通过则返回令牌里的 user_id，验不过（含过期）返回 None。"""
    exp_s, _, rest = (token or "").partition(".")
    user_id, _, sig = rest.partition(".")
    if not exp_s.isdigit() or not user_id or not sig:
        return None
    if not hmac.compare_digest(sig, _sign(int(exp_s), user_id)):
        return None
    return user_id if int(exp_s) > time.time() else None


@router.get("/tile-token")
def tile_token(user: User = Depends(require_manager)):
    """管理端打开地图时领瓦片令牌；快过期时前端再来领一次。"""
    if not tiles_configured():
        return {"configured": False}
    token, exp = issue_token(user.id)
    return {"configured": True, "tile_base": TILE_BASE, "token": token,
            "expires_at": datetime.fromtimestamp(exp, timezone.utc).isoformat(),
            # 相对秒数：前端按它排下一次领取，不受用户电脑时钟不准的影响
            "expires_in": max(0, exp - int(time.time()))}


# ======================= 瓦片 =======================

def _int(raw: str, lo: int, hi: int) -> int:
    if not raw.isdigit() or len(raw) > 6 or not lo <= int(raw) <= hi:
        raise HTTPException(400, "瓦片坐标不对")
    return int(raw)


def _image_type(data: bytes) -> str | None:
    """按文件头认图片类型。天地图出错时常常回 200 + 一页 XML/HTML，不能照单全收。"""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _tile_response(data: bytes) -> Response:
    return Response(content=data, media_type=_image_type(data),
                    headers={"Cache-Control": CACHE_CONTROL})


def _read(path: Path) -> bytes | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data if _image_type(data) else None


def _write(path: Path, data: bytes) -> None:
    """先写临时文件再改名：多个进程同时取同一张瓦片时，谁也读不到写了一半的图。"""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}")
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except OSError:
        # 盘满、没权限：这张照样给出去，只是下次还得再取
        logger.exception("瓦片缓存写不进去：%s", path)


def _fail(stale: bytes | None, status: int, detail: str) -> Response:
    """取不到新图：有旧的就给旧的，没有才报错。上游的错误页一律不透传。"""
    if stale is not None:
        return _tile_response(stale)
    raise HTTPException(status, detail)


@router.get("/tiles/{layer}/{z}/{x}/{y}")
def tile(layer: str, z: str, x: str, y: str, t: str = ""):
    """一张瓦片。令牌先验：没令牌的人连参数对不对都不必知道。"""
    user_id = verify_token(t)
    if user_id is None:
        raise HTTPException(401, "地图令牌无效或已过期，请刷新页面")
    if layer not in LAYERS:
        raise HTTPException(400, "不支持的图层")
    zi = _int(z, MIN_ZOOM, MAX_ZOOM)
    xi = _int(x, 0, 2 ** zi - 1)
    yi = _int(y, 0, 2 ** zi - 1)
    key = _upstream_key()
    if key is None:
        raise HTTPException(404, "没有配置天地图")

    path = Path(config.TILE_CACHE_DIR) / layer / str(zi) / str(xi) / str(yi)
    cached = _read(path)
    if cached is not None:
        try:
            age = time.time() - path.stat().st_mtime
        except OSError:
            age = float("inf")
        if age < config.TILE_CACHE_DAYS * 86400:
            return _tile_response(cached)

    # 走到这里才会花额度
    day = now().date().isoformat()
    if locks.tile_fetches(day) >= config.TIANDITU_DAILY_LIMIT:
        return _fail(cached, 503, "今天的地图额度用完了，明天再看")
    # 每人每天的上限：地图对所有管理者共用一把 tk，限住单个人，一个人刷屏
    # 不会把别人的额度一起耗光。先占后判：先自增再比，并发请求不会双双漏过
    user_key = f"tile-user:{user_id}:{day}"
    locks.note_rate(user_key, 86400)
    if locks.rate_count(user_key) > config.TILE_USER_DAILY:
        return _fail(cached, 429, "今天看地图的次数太多了，明天再看")
    n = locks.count_tile_fetch(day)
    if n > config.TIANDITU_DAILY_LIMIT:
        # 几个请求同时越过了上面那道检查：多出来的这个不打上游
        return _fail(cached, 503, "今天的地图额度用完了，明天再看")
    if n == config.TIANDITU_WARN_AT or (n > config.TIANDITU_WARN_AT and n % 100 == 0):
        logger.warning("天地图瓦片今天已取 %d 次，上限 %d", n, config.TIANDITU_DAILY_LIMIT)

    tk, headers = key
    params = {"SERVICE": "WMTS", "REQUEST": "GetTile", "VERSION": "1.0.0",
              "LAYER": layer, "STYLE": "default", "TILEMATRIXSET": "w",
              "FORMAT": "tiles", "TILEMATRIX": zi, "TILEROW": yi, "TILECOL": xi,
              "tk": tk}
    # 八个子域轮着用，分散到天地图那边的不同入口
    url = UPSTREAM.format(n=(xi + yi) % 8, layer=layer)
    try:
        resp = _http.get(url, params=params, headers=headers)
    except httpx.TimeoutException:
        logger.warning("天地图瓦片超时：%s/%d/%d/%d", layer, zi, xi, yi)
        return _fail(cached, 504, "地图服务响应超时")
    except httpx.HTTPError as exc:
        # 只记异常类型：异常信息里可能带着含 tk 的网址
        logger.warning("天地图瓦片请求失败（%s）：%s/%d/%d/%d",
                       type(exc).__name__, layer, zi, xi, yi)
        return _fail(cached, 502, "地图服务暂时不可用")
    data = resp.content
    if resp.status_code != 200 or _image_type(data) is None:
        logger.warning("天地图瓦片返回异常（HTTP %d，%s）：%s/%d/%d/%d",
                       resp.status_code, resp.headers.get("content-type", ""),
                       layer, zi, xi, yi)
        return _fail(cached, 502, "地图服务暂时不可用")
    _write(path, data)
    return _tile_response(data)
