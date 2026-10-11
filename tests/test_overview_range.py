"""数据总览的区间：只管「活动总数 / 有效报名 / 累计时长 / 报名学院分布」四张卡，
其余卡片（注册学生、报名中活动、候补中、24 小时签到、近 14 天趋势、最近留痕）
是此刻或固定窗口，给不给区间都一样。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from backend.models import (Activity, Enrollment, HoursRecord, Shift, day_of, now,
                            uid)
from conftest import ADMIN, login, student_row

COUNCIL = "2025003"      # 张远航，会长团：进得了数据总览
AUG = {"start": "2019-08-01", "end": "2019-08-31"}


def dt(text: str) -> datetime:
    """2019 年里的一个本地时间：'08-10 09:00' → 2019-08-10 09:00。"""
    return datetime.strptime(f"2019-{text}", "%Y-%m-%d %H:%M")


def _activity(db, title, start, end, *, status="finished"):
    act = Activity(id=uid("act"), title=title, intro="用例生成", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status=status,
                   reg_start=start - timedelta(days=7), reg_end=start,
                   created_by=student_row(db, ADMIN).id)
    db.add(act)
    db.flush()
    g = Shift(id=uid("g"), activity_id=act.id, name="上午场", mode="pick",
              start_time=start, end_time=end, sort=0)
    db.add(g)
    db.commit()
    return act, g


def _enroll(db, act, g, sid, *, joined_at=None):
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=student_row(db, sid).id, status="enrolled",
                      joined_at=joined_at or now()))
    db.commit()


def _hours(db, act, g, sid, hours):
    """一笔时长：一段，服务日期是场次开始那天。"""
    rec = HoursRecord(id=uid("hrs"), activity_id=act.id,
                      user_id=student_row(db, sid).id)
    db.add(rec)
    rec.add_segment(kind="shift", date=day_of(g.start_time), hours=hours,
                    label=g.name, shift_id=g.id)
    db.commit()


def _overview(client, headers, **params) -> dict:
    r = client.get("/api/staff/overview", headers=headers, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def test_区间只改四张卡(client, db, take_student):
    council = login(client, COUNCIL)
    base_all = _overview(client, council)["cards"]
    base_aug = _overview(client, council, **AUG)["cards"]

    inside, g_in = _activity(db, "总览八月里", dt("08-10 09:00"), dt("08-10 12:00"))
    outside, g_out = _activity(db, "总览九月里", dt("09-10 09:00"), dt("09-10 12:00"))
    # 一条报名发生在八月里（落区间），一条就是现在（区间外）
    _enroll(db, inside, g_in, take_student(), joined_at=dt("08-05 10:00"))
    _enroll(db, inside, g_in, take_student())
    _hours(db, inside, g_in, take_student(), 3)
    _hours(db, outside, g_out, take_student(), 5)

    full = _overview(client, council)
    aug = _overview(client, council, **AUG)
    cards, aug_cards = full["cards"], aug["cards"]

    # 活动总数：不填区间＝全部活动（含草稿与还没办的），填了＝区间内有服务的
    assert cards["activities_all"] - base_all["activities_all"] == 2
    assert aug_cards["activities_all"] - base_aug["activities_all"] == 1
    # 有效报名：填了区间只数区间内新产生的报名
    assert cards["active_enrollments"] - base_all["active_enrollments"] == 2
    assert aug_cards["active_enrollments"] - base_aug["active_enrollments"] == 1
    # 累计时长：只有服务日期在区间内的那一笔进账
    assert round(cards["hours_total"] - base_all["hours_total"], 1) == 8.0
    assert round(aug_cards["hours_total"] - base_aug["hours_total"], 1) == 3.0

    # 其余卡片在两种调用里完全一致
    for key in ("students", "activities_published", "waitlisted",
                "checked_recent"):
        assert cards[key] == aug_cards[key], key
    assert full["enroll_trend"] == aug["enroll_trend"]
    assert full["recent_logs"] == aug["recent_logs"]


def test_学院分布跟着区间的报名时间走(client, db, take_student):
    council = login(client, COUNCIL)
    inside, g = _activity(db, "总览八月学院", dt("08-12 09:00"), dt("08-12 12:00"))
    sid = take_student()
    college = student_row(db, sid).college

    def mine(d: dict) -> int:
        return {x["college"]: x["count"] for x in d["colleges"]}.get(college, 0)

    before = mine(_overview(client, council, **AUG))
    _enroll(db, inside, g, sid, joined_at=dt("08-06 10:00"))
    assert mine(_overview(client, council, **AUG)) - before == 1


def test_只给一侧边界(client, db, take_student):
    council = login(client, COUNCIL)
    base_lo = _overview(client, council, start="2019-07-01")["cards"]
    base_hi = _overview(client, council, end="2019-06-30")["cards"]
    act, g = _activity(db, "总览七月里", dt("07-20 09:00"), dt("07-20 12:00"))
    _enroll(db, act, g, take_student(), joined_at=dt("07-15 10:00"))

    # 只给 start：服务场次在 7/1 及以后开始的活动算进来
    lo = _overview(client, council, start="2019-07-01")["cards"]
    assert lo["activities_all"] - base_lo["activities_all"] == 1
    # 只给 end：6/30 之前的窗口装不下这场七月的活动
    hi = _overview(client, council, end="2019-06-30")["cards"]
    assert hi["activities_all"] == base_hi["activities_all"]


def test_日期格式不对给400(client):
    council = login(client, COUNCIL)
    for params in ({"start": "2019-13-01"}, {"end": "昨天"}):
        r = client.get("/api/staff/overview", headers=council, params=params)
        assert r.status_code == 400, r.text
