"""登录、会话与令牌传递方式。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Session, now
from conftest import ADMIN, DEMO_PASSWORD, login


def test_login_requires_password(client):
    """没有口令拿不到令牌——这是公网上唯一的入口。"""
    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN,
                                                  "password": ""})
    assert r.status_code == 401

    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN,
                                                  "password": "wrong"})
    assert r.status_code == 401

    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN})
    assert r.status_code == 422      # 少字段直接被模型拦下


def test_login_succeeds_with_password(client):
    r = client.post("/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200
    assert "admin" in r.json()["user"]["roles"]


def test_expired_session_rejected(client, db):
    headers = login(client, ADMIN)
    token = headers["Authorization"].removeprefix("Bearer ")

    row = db.get(Session, token)
    assert row.expires_at > now()                     # 默认还没过期
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()

    assert client.get("/api/auth/me", headers=headers).status_code == 401
    db.expunge_all()                                  # 绕开身份映射缓存
    assert db.get(Session, token) is None             # 过期会话顺手清掉


def test_query_token_only_works_for_download(client, admin):
    """令牌走 URL 会漏进日志与 Referer，只有直链下载那一个路由认。"""
    token = admin["Authorization"].removeprefix("Bearer ")

    assert client.get(f"/api/auth/me?token={token}").status_code == 401

    acts = client.get("/api/staff/activities", headers=admin).json()
    r = client.get(f"/api/staff/activities/{acts[0]['id']}/export.xlsx"
                   f"?token={token}")
    assert r.status_code == 200


def test_logout_only_kills_current_session(client):
    """在手机上退出，不该把电脑上的登录一起顶掉。"""
    phone = login(client, ADMIN)
    laptop = login(client, ADMIN)

    assert client.post("/api/auth/logout", headers=phone).status_code == 200
    assert client.get("/api/auth/me", headers=phone).status_code == 401
    assert client.get("/api/auth/me", headers=laptop).status_code == 200
