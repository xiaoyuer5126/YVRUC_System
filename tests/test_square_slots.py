"""活动广场按可参与时段筛：周视图里一格两小时，选中的格子和场次时段有重叠就算。"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.models import Activity, ShiftPeriod, now, uid


def _day():
    # 放到很远的将来，不和别的用例造的场次撞在同一天
    return (now() + timedelta(days=700)).date()


def _periods(db, g, rows):
    for p in list(g.periods):
        db.delete(p)
    db.flush()
    for i, (a, b) in enumerate(rows):
        db.add(ShiftPeriod(id=uid("sp"), shift_id=g.id, sort=i, start_time=a, end_time=b))
    db.commit()


def _at(d, hour, days=0):
    return datetime.combine(d, datetime.min.time()) + timedelta(days=days, hours=hour)


def _ids(client, admin, slots):
    r = client.get(f"/api/activities?size=50&slots={','.join(slots)}", headers=admin)
    assert r.status_code == 200, r.text
    return {x["id"]: x.get("slot_note") for x in r.json()["items"]}


def test_按时段筛_重叠才出现_多时段和时间灵活另有标注(client, admin, db, open_group):
    d = _day()
    two, g2 = open_group(5)
    _periods(db, g2, [(_at(d, 9), _at(d, 11)), (_at(d, 9, days=2), _at(d, 11, days=2))])
    flex, gf = open_group(5)
    gf.flexible_time = True
    _periods(db, gf, [(_at(d, 0), _at(d, 0, days=30))])
    key = f"{d:%Y-%m-%d}"

    # 9:00-11:00 和 8-10、10-12 两格都沾边；12-14 不沾
    got = _ids(client, admin, [f"{key}T08"])
    assert got[two.id] == {"periods": 2, "flexible": False}
    assert two.id in _ids(client, admin, [f"{key}T10"])
    assert two.id not in _ids(client, admin, [f"{key}T12"])
    # 时间灵活的场次：所选格子那天有它的时段就算，钟点不论
    assert _ids(client, admin, [f"{key}T20"])[flex.id] == {"periods": 1, "flexible": True}
    before = f"{d - timedelta(days=3):%Y-%m-%d}"
    assert not {two.id, flex.id} & set(_ids(client, admin, [f"{before}T08"]))

    # 只看报名中的：截止了就不在里面
    db.get(Activity, two.id).reg_end = now() - timedelta(minutes=1)
    db.commit()
    assert two.id not in _ids(client, admin, [f"{key}T08"])


def test_按时段筛_格子必须落在整两小时上(client, admin):
    key = f"{_day():%Y-%m-%d}"
    for bad in (f"{key}T09", f"{key}T22", "明天上午"):
        r = client.get(f"/api/activities?slots={bad}", headers=admin)
        assert r.status_code == 400, bad
