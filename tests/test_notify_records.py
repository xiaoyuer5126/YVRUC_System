"""通知记录：谁能看、触发人是谁、筛选、分页、导出、没有请求上下文记为系统。"""
from __future__ import annotations

from io import BytesIO
from urllib.parse import urlencode

from openpyxl import load_workbook

from backend import config
from backend.integrations import msgcenter
from backend.models import now
from conftest import (ADMIN, LIAISON_MEMBER, QINGXIE, dl_url, login,
                      student_row)

HEADER = ["时间", "收件人学号", "收件人姓名", "类型", "标题", "正文", "活动",
          "推送状态", "已读", "签收", "触发人"]


def _since() -> str:
    """本用例开始的时刻：只看这之后的通知，不把别的用例留下的算进来。"""
    return now().isoformat(timespec="milliseconds")


def _page(client, headers, since: str, *, page: int = 1, size: int = 20,
          **filters) -> dict:
    query = urlencode({"start": since, "page": page, "size": size, **filters})
    r = client.get(f"/api/staff/notifications?{query}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _rows(client, headers, since: str, **filters) -> list[dict]:
    return _page(client, headers, since, size=100, **filters)["items"]


def _send(db, sid: str, template_key: str, **fields):
    """直接发一条通知（没有请求上下文，触发人应当是系统）。"""
    msgcenter.send_user(db, student_row(db, sid), template_key, **fields)
    db.commit()


def test_请求触发的通知记下发请求的人(client, db, take_student):
    """学生提反馈会通知全体管理员：这条通知的触发人应当是提反馈的人。"""
    since = _since()
    sid = take_student()
    me = login(client, sid)
    r = client.post("/api/feedback", headers=me,
                    data={"content": "触发人标记 QX-NTF-1"})
    assert r.status_code == 200, r.text

    rows = _rows(client, login(client, ADMIN), since,
                 person=ADMIN, template="custom")
    row = next(x for x in rows if "QX-NTF-1" in x["content"])
    assert row["recipient_sid"] == ADMIN
    assert row["trigger_sid"] == sid
    assert row["trigger_name"] == student_row(db, sid).name
    assert row["template_label"] == "自定义通知"
    # 演示模式没有外部推送，通道只是站内
    assert row["status"] == "site" and row["status_label"] == "仅站内"


def test_没有请求上下文的通知记为系统(client, db, take_student):
    since = _since()
    sid = take_student()
    _send(db, sid, "signup_success", activity="系统发的活动", shift="上午场",
          time="2026-10-25 08:00", detail="")

    row = next(x for x in _rows(client, login(client, ADMIN), since, person=sid)
               if x["template_key"] == "signup_success")
    assert row["trigger_sid"] == "" and row["trigger_name"] == "系统"
    assert row["status"] == "site"


def test_按收件人类型状态活动筛选(client, db, take_student, open_group):
    since = _since()
    act, _ = open_group(1)
    a = take_student()
    b = take_student()
    _send(db, a, "signup_success", activity=act.title, shift="上午场",
          time="2026-10-25 08:00", detail="", data={"activity_id": act.id})
    _send(db, b, "hours_locked", activity="另一个活动", result="已锁定")
    admin = login(client, ADMIN)

    by_sid = _rows(client, admin, since, person=a)
    assert by_sid and all(x["recipient_sid"] == a for x in by_sid)
    # 收件人：姓名包含，命中的是同一个人
    by_name = _rows(client, admin, since, person=student_row(db, a).name)
    assert [x["id"] for x in by_name] == [x["id"] for x in by_sid]
    # 类型
    rows = _rows(client, admin, since, template="hours_locked")
    assert rows and all(x["template_key"] == "hours_locked" for x in rows)
    assert rows[0]["template_label"] == "志愿时长已锁定"
    # 推送状态
    rows = _rows(client, admin, since, status="site")
    assert rows and all(x["status"] == "site" for x in rows)
    # 活动：按活动名筛，只有带这个活动编号的通知
    rows = _rows(client, admin, since, activity=act.title)
    assert rows and all(x["activity_id"] == act.id for x in rows)
    assert rows[0]["activity_title"] == act.title
    assert _rows(client, admin, since, activity="不存在的活动名") == []


def test_分页新的在前且带上今日统计(client, db, take_student):
    since = _since()
    sid = take_student()
    for i in range(3):
        _send(db, sid, "hours_locked", activity=f"活动 {i}", result="已锁定")
    admin = login(client, ADMIN)

    first = _page(client, admin, since, page=1, size=2, person=sid)
    assert first["total"] == 3 and len(first["items"]) == 2
    assert first["max_days"] == config.OPLOG_QUERY_MAX_DAYS
    assert first["templates"]["hours_locked"] == "志愿时长已锁定"
    # 今日统计不受筛选影响：今天发过的通知都在里面，且至少有这 3 条
    assert first["today"]["total"] >= 3
    second = _page(client, admin, since, page=2, size=2, person=sid)
    assert second["total"] == 3 and len(second["items"]) == 1
    assert second["items"][0]["created_at"] <= first["items"][-1]["created_at"]


def test_筛选参数校验(client):
    admin = login(client, ADMIN)
    assert client.get("/api/staff/notifications?status=nope",
                      headers=admin).status_code == 400
    assert client.get("/api/staff/notifications?page=0",
                      headers=admin).status_code == 422


def test_导出带齐各列(client, db, take_student, open_group):
    since = _since()
    act, _ = open_group(1)
    sid = take_student()
    _send(db, sid, "hours_locked", activity=act.title, result="已锁定",
          data={"activity_id": act.id})
    admin = login(client, ADMIN)

    query = urlencode({"start": since, "person": sid})
    r = client.get(dl_url(client, admin, f"/api/staff/notifications.xlsx?{query}"))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.spreadsheetml")
    ws = load_workbook(BytesIO(r.content)).active
    assert ws.max_row == 2                       # 表头 + 一条
    assert [c.value for c in ws[1]] == HEADER
    assert ws.cell(row=2, column=2).value == sid
    assert ws.cell(row=2, column=4).value == "志愿时长已锁定"
    assert ws.cell(row=2, column=7).value == act.title
    assert ws.cell(row=2, column=8).value == "仅站内"
    assert ws.cell(row=2, column=10).value == "不需签收"
    assert ws.cell(row=2, column=11).value == "系统"


def test_导出超上限回绝(client, db, take_student, monkeypatch):
    since = _since()
    sid = take_student()
    for _ in range(3):
        _send(db, sid, "hours_locked", activity="x", result="已锁定")
    monkeypatch.setattr(config, "OPLOG_EXPORT_MAX_ROWS", 2)
    query = urlencode({"start": since, "person": sid})
    r = client.get(dl_url(client, login(client, ADMIN),
                          f"/api/staff/notifications.xlsx?{query}"))
    assert r.status_code == 400
    assert "缩短时间段" in r.json()["detail"]


def test_只有管理员能看(client, take_student):
    student = take_student()
    for sid in (QINGXIE, LIAISON_MEMBER, student):    # 会长团、部门成员、学生都不行
        h = login(client, sid)
        assert client.get("/api/staff/notifications", headers=h).status_code == 403
        r = client.get(dl_url(client, h, "/api/staff/notifications.xlsx"))
        assert r.status_code == 403
