"""操作日志：记什么、不记什么、谁能看、筛选、导出、清理、磁盘不足停写。"""
from __future__ import annotations

import json
from datetime import date, timedelta
from urllib.parse import urlencode

import pytest

from backend import config, locks, oplog, scheduled
from backend.models import now
from conftest import ADMIN, INITIATOR, QINGXIE, dl_url, inbox, login


def _since() -> str:
    """本用例开始的时刻：只看这之后的记录，别的用例留下的不掺进来。"""
    return (now() - timedelta(seconds=1)).isoformat(timespec="seconds")


def _logs(client, headers, since: str, **filters) -> list[dict]:
    oplog.flush()
    query = urlencode({"start": since, "size": 100, **filters})
    r = client.get(f"/api/staff/oplogs?{query}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def test_写请求与登录都记下且带操作人(client, take_student):
    since = _since()
    sid = take_student()
    me = {**login(client, sid), "X-Identity": "student"}
    client.post("/api/feedback", headers=me,
                data={"content": "身份证号 110101200501010011 不该进日志"})
    client.get("/api/announcement", headers=me)       # 浏览不记

    admin = login(client, ADMIN)
    rows = _logs(client, admin, since, person=sid)
    kinds = {(r["kind"], r["path"]) for r in rows}
    assert ("login", "/api/auth/demo-login") in kinds
    assert ("create", "/api/feedback") in kinds
    assert not any(r["path"] == "/api/announcement" for r in rows)
    fb = next(r for r in rows if r["path"] == "/api/feedback")
    assert fb["sid"] == sid and fb["name"].startswith("测试用户")
    assert fb["identity"] == "student" and fb["status"] == 200
    assert fb["method"] == "POST" and fb["kind_label"] == "提交"
    login_row = next(r for r in rows if r["kind"] == "login")
    assert login_row["label"] == "登录（演示口令）" and login_row["ip"]


def test_请求正文不进日志文件(client, take_student):
    me = login(client, take_student())
    client.post("/api/feedback", headers=me,
                data={"content": "正文标记 QX-OPLOG-SECRET"})
    oplog.flush()
    for path in config.OPLOG_DIR.glob("*.jsonl"):
        assert "QX-OPLOG-SECRET" not in path.read_text(encoding="utf-8")


def test_登录失败也记下填的学号(client):
    since = _since()
    r = client.post("/api/auth/demo-login",
                    json={"student_id": "8800001", "password": "wrong"})
    assert r.status_code == 401
    rows = _logs(client, login(client, ADMIN), since, person="8800001",
                 kind="login")
    assert len(rows) == 1
    assert rows[0]["status"] == 401 and rows[0]["name"] == ""


def test_导出下载记录且换票不记(client):
    since = _since()
    admin = login(client, ADMIN)
    url = dl_url(client, admin, "/api/staff/oplogs.xlsx")
    r = client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml")
    rows = _logs(client, admin, since, person=ADMIN)
    downloads = [x for x in rows if x["kind"] == "download"]
    assert [x["path"] for x in downloads] == ["/api/staff/oplogs.xlsx"]
    # 地址里的票据不进日志：只记路径
    assert "ticket" not in json.dumps(downloads)
    assert not any(x["path"] == "/api/auth/download-ticket" for x in rows)


def test_按活动和类型筛选(client, open_group):
    since = _since()
    act, _ = open_group(1)
    other, _ = open_group(1)
    admin = login(client, ADMIN)
    client.post(f"/api/staff/activities/{act.id}/publish", headers=admin)
    client.post(f"/api/staff/activities/{other.id}/publish", headers=admin)

    rows = _logs(client, admin, since, activity=act.title)
    assert rows and all(r["activity_id"] == act.id for r in rows)
    assert rows[0]["activity_title"] == act.title
    assert _logs(client, admin, since, activity=act.title, kind="delete") == []
    assert _logs(client, admin, since, activity="不存在的活动名") == []


def test_只有管理员能看(client):
    for sid in (INITIATOR, QINGXIE):      # 张远航是会长团，也不行
        h = login(client, sid)
        assert client.get("/api/staff/oplogs", headers=h).status_code == 403
        r = client.get(dl_url(client, h, "/api/staff/oplogs.xlsx"))
        assert r.status_code == 403


def test_时间段与类型校验(client):
    admin = login(client, ADMIN)
    too_long = (now() - timedelta(days=config.OPLOG_QUERY_MAX_DAYS + 1)
                ).isoformat(timespec="seconds")
    assert client.get(f"/api/staff/oplogs?start={too_long}",
                      headers=admin).status_code == 400
    assert client.get("/api/staff/oplogs?kind=nope",
                      headers=admin).status_code == 400
    late = now().isoformat(timespec="seconds")
    early = (now() - timedelta(days=1)).isoformat(timespec="seconds")
    assert client.get(f"/api/staff/oplogs?start={late}&end={early}",
                      headers=admin).status_code == 400


def test_超过保留期的文件被定时任务删掉(db):
    config.OPLOG_DIR.mkdir(parents=True, exist_ok=True)
    today = date.today()
    old = today - timedelta(days=config.OPLOG_RETAIN_DAYS + 1)
    keep = today - timedelta(days=config.OPLOG_RETAIN_DAYS)
    old_file = config.OPLOG_DIR / f"{old.isoformat()}-1.jsonl"
    keep_file = config.OPLOG_DIR / f"{keep.isoformat()}-1.jsonl"
    old_file.write_text("{}\n", encoding="utf-8")
    keep_file.write_text("{}\n", encoding="utf-8")
    try:
        assert scheduled.purge_oplogs(db) >= 1
        assert not old_file.exists() and keep_file.exists()
    finally:
        keep_file.unlink(missing_ok=True)


@pytest.fixture
def disk_full(monkeypatch):
    """假装磁盘只剩不到下限的空间。"""
    locks._client.delete(f"yvruc:once:oplog:disk-alert:{date.today().isoformat()}")
    monkeypatch.setattr(config, "OPLOG_MIN_FREE_BYTES", 1 << 62)
    monkeypatch.setattr(oplog, "_disk_checked_at", 0.0)
    yield
    monkeypatch.undo()
    oplog._disk_checked_at = 0.0
    oplog._disk_ok = True


def test_磁盘不足时停写并通知管理员一次(client, take_student, db, disk_full):
    since = _since()
    sid = take_student()
    me = login(client, sid)
    client.post("/api/feedback", headers=me, data={"content": "磁盘满时的请求"})
    oplog.flush()
    oplog._disk_checked_at = 0.0          # 再查一次磁盘，验证告警不重复
    client.post("/api/auth/logout", headers=me)
    oplog.flush()
    assert len(inbox(db, ADMIN, "服务器磁盘空间不足")) == 1

    oplog._disk_checked_at = 0.0
    oplog._disk_ok = True
    config.OPLOG_MIN_FREE_BYTES = 1
    rows = _logs(client, login(client, ADMIN), since, person=sid)
    assert rows == []
