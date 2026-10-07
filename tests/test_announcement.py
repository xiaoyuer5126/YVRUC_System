"""平台公告：管理员编辑与开关，登录用户读到当前生效的那一条。"""
from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

import pytest

from backend import oplog
from backend.models import Announcement, now
from conftest import ADMIN, INITIATOR, QINGXIE, login


@pytest.fixture(autouse=True)
def no_announcement(db):
    """每个用例从「没有公告」开始。"""
    db.query(Announcement).delete()
    db.commit()
    yield
    db.query(Announcement).delete()
    db.commit()


def _put(client, headers, **body):
    return client.put("/api/staff/announcement", headers=headers,
                      json={"title": "", "body": "", "enabled": False, **body})


def test_没有公告时回空(client, take_student):
    me = login(client, take_student())
    assert client.get("/api/announcement", headers=me).json() == {"announcement": None}
    d = client.get("/api/staff/announcement", headers=login(client, ADMIN)).json()
    assert d["enabled"] is False and d["title"] == ""


def test_启用后所有人读得到_关闭后读不到(client, take_student):
    admin = login(client, ADMIN)
    r = _put(client, admin, title="试运行公告",
             body="欢迎试用。\n反馈入口：https://example.com/f", enabled=True)
    assert r.status_code == 200, r.text
    assert r.json()["updated_by_name"] and r.json()["updated_at"]

    me = login(client, take_student())
    got = client.get("/api/announcement", headers=me).json()["announcement"]
    assert got == {"title": "试运行公告",
                   "body": "欢迎试用。\n反馈入口：https://example.com/f"}

    assert _put(client, admin, title="试运行公告", body="x").status_code == 200
    assert client.get("/api/announcement", headers=me).json()["announcement"] is None


def test_启用时标题正文必填(client):
    admin = login(client, ADMIN)
    assert _put(client, admin, title="只有标题", enabled=True).status_code == 400
    assert _put(client, admin, body="只有正文", enabled=True).status_code == 400
    assert _put(client, admin, title="  ", body="  ", enabled=True).status_code == 400
    # 关着的时候可以先存草稿
    assert _put(client, admin, title="草稿").status_code == 200


def test_只有管理员能改(client):
    for sid in (INITIATOR, QINGXIE):      # 张远航是会长团，也不行
        h = login(client, sid)
        assert client.get("/api/staff/announcement", headers=h).status_code == 403
        assert _put(client, h, title="t", body="b", enabled=True).status_code == 403


def test_修改公告进操作日志(client):
    since = (now() - timedelta(seconds=1)).isoformat(timespec="seconds")
    admin = login(client, ADMIN)
    _put(client, admin, title="t", body="b", enabled=True)
    oplog.flush()
    q = urlencode({"start": since, "person": ADMIN, "kind": "update"})
    rows = client.get(f"/api/staff/oplogs?{q}", headers=admin).json()["items"]
    assert any(r["path"] == "/api/staff/announcement" for r in rows)
