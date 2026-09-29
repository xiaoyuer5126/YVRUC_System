"""天地图瓦片转发（backend/routers/geo.py）。

**绝不访问外网**：上游一律换成 httpx.MockTransport，由用例决定它回什么、
并记下服务器实际发出去的请求（带的哪把 tk、Referer 是什么）。
"""
from __future__ import annotations

import os
import time

import httpx
import pytest

from backend import config, locks
from backend.models import now
from backend.routers import geo
from conftest import login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
TILE = "/api/geo/tiles/vec/15/26978/12406"


@pytest.fixture
def upstream(monkeypatch, tmp_path):
    """假的天地图。calls 记下每次真正打到上游的请求；handler 可按用例换。"""
    state = {"calls": [], "reply": lambda req: httpx.Response(
        200, content=PNG, headers={"content-type": "image/png"})}

    def handler(req: httpx.Request) -> httpx.Response:
        state["calls"].append(req)
        return state["reply"](req)

    monkeypatch.setattr(geo, "_http", httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(config, "TILE_CACHE_DIR", tmp_path / "tiles")
    monkeypatch.setattr(config, "TIANDITU_TK_SERVER", "server-tk")
    monkeypatch.setattr(config, "TIANDITU_TK", "browser-tk")
    locks._client.delete(f"rucva:tiles:{now().date().isoformat()}")
    yield state
    locks._client.delete(f"rucva:tiles:{now().date().isoformat()}")


def _token(client, headers) -> str:
    d = client.get("/api/geo/tile-token", headers=headers).json()
    assert d["configured"] is True
    return d["token"]


def _today() -> int:
    return locks.tile_fetches(now().date().isoformat())


# ======================= 令牌 =======================

def test_瓦片令牌只给管理者(client, admin, take_student, upstream):
    assert client.get("/api/geo/tile-token").status_code == 401
    assert client.get("/api/geo/tile-token",
                      headers=login(client, take_student())).status_code == 403
    d = client.get("/api/geo/tile-token", headers=admin).json()
    assert d["tile_base"] == "/api/geo/tiles" and d["token"]
    assert d["expires_at"].endswith("+00:00")
    # 按整点对齐：有效期在 6～7 小时之间
    assert 6 * 3600 <= d["expires_in"] <= 7 * 3600


def test_同一小时领到的令牌一样(client, admin, upstream):
    """地址不变，浏览器缓存才能在几次打开之间共用。"""
    assert _token(client, admin) == _token(client, admin)


def test_没令牌或令牌不对一律401(client, admin, upstream):
    token = _token(client, admin)
    exp, _, sig = token.partition(".")
    for t in ("", "garbage", f"{exp}.{'0' * 32}", f"{int(exp) + 3600}.{sig}"):
        r = client.get(TILE, params={"t": t})
        assert r.status_code == 401, t
    assert upstream["calls"] == []


def test_过期的令牌401(client, upstream):
    token, exp = geo.issue_token(time.time() - 8 * 3600)
    assert exp < time.time()
    assert client.get(TILE, params={"t": token}).status_code == 401


def test_缓存命中也要验令牌(client, admin, upstream):
    """不然缓存过的瓦片就成了谁都能拉的免费图源。"""
    t = _token(client, admin)
    assert client.get(TILE, params={"t": t}).status_code == 200
    assert client.get(TILE).status_code == 401
    assert client.get(TILE, params={"t": "x.y"}).status_code == 401


# ======================= 参数 =======================

@pytest.mark.parametrize("path", [
    "/api/geo/tiles/img/15/1/1",           # 只放 vec / cva
    "/api/geo/tiles/vec/0/0/0",            # 天地图 _w 从 1 级开始
    "/api/geo/tiles/vec/19/0/0",
    "/api/geo/tiles/vec/2/4/0",            # 2 级只有 0～3
    "/api/geo/tiles/vec/2/0/4",
    "/api/geo/tiles/vec/a/0/0",
    "/api/geo/tiles/vec/3/-1/0",
    "/api/geo/tiles/cva/15/26978/12406.png",
])
def test_非法图层或坐标400(client, admin, upstream, path):
    t = _token(client, admin)
    assert client.get(path, params={"t": t}).status_code == 400
    assert upstream["calls"] == []


# ======================= 取图 =======================

def test_正常返回图片且头对(client, admin, upstream):
    t = _token(client, admin)
    r = client.get(TILE, params={"t": t})
    assert r.status_code == 200
    assert r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"].startswith("public, max-age=")
    assert int(r.headers["cache-control"].split("=")[1]) >= 30 * 86400

    upstream["reply"] = lambda req: httpx.Response(200, content=JPEG)
    r = client.get("/api/geo/tiles/cva/15/26978/12407", params={"t": t})
    assert r.headers["content-type"] == "image/jpeg"


def test_上游请求拼得对(client, admin, upstream):
    client.get(TILE, params={"t": _token(client, admin)})
    (req,) = upstream["calls"]
    # 子域按 x+y 取模分散
    assert req.url.host == f"t{(26978 + 12406) % 8}.tianditu.gov.cn"
    assert req.url.path == "/vec_w/wmts"
    q = req.url.params
    assert (q["LAYER"], q["TILEMATRIXSET"], q["TILEMATRIX"], q["TILEROW"],
            q["TILECOL"]) == ("vec", "w", "15", "12406", "26978")


def test_优先用服务端tk且不带Referer(client, admin, upstream):
    client.get(TILE, params={"t": _token(client, admin)})
    (req,) = upstream["calls"]
    assert req.url.params["tk"] == "server-tk"
    assert "referer" not in req.headers


def test_没有服务端tk就不画地图_浏览器端那把不能兜底(client, admin, upstream, monkeypatch):
    """浏览器端 tk 只认真浏览器（403 / 301012，带什么 Referer 都一样），
    由服务器发出去必然被拒。所以它不能当兜底：没配服务端 tk 就如实说没配，
    前端退化成只画点和圈——留着兜底只会把「没配」伪装成 502。"""
    t = _token(client, admin)          # 先领令牌（要判「没配置」得先过验签那一关）
    monkeypatch.setattr(config, "TIANDITU_TK_SERVER", "")
    assert client.get("/api/geo/tile-token", headers=admin).json() == {"configured": False}
    assert client.get(TILE, params={"t": t}).status_code == 404
    assert upstream["calls"] == []


def test_上游请求不冒充浏览器(client, admin, upstream):
    """发出去的上游请求不许带浏览器样的 User-Agent。

    天地图靠 UA 判断来的是浏览器还是服务器，再要求「浏览器用浏览器端 tk、
    服务器用服务端 tk」。带着服务端 tk 却用 Mozilla 开头的 UA，会被判成
    「请使用服务器端访问」（403 / 301013）——2026-09-30 生产上就是这么
    502 的（地图整块不出来），所以钉住这条。
    """
    client.get(TILE, params={"t": _token(client, admin)})
    (req,) = upstream["calls"]
    assert "mozilla" not in req.headers.get("user-agent", "").lower()


def test_两把tk都没配就不画地图(client, admin, upstream, monkeypatch):
    t = _token(client, admin)
    monkeypatch.setattr(config, "TIANDITU_TK_SERVER", "")
    monkeypatch.setattr(config, "TIANDITU_TK", "")
    assert client.get("/api/geo/tile-token", headers=admin).json() == {"configured": False}
    assert client.get("/api/staff/map-config", headers=admin).json()["tiles"] is False
    assert client.get(TILE, params={"t": t}).status_code == 404
    assert upstream["calls"] == []


# ======================= 上游出错 =======================

def test_上游超时504(client, admin, upstream):
    def boom(req):
        raise httpx.ReadTimeout("slow", request=req)
    upstream["reply"] = boom
    r = client.get(TILE, params={"t": _token(client, admin)})
    assert r.status_code == 504


def test_上游连不上502(client, admin, upstream):
    def boom(req):
        raise httpx.ConnectError("down", request=req)
    upstream["reply"] = boom
    assert client.get(TILE, params={"t": _token(client, admin)}).status_code == 502


@pytest.mark.parametrize("resp", [
    httpx.Response(403, content=b"<html>forbidden</html>"),
    # 天地图出错时常回 200 + 一页 XML：不能当图片存下来
    httpx.Response(200, content=b"<?xml version='1.0'?><error>key</error>",
                   headers={"content-type": "text/xml"}),
])
def test_上游错误页不透传也不缓存(client, admin, upstream, resp):
    upstream["reply"] = lambda req: resp
    t = _token(client, admin)
    r = client.get(TILE, params={"t": t})
    assert r.status_code == 502
    assert b"forbidden" not in r.content and b"<error>" not in r.content
    # 没缓存：恢复之后再来，照样去取
    upstream["reply"] = lambda req: httpx.Response(200, content=PNG)
    assert client.get(TILE, params={"t": t}).status_code == 200
    assert len(upstream["calls"]) == 2


def test_缓存过期又取不到新图时给旧图(client, admin, upstream):
    t = _token(client, admin)
    client.get(TILE, params={"t": t})
    path = config.TILE_CACHE_DIR / "vec" / "15" / "26978" / "12406"
    old = time.time() - 31 * 86400
    os.utime(path, (old, old))

    def boom(req):
        raise httpx.ConnectError("down", request=req)
    upstream["reply"] = boom
    r = client.get(TILE, params={"t": t})
    assert r.status_code == 200 and r.content == PNG
    assert len(upstream["calls"]) == 2       # 过期了确实去取过


# ======================= 缓存与额度 =======================

def test_同一张瓦片只向上游取一次(client, admin, upstream):
    t = _token(client, admin)
    for _ in range(3):
        assert client.get(TILE, params={"t": t}).status_code == 200
    assert len(upstream["calls"]) == 1
    assert _today() == 1                      # 缓存命中不计数


def test_计数只算上游请求(client, admin, upstream):
    t = _token(client, admin)
    client.get(TILE, params={"t": t})
    client.get("/api/geo/tiles/cva/15/26978/12406", params={"t": t})
    client.get(TILE, params={"t": t})                     # 命中
    client.get(TILE, params={"t": "bad"})                 # 401，不打上游
    client.get("/api/geo/tiles/vec/0/0/0", params={"t": t})   # 400
    assert len(upstream["calls"]) == 2
    assert _today() == 2
    ttl = locks._client.ttl(f"rucva:tiles:{now().date().isoformat()}")
    assert 0 < ttl <= 2 * 86400


def test_超额直接503不打上游(client, admin, upstream, monkeypatch):
    monkeypatch.setattr(config, "TIANDITU_DAILY_LIMIT", 2)
    monkeypatch.setattr(config, "TIANDITU_WARN_AT", 1)
    t = _token(client, admin)
    assert client.get("/api/geo/tiles/vec/15/1/1", params={"t": t}).status_code == 200
    assert client.get("/api/geo/tiles/vec/15/1/2", params={"t": t}).status_code == 200
    r = client.get("/api/geo/tiles/vec/15/1/3", params={"t": t})
    assert r.status_code == 503
    assert len(upstream["calls"]) == 2
    assert _today() == 2
    # 已经缓存的照样能看
    assert client.get("/api/geo/tiles/vec/15/1/1", params={"t": t}).status_code == 200


def test_接近额度记警告(client, admin, upstream, monkeypatch, caplog):
    monkeypatch.setattr(config, "TIANDITU_WARN_AT", 2)
    t = _token(client, admin)
    with caplog.at_level("WARNING", logger="backend.routers.geo"):
        client.get("/api/geo/tiles/vec/15/1/1", params={"t": t})
        assert "已取" not in caplog.text
        client.get("/api/geo/tiles/vec/15/1/2", params={"t": t})
    assert "天地图瓦片今天已取 2 次" in caplog.text


# ======================= 前端拿不到 tk =======================

def test_前端拿不到任何tk(client, admin, upstream):
    for url in ("/api/staff/map-config", "/api/geo/tile-token"):
        body = client.get(url, headers=admin).text
        assert "server-tk" not in body and "browser-tk" not in body, url
    d = client.get("/api/staff/map-config", headers=admin).json()
    assert "tk" not in d and d["tiles"] is True
