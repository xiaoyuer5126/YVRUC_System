"""初始管理员：YVRUC_BOOTSTRAP_ADMINS 里的学号经统一身份认证登录时补上管理员。

回调接口照 test_sso_oauth.py 的做法把 sso 换成假的：拿到身份之后的事才是
这里要验的。学号是凭空编的测试号，用完即删。
"""
from __future__ import annotations

import pytest

from backend import config
from backend.database import SessionLocal
from backend.integrations import sso
from backend.models import AuditLog, Notification, Session, User, uid

from conftest import login

LISTED_SID = "2099300001"
OTHER_SID = "2099300002"


def _drop_users(*sids: str) -> None:
    s = SessionLocal()
    try:
        ids = [u.id for u in s.query(User)
               .filter(User.student_id.in_(sids)).all()]
        if ids:
            for table in (Session, Notification):
                s.query(table).filter(table.user_id.in_(ids)).delete(
                    synchronize_session=False)
            s.query(AuditLog).filter(AuditLog.target_id.in_(ids)).delete(
                synchronize_session=False)
            s.query(User).filter(User.id.in_(ids)).delete(
                synchronize_session=False)
        s.commit()
    finally:
        s.close()


@pytest.fixture(autouse=True)
def _cleanup():
    _drop_users(LISTED_SID, OTHER_SID)
    yield
    _drop_users(LISTED_SID, OTHER_SID)


@pytest.fixture
def sso_login(client, monkeypatch):
    """走一遍统一身份认证回调，返回 /api/auth/me 看到的用户。"""
    monkeypatch.setattr(sso, "mode", lambda: "oauth2")

    def _login(sid: str) -> dict:
        monkeypatch.setattr(sso, "handle_callback",
                            lambda code, state, nonce: {"student_id": sid,
                                                        "name": "测试同学",
                                                        "user_type": "student"})
        r = client.post("/api/auth/sso-callback",
                        json={"code": "c", "state": "s"})
        assert r.status_code == 200, r.text
        me = client.get("/api/auth/me", headers={
            "Authorization": f"Bearer {r.json()['token']}"})
        assert me.status_code == 200, me.text
        return me.json()["user"]
    return _login


def _grant_logs(sid: str) -> list[AuditLog]:
    s = SessionLocal()
    try:
        u = s.query(User).filter(User.student_id == sid).one()
        return s.query(AuditLog).filter(AuditLog.target_id == u.id).all()
    finally:
        s.close()


def test_列表里的学号首次登录就是管理员(sso_login, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", frozenset({LISTED_SID}))
    user = sso_login(LISTED_SID)
    assert set(user["roles"]) == {"student", "admin"}
    logs = _grant_logs(LISTED_SID)
    assert len(logs) == 1
    assert "按配置授予管理员身份" in logs[0].summary


def test_已有账号再次登录补上管理员且保留原身份(sso_login, monkeypatch):
    s = SessionLocal()
    try:
        s.add(User(id=uid("u"), student_id=LISTED_SID, name="测试同学",
                   user_type="student", roles=["student", "initiator"]))
        s.commit()
    finally:
        s.close()
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS",
                        frozenset({"2099399999", LISTED_SID}))
    user = sso_login(LISTED_SID)
    assert set(user["roles"]) == {"student", "initiator", "admin"}
    # 已经是管理员就不再动，也不再留痕
    assert set(sso_login(LISTED_SID)["roles"]) == {"student", "initiator", "admin"}
    assert len(_grant_logs(LISTED_SID)) == 1


def test_列表外的学号不受影响(sso_login, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", frozenset({LISTED_SID}))
    assert sso_login(OTHER_SID)["roles"] == ["student"]
    assert _grant_logs(OTHER_SID) == []


def test_留空不启用(sso_login, monkeypatch):
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", frozenset())
    assert sso_login(LISTED_SID)["roles"] == ["student"]


def test_演示登录不补管理员(client, take_student, monkeypatch):
    sid = take_student()
    monkeypatch.setattr(config, "BOOTSTRAP_ADMINS", frozenset({sid}))
    headers = login(client, sid)
    me = client.get("/api/auth/me", headers=headers)
    assert me.json()["user"]["roles"] == ["student"]


def test_学号列表按逗号拆开并去掉空白():
    assert config.id_list(" 2099300001 ,,2099300002, ") == frozenset(
        {"2099300001", "2099300002"})
    assert config.id_list("") == frozenset()
