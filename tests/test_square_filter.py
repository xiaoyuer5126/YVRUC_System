"""活动广场的时间范围筛选：志愿者按「这几天我有空」找活动。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Activity, User, now, uid
from conftest import ADMIN, login


def _day(n: int) -> str:
    return (now() + timedelta(days=n)).strftime("%Y-%m-%d")


def _square(client, headers, **params) -> dict:
    r = client.get("/api/activities", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_广场按时间范围筛_沾到任一天就出现(client, open_group, take_student):
    """区间按服务场次的跨度算：活动只要有一天落在区间里就该出现。"""
    near = open_group(1, day_offset=3, start_in_hours=1)[0]     # 第 3 天开场
    far = open_group(1, day_offset=10, start_in_hours=1)[0]     # 第 10 天开场
    h = login(client, take_student())

    ids = {a["id"] for a in _square(client, h, status="", size=50)["items"]}
    assert {near.id, far.id} <= ids, "不填时间范围时两个都该在"

    got = _square(client, h, status="", size=50, start=_day(2), end=_day(4))
    picked = {a["id"] for a in got["items"]}
    assert near.id in picked and far.id not in picked
    # 别处留下的活动也可能落在这个区间里，所以只核「不少于本页」
    assert got["total"] >= len(picked)


def test_广场时间范围_没场次的活动不出现(client, db, take_student):
    """还没排场次的活动没有时间可对：填了范围就不列它。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"没排场次的活动 {uid('t')}",
                   intro="用例生成", location="测试地点",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=30),
                   created_by=admin.id)
    db.add(act)
    db.commit()
    h = login(client, take_student())

    assert act.id in {a["id"] for a in _square(client, h, status="", size=50)["items"]}
    got = _square(client, h, status="", size=50, start=_day(0), end=_day(2))
    assert act.id not in {a["id"] for a in got["items"]}


def test_广场时间范围_日期格式不对给400(client, take_student):
    h = login(client, take_student())
    assert client.get("/api/activities", headers=h,
                      params={"start": "不是日期"}).status_code == 400
    assert client.get("/api/activities", headers=h,
                      params={"end": "2026-13-40"}).status_code == 400
