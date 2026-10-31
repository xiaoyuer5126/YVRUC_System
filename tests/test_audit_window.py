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


def _all_ids(client, headers, **params) -> set[str]:
    """一页页翻到底，把符合条件的全部 id 收齐：别的用例留下的留痕，
    待查的那条未必落在第一页。"""
    ids, page = set(), 1
    while True:
        r = _get(client, headers, page=page, size=100, **params)
        assert r.status_code == 200, r.text
        body = r.json()
        ids |= {x["id"] for x in body["items"]}
        if page * 100 >= body["total"]:
            return ids
        page += 1


def test_不填时间只看最近七天(client, admin, db):
    old = _log(db, created_at=now() - timedelta(days=30))
    fresh = _log(db, created_at=now() - timedelta(days=1))

    r = _get(client, admin)
    assert r.status_code == 200, r.text
    body = r.json()
    # 默认窗口要让页面显示得出来，否则管理员不知道自己在看哪一段
    assert body["start"] and body["end"]
    assert body["max_days"] == 31
    # 三十天前那条不在默认窗口里，一天前的在
    ids = _all_ids(client, admin)
    assert fresh.id in ids and old.id not in ids


def test_选更早的区间能看到旧记录(client, admin, db):
    old = _log(db, created_at=now() - timedelta(days=30))
    start = (now() - timedelta(days=40)).strftime("%Y-%m-%d")
    end = (now() - timedelta(days=20)).strftime("%Y-%m-%d")
    assert old.id in _all_ids(client, admin, start=start, end=end)


def test_一次最多三十一天(client, admin):
    start = (now() - timedelta(days=60)).strftime("%Y-%m-%d")
    r = _get(client, admin, start=start)
    assert r.status_code == 400
    assert "31" in r.text


def test_时间格式不对给四百(client, admin):
    assert _get(client, admin, start="昨天").status_code == 400
    assert _get(client, admin, start="2026-13-01").status_code == 400


def test_指定活动不填时间看这场活动的全部留痕(client, admin, db, open_group):
    """活动管理页的「变更记录」只带 activity_id：立项一个多月前的改动也要看得到。"""
    act, _g = open_group(5)
    old = _log(db, activity_id=act.id, created_at=now() - timedelta(days=60))
    other = _log(db, created_at=now() - timedelta(days=1))

    ids = _all_ids(client, admin, activity_id=act.id)
    assert old.id in ids and other.id not in ids
