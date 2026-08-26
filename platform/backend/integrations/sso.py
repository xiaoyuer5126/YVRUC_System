"""统一身份认证（微人大 SSO）适配。

《设计方案》五：师生登录平台，OAuth2.0 或 CAS 协议。
  · 演示模式：/api/auth/sso-login 模拟"统一身份认证已通过"，按学号在本平台
    建立会话——学校评审时可直接切换演示账号体验完整流程；
  · 对接模式：login_url() 返回跳转地址，handle_callback() 用 code 换用户身份。
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

from . import get_config, is_mock


def mode() -> str:
    return "mock" if is_mock("sso") else get_config("sso").get("protocol", "oauth2")


def login_url(next_url: str = "") -> str:
    """真实 SSO 的授权跳转地址。"""
    cfg = get_config("sso")
    if cfg.get("protocol") == "cas":
        q = urllib.parse.urlencode({"service": cfg.get("redirect_uri", "")})
        return f"{cfg['authorize_url']}?{q}"
    q = urllib.parse.urlencode({
        "response_type": "code", "client_id": cfg.get("client_id", ""),
        "redirect_uri": cfg.get("redirect_uri", ""), "state": next_url,
    })
    return f"{cfg['authorize_url']}?{q}"


def handle_callback(code: str) -> dict:
    """用授权码换用户信息 → {"student_id": ..., "name": ...}。"""
    cfg = get_config("sso")
    data = urllib.parse.urlencode({
        "grant_type": "authorization_code", "code": code,
        "client_id": cfg.get("client_id", ""),
        "client_secret": cfg.get("client_secret", ""),
        "redirect_uri": cfg.get("redirect_uri", ""),
    }).encode()
    req = urllib.request.Request(cfg["token_url"], data=data)  # noqa: S310
    with urllib.request.urlopen(req, timeout=6) as resp:  # noqa: S310
        token = json.loads(resp.read().decode())
    req2 = urllib.request.Request(  # noqa: S310
        cfg["user_info_url"],
        headers={"Authorization": f"Bearer {token.get('access_token', '')}"})
    with urllib.request.urlopen(req2, timeout=6) as resp:  # noqa: S310
        info = json.loads(resp.read().decode())
    return {"student_id": str(info.get("student_id", "")),
            "name": str(info.get("name", ""))}
