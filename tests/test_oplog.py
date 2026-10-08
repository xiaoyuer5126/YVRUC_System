"""操作日志：记什么、不记什么、谁能看、筛选、分页、导出、清理、磁盘不足停写。"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from io import BytesIO
from urllib.parse import urlencode

import pytest
from openpyxl import load_workbook

from backend import config, locks, oplog, scheduled
from backend.models import now
from conftest import ADMIN, INITIATOR, QINGXIE, dl_url, inbox, login


def _since() -> str:
    """本用例开始的时刻，精确到毫秒：只看这之后的记录。

    不能减一秒再按秒截断——用例跑得快，一秒里能过好几个，上一个用例留下的记录
    会被算进来（管理员是所有用例共用的操作人，按人筛不掉）。
    """
    return now().isoformat(timespec="milliseconds")


def _page(client, headers, since: str, *, page: int = 1, size: int = 20,
          **filters) -> dict:
    """取一页操作日志，连 total 一起回。"""
    oplog.flush()
    query = urlencode({"start": since, "page": page, "size": size, **filters})
    r = client.get(f"/api/staff/oplogs?{query}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _logs(client, headers, since: str, **filters) -> list[dict]:
    return _page(client, headers, since, size=100, **filters)["items"]


def _three_edits(client, admin: dict, acts: list) -> list:
    """让三个活动依次进编辑态：三条带活动编号、分得清谁先谁后的管理端记录。

    活动先建好再调这个函数：建活动、建场次岗位也各记一条 create，落在同一个
    时间窗口里就把「三条」搅成十几条。
    """
    for act in acts:
        r = client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
        assert r.status_code == 200, r.text
    return acts


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


def test_调剂意愿记下改成了什么(client, open_group, take_student):
    since = _since()
    act, g = open_group(1)
    sid = take_student()
    me = login(client, sid)
    eid = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                      json={"position_id": g.test_position_id},
                      headers=me).json()["enrollment_id"]
    client.patch(f"/api/activities/enrollments/{eid}/reassign-pref",
                 json={"allow_reassign": False}, headers=me)

    rows = _logs(client, login(client, ADMIN), since, person=sid)
    row = next(r for r in rows if r["path"].endswith("/reassign-pref"))
    assert row["note"] == "不接受调剂" and row["status"] == 200


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


def test_分页按时间倒序且总数不受分页影响(client, open_group):
    acts = [open_group(1)[0] for _ in range(3)]
    since = _since()
    admin = login(client, ADMIN)
    _three_edits(client, admin, acts)
    only = {"person": ADMIN, "kind": "create"}

    first = _page(client, admin, since, page=1, size=2, **only)
    assert first["total"] == 3
    assert [r["activity_id"] for r in first["items"]] == [acts[2].id, acts[1].id]

    second = _page(client, admin, since, page=2, size=2, **only)
    assert second["total"] == 3
    assert [r["activity_id"] for r in second["items"]] == [acts[0].id]
    assert second["items"][0]["t"] <= first["items"][-1]["t"]   # 接在第一页后面

    empty = _page(client, admin, since, page=3, size=2, **only)
    assert empty["items"] == [] and empty["total"] == 3
    assert _page(client, admin, since, page=9, size=2, **only)["items"] == []

    # 各页按序拼起来，就是不分页时的那一份
    assert _logs(client, admin, since, **only) == first["items"] + second["items"]


def test_同一天多个进程的文件按时间归并(monkeypatch, tmp_path):
    """一天里几个进程各写一个文件，读出来照样是从早到晚，分页留下的才是最新的。"""
    monkeypatch.setattr(config, "OPLOG_DIR", tmp_path)
    day = "2020-01-02"
    for pid, secs in ((11, (0, 2, 4)), (22, (1, 3, 5))):
        (tmp_path / f"{day}-{pid}.jsonl").write_text(
            "".join(json.dumps({"t": f"{day}T09:00:0{s}.000", "sid": str(s)}) + "\n"
                    for s in secs), encoding="utf-8")
    lo, hi = datetime(2020, 1, 2), datetime(2020, 1, 3)
    assert [r["sid"] for r in oplog.iter_rows(lo, hi)] == ["0", "1", "2",
                                                           "3", "4", "5"]
    rows, total = oplog.page_rows(lo, hi, page=1, size=2)
    assert total == 6 and [r["sid"] for r in rows] == ["5", "4"]
    rows, total = oplog.page_rows(lo, hi, page=2, size=2)
    assert total == 6 and [r["sid"] for r in rows] == ["3", "2"]


def test_同一毫秒的几条翻页时不重不漏(monkeypatch, tmp_path):
    """时间只精确到毫秒，同一毫秒的几条翻页时既不能重复也不能漏掉。"""
    monkeypatch.setattr(config, "OPLOG_DIR", tmp_path)
    day = "2020-01-02"
    (tmp_path / f"{day}-1.jsonl").write_text(
        "".join(json.dumps({"t": f"{day}T09:00:00.000", "sid": f"s{i}"}) + "\n"
                for i in range(7)), encoding="utf-8")
    lo, hi = datetime(2020, 1, 2), datetime(2020, 1, 3)
    got = []
    for page in (1, 2, 3):
        rows, total = oplog.page_rows(lo, hi, page=page, size=3)
        assert total == 7
        got += [r["sid"] for r in rows]
    assert sorted(got) == [f"s{i}" for i in range(7)]


def test_导出新的在前且超上限回绝(client, open_group, monkeypatch):
    acts = [open_group(1)[0] for _ in range(3)]
    since = _since()
    admin = login(client, ADMIN)
    _three_edits(client, admin, acts)
    query = urlencode({"start": since, "person": ADMIN, "kind": "create"})

    # 上限调小到 2，三条记录就够触发，不必真造五万条
    monkeypatch.setattr(config, "OPLOG_EXPORT_MAX_ROWS", 2)
    r = client.get(dl_url(client, admin, f"/api/staff/oplogs.xlsx?{query}"))
    assert r.status_code == 400
    assert "缩短时间段" in r.json()["detail"]

    monkeypatch.setattr(config, "OPLOG_EXPORT_MAX_ROWS", 100)
    r = client.get(dl_url(client, admin, f"/api/staff/oplogs.xlsx?{query}"))
    assert r.status_code == 200
    ws = load_workbook(BytesIO(r.content)).active
    assert ws.max_row == 4                        # 表头 + 三条
    assert [ws.cell(row=i, column=11).value for i in range(2, 5)] == [
        acts[2].title, acts[1].title, acts[0].title]


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
