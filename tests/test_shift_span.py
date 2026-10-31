"""活动上的服务场次跨度与展示状态的 SQL 版。

广场把展示状态写进 SQL 之后，状态只在下列这三人手里会变：场次、时段、
活动自己的报名时间。跨度要是漏同步，广场上就会把这个活动放错筛选、
排到错的位置；这里逐条钉住同步与两套算法的一致性。
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from backend.models import Activity, ShiftPeriod, now, uid
from backend.serializers import display_status_expr, display_status_of
from conftest import ADMIN, login, mk_shift


def _reload(db, act_id):
    db.expire_all()
    return db.get(Activity, act_id)


def _span(act):
    return (act.shift_first_start, act.shift_last_end, act.shift_last_start)


def test_建场次就把跨度写上(client, db, open_group):
    act, g = open_group(5)
    act = _reload(db, act.id)
    assert _span(act) == (g.start_time, g.end_time, g.start_time)


def test_改时段就地重算(client, db, open_group):
    act, g = open_group(5)
    later = g.start_time + timedelta(hours=6)
    p = g.periods[0]
    p.start_time, p.end_time = later, later + timedelta(hours=2)
    db.commit()

    act = _reload(db, act.id)
    assert _span(act) == (later, later + timedelta(hours=2), later)


def test_多时段场次的最晚开始算的是开场那一刻(client, db, open_group):
    """同一场里另加一段更晚的时段：那个场次的开始仍是它最早那一段。

    报名按场次截止，最后一场在它最早那一段开始时就报不进去了；拿最后一段的
    开始当作「最晚开始」，多出来的那几天广场会一直写着报名中。
    """
    act, g = open_group(5)
    first = g.start_time - timedelta(days=3)
    db.add(ShiftPeriod(id=uid("sp"), shift_id=g.id, sort=1,
                       start_time=first, end_time=first + timedelta(hours=2)))
    db.commit()

    act = _reload(db, act.id)
    assert act.shift_first_start == first
    assert act.shift_last_start == g.start_time


def test_跨场次取最晚那一场的开场(client, db, open_group):
    act, g = open_group(5)
    start = g.start_time + timedelta(days=2)
    later = mk_shift(id=uid("g"), activity_id=act.id, name="第二场",
                     start_time=start, end_time=start + timedelta(days=3), sort=1)
    # 第二场自己也有两段：最晚一段开始得比开场晚两天
    later.periods.append(ShiftPeriod(id=uid("sp"), shift_id=later.id, sort=1,
                                     start_time=start + timedelta(days=2),
                                     end_time=start + timedelta(days=3)))
    db.add(later)
    db.commit()

    act = _reload(db, act.id)
    assert act.shift_last_start == start
    assert act.shift_last_end == start + timedelta(days=3)


def test_删场次后按剩下的重算(client, db, open_group):
    act, g = open_group(5)
    start = g.start_time + timedelta(days=2)
    other = mk_shift(id=uid("g"), activity_id=act.id, name="第二场",
                     start_time=start, end_time=start + timedelta(hours=2), sort=1)
    db.add(other)
    db.commit()
    assert _reload(db, act.id).shift_last_start == start

    db.delete(other)
    db.commit()
    act = _reload(db, act.id)
    assert act.shift_last_start == g.periods[0].start_time
    assert act.shift_last_end == g.periods[0].end_time


def test_减少段数后按剩下的段重算(client, db, open_group):
    """增减段数时是先清空再重建（见 staff_activities._set_periods）：同一次
    flush 里新段先插、旧段后删，删完要再算一次，否则跨度里还带着旧段。"""
    act, g = open_group(5)
    first = g.periods[0]
    later = first.start_time + timedelta(days=3)
    g.periods.append(ShiftPeriod(id=uid("sp"), shift_id=g.id, sort=1,
                                 start_time=later,
                                 end_time=later + timedelta(hours=2)))
    db.commit()
    assert _reload(db, act.id).shift_last_end == later + timedelta(hours=2)

    g = db.get(type(g), g.id)
    keep_start, keep_end = g.periods[0].start_time, g.periods[0].end_time
    g.periods.clear()
    g.periods.append(ShiftPeriod(id=uid("sp"), shift_id=g.id, sort=0,
                                 start_time=keep_start, end_time=keep_end))
    db.commit()
    assert _span(_reload(db, act.id)) == (keep_start, keep_end, keep_start)


def test_培训彩排不算进跨度(client, db, open_group):
    act, g = open_group(5)
    before = g.start_time - timedelta(days=5)
    db.add(mk_shift(id=uid("g"), activity_id=act.id, kind="training", name="培训",
                    start_time=before, end_time=before + timedelta(hours=2)))
    db.commit()

    act = _reload(db, act.id)
    assert act.shift_first_start == g.start_time


@pytest.mark.parametrize("status", ["published", "closed", "finished"])
def test_sql状态与现算一致(client, db, open_group, status):
    """同一批数据下，两套算法给出一样的码。"""
    act, _g = open_group(5)
    db.query(Activity).filter(Activity.id == act.id).update(
        {"status": status}, synchronize_session=False)
    db.commit()

    t = now()
    act = _reload(db, act.id)
    code, _label = display_status_of(act.status, act.reg_start, act.reg_end,
                                     act.time_span(), act.last_shift_start(), t)
    with db.bind.connect() as conn:
        got = conn.execute(
            select(display_status_expr(t)).where(Activity.id == act.id)).scalar()
    assert got == code


def test_场次全过去之后是已结束(client, db, open_group):
    act, g = open_group(5)
    past = now() - timedelta(days=10)
    p = g.periods[0]
    p.start_time, p.end_time = past, past + timedelta(hours=3)
    db.commit()

    with db.bind.connect() as conn:
        got = conn.execute(
            select(display_status_expr()).where(Activity.id == act.id)).scalar()
    assert got == "finished"


def test_广场按状态筛只出该状态的(client, db, open_group):
    """已结束的活动不该出现在「报名中」的筛选结果里。"""
    act, g = open_group(5)
    past = now() - timedelta(days=20)
    p = g.periods[0]
    p.start_time, p.end_time = past, past + timedelta(hours=3)
    db.commit()

    headers = login(client, ADMIN)
    r = client.get("/api/activities?status=published&size=50", headers=headers)
    assert r.status_code == 200, r.text
    assert act.id not in {x["id"] for x in r.json()["items"]}

    r = client.get("/api/activities?status=finished&size=50", headers=headers)
    assert act.id in {x["id"] for x in r.json()["items"]}
