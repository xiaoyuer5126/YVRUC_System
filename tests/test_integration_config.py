"""学校对接配置：开关和参数都在 .env，启动时核对。

开关开了参数却没填齐、通道里有短信、开关写成 1 以外的值，后端都拒绝启动；
开关没开就走演示 Mock，不碰学校接口。
"""
from __future__ import annotations

import urllib.parse

import pytest
from fastapi.testclient import TestClient

from backend import config
from backend.integrations import check, msgcenter
from backend.main import app

SSO_FULL = {
    "SSO_ENABLED": True,
    "OPENPLATFORM_APPID": "app-1",
    "OPENPLATFORM_SECRET": "secret-1",
    "CAS_BASE_URL": "https://cas.example.edu/cas",
    "SSO_REDIRECT_URI": "https://v.example.edu/",
}


def _set(monkeypatch, **values) -> None:
    for name, value in values.items():
        monkeypatch.setattr(config, name, value)


def test_没开开关时走演示模式(client):
    check()
    assert client.get("/api/auth/sso-mode").json() == {"mode": "demo"}
    assert client.get("/api/auth/sso-login-url").status_code == 409
    assert not msgcenter.real_mode()


def test_统一认证开了缺参数_拒绝启动并写明缺哪个(monkeypatch):
    _set(monkeypatch, SSO_ENABLED=True)
    with pytest.raises(RuntimeError) as exc, TestClient(app):
        pass
    msg = str(exc.value)
    assert "YVRUC_SSO_ENABLED=1" in msg
    for name in ("YVRUC_SSO_CLIENT_ID", "YVRUC_SSO_CLIENT_SECRET",
                 "YVRUC_SSO_REDIRECT_URI", "YVRUC_CAS_BASE_URL"):
        assert name in msg


def test_消息中心开了缺参数_拒绝启动(monkeypatch):
    _set(monkeypatch, MSGCENTER_ENABLED=True, OPENPLATFORM_APPID="app-1")
    with pytest.raises(RuntimeError) as exc:
        check()
    msg = str(exc.value)
    assert "YVRUC_MSGCENTER_ENABLED=1" in msg
    assert "YVRUC_OPENPLATFORM_SECRET" in msg
    assert "YVRUC_OPENPLATFORM_GATEWAY" in msg
    assert "YVRUC_OPENPLATFORM_APPID" not in msg


@pytest.mark.parametrize("channels", ["1", "1,4", "4, 1"])
def test_通道含短信_拒绝启动(monkeypatch, channels):
    """学校没同意本平台发短信，短信还是按条计费的。开关没开也不让起来。"""
    _set(monkeypatch, MSGCENTER_CHANNELS=channels)
    with pytest.raises(RuntimeError, match="短信"):
        check()


def test_通道写错_拒绝启动(monkeypatch):
    _set(monkeypatch, MSGCENTER_CHANNELS="4,8")
    with pytest.raises(RuntimeError, match="未知通道 8"):
        check()


def test_邮件和企业微信照常放行(monkeypatch):
    _set(monkeypatch, MSGCENTER_CHANNELS="2,4")
    check()


@pytest.mark.parametrize("raw", ["true", "yes", "on", "2"])
def test_开关只认1(monkeypatch, raw):
    """写成 true 不算打开：要是悄悄当成没开，部署的人会以为已经接通了。"""
    monkeypatch.setenv("YVRUC_SSO_ENABLED", raw)
    with pytest.raises(RuntimeError, match="YVRUC_SSO_ENABLED"):
        check()


def test_统一认证参数齐全_走真实登录(client, monkeypatch):
    """client_id/secret 没单独配时用开放平台的 appid/secret，端点按 CAS 根地址拼。"""
    _set(monkeypatch, **SSO_FULL)
    check()
    assert client.get("/api/auth/sso-mode").json() == {"mode": "oauth2"}
    url = client.get("/api/auth/sso-login-url").json()["login_url"]
    base, query = url.split("?", 1)
    q = dict(urllib.parse.parse_qsl(query))
    assert base == "https://cas.example.edu/cas/oauth2.0/authorize"
    assert q["client_id"] == "app-1"
    assert q["redirect_uri"] == "https://v.example.edu/"


def test_单独配的client_id和端点优先(client, monkeypatch):
    _set(monkeypatch, **SSO_FULL, SSO_CLIENT_ID="oauth-9",
         SSO_AUTHORIZE_URL="https://sso.example.edu/authorize")
    check()
    url = client.get("/api/auth/sso-login-url").json()["login_url"]
    assert url.startswith("https://sso.example.edu/authorize?")
    assert dict(urllib.parse.parse_qsl(url.split("?", 1)[1]))["client_id"] == "oauth-9"


def test_三个端点都填了可以不填CAS根地址(monkeypatch):
    _set(monkeypatch, **{**SSO_FULL, "CAS_BASE_URL": ""},
         SSO_AUTHORIZE_URL="https://sso.example.edu/a",
         SSO_TOKEN_URL="https://sso.example.edu/t",
         SSO_USER_INFO_URL="https://sso.example.edu/u")
    check()


def test_集成设置接口已经没有了(client, admin):
    assert client.get("/api/staff/integrations", headers=admin).status_code == 404
