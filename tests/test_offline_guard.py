"""测试环境碰不到学校接口：conftest 清空了对接参数，也堵住了 urllib 出网。

这几条一旦失守，全量 pytest 就可能拿开发机 .env 里的真实参数往学校网关、
统一身份认证上打请求。
"""
from __future__ import annotations

import urllib.request

import pytest

from backend import config, sudy_sign
from backend.integrations import is_mock, msgcenter, sso


def test_学校对接参数全是空的():
    assert config.OPENPLATFORM_APPID == ""
    assert config.OPENPLATFORM_SECRET == ""
    assert config.OPENPLATFORM_GATEWAY == ""
    assert config.CAS_BASE_URL == ""
    assert config.SSO_REDIRECT_URI == ""


def test_统一身份认证走演示模式():
    assert sso.mode() == "mock"
    assert not sso.configured()


def test_消息中心走演示实现():
    assert is_mock("msgcenter")
    assert not msgcenter.real_mode()
    assert not sudy_sign.configured()


def test_urllib_出不了网():
    with pytest.raises(RuntimeError, match="不许"):
        urllib.request.urlopen("https://cas.ruc.edu.cn/cas/oauth2.0/authorize")
