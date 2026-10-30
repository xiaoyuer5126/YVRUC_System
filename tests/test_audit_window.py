"""审计日志的时间窗：默认最近 7 天，一次最多 31 天。

留痕只增不减，不限时间时每翻一页都要数、排整张表。默认窗口和上限写死在这里，
改之前先想清楚十年量级下这一页还能不能打开。
"""
from __future__ import annotations

from datetime import timedelta

from backend.models import AuditLog, now, uid


def _log(db, **kw) -> AuditLog:
    row = AuditLog(id=uid("log"), action="update", target_type="activity",
                   summary="用例生成", **kw)
    db.add(row)
    db.commit()
    return row


def _get(client, headers, **params):
    return client.get("/api/staff/audit-logs", headers=headers, params=params)


def test_不填时间只看最近七天(client, admin, db):
    old = _log(db, created_at=now() - timedelta(days=30))
    fresh = _log(db, created_at=now() - timedelta(days=1))

    r = _get(client, admin)
    assert r.status_code == 200, r.text
    ids = {x["id"] for x in r.json()["items"]}
    assert fresh.id in ids and old.id not in ids
    # 默认窗口要让页面显示得出来，否则管理员不知道自己在看哪一段
    body = r.json()
    assert body["start"] and body["end"]
    assert body["max_days"] == 31


def test_选更早的区间能看到旧记录(client, admin, db):
    old = _log(db, created_at=now() - timedelta(days=30))
    start = (now() - timedelta(days=40)).strftime("%Y-%m-%d")
    end = (now() - timedelta(days=20)).strftime("%Y-%m-%d")
    r = _get(client, admin, start=start, end=end)
    assert r.status_code == 200, r.text
    assert old.id in {x["id"] for x in r.json()["items"]}


def test_一次最多三十一天(client, admin):
    start = (now() - timedelta(days=60)).strftime("%Y-%m-%d")
    r = _get(client, admin, start=start)
    assert r.status_code == 400
    assert "31" in r.text


def test_时间格式不对给四百(client, admin):
    assert _get(client, admin, start="昨天").status_code == 400
    assert _get(client, admin, start="2026-13-01").status_code == 400
