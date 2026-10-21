"""场次多时段：冲突按段比、锁定看第一段、签到与时长按段累加。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import enroll_logic
from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, Position, Shift, now,
                            new_checkin_token, uid)
from conftest import (ADMIN, POSITION_TEXT, login, mk_shift, student_row)
from test_enroll import enroll


def _activity_with_shift(db, periods, *, capacity=5, lock_hours=0,
                         title="多段场次活动"):
    admin = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"{title} {uid('t')}", intro="x",
                   location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=60), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = mk_shift(id=uid("g"), activity_id=act.id, name="多段场次",
                 periods=periods, mode="pick", sort=0, lock_hours=lock_hours)
    db.add(g)
    db.flush()
    p = Position(id=uid("p"), shift_id=g.id, name="引导", quota=capacity,
                 waitlist_quota=3, sort=0, **POSITION_TEXT)
    db.add(p)
    db.flush()
    g = db.get(Shift, g.id)
    g.test_position_id = p.id
    db.commit()
    return act, g


def _at(day, hour, minute=0):
    return day.replace(hour=hour, minute=minute, second=0, microsecond=0)


def test_时段之间空着的日子不算冲突(client, db, take_student):
    base = now() + timedelta(days=210)
    act1, g1 = _activity_with_shift(db, [
        {"start_time": _at(base, 9), "end_time": _at(base, 12)},
        {"start_time": _at(base + timedelta(days=4), 9),
         "end_time": _at(base + timedelta(days=4), 12)},
    ])
    act2, g2 = _activity_with_shift(db, [
        {"start_time": _at(base + timedelta(days=2), 10),
         "end_time": _at(base + timedelta(days=2), 11)},
    ])
    h = login(client, take_student())
    assert enroll(client, h, act1, g1).status_code == 200
    # 中间那天不在这场次的任何时段里，不该提示冲突
    assert enroll(client, h, act2, g2).status_code == 200

    act3, g3 = _activity_with_shift(db, [
        {"start_time": _at(base + timedelta(days=4), 10),
         "end_time": _at(base + timedelta(days=4), 11)},
    ])
    r = enroll(client, h, act3, g3)
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "time_conflict"


def test_连续同钟点的时段合成日期范围(db, take_student):
    day1 = now().replace(hour=9, minute=0, second=0, microsecond=0)
    _, g = _activity_with_shift(db, [
        {"start_time": day1, "end_time": day1.replace(hour=12)},
        {"start_time": day1 + timedelta(days=1),
         "end_time": (day1 + timedelta(days=1)).replace(hour=12)},
        {"start_time": day1 + timedelta(days=5),
         "end_time": (day1 + timedelta(days=5)).replace(hour=12)},
    ])
    text = g.span_text()
    assert len(g.periods) == 3
    assert "~" in text and text.count("、") == 1
    assert text.count("09:00-12:00") == 2


def test_名单锁定按第一段开场算(db, take_student):
    first = now() + timedelta(hours=2)
    _, g = _activity_with_shift(db, [
        {"start_time": first, "end_time": first + timedelta(hours=3)},
        {"start_time": first + timedelta(days=5),
         "end_time": first + timedelta(days=5, hours=3)},
    ], lock_hours=12)
    assert enroll_logic.lock_at(g) == first - timedelta(hours=12)


def test_时长按段累加(client, admin, db, take_student):
    base = (now() - timedelta(days=1)).replace(hour=0, minute=0, second=0,
                                               microsecond=0)
    p1s, p1e = _at(base, 9), _at(base, 12)
    p2s, p2e = _at(base, 14), _at(base, 17)
    act, g = _activity_with_shift(db, [
        {"start_time": p1s, "end_time": p1e},
        {"start_time": p2s, "end_time": p2e},
    ])
    u = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, position_id=g.test_position_id,
                      status="enrolled", joined_at=now()))
    admin_user = student_row(db, ADMIN)
    tasks = []
    for p in g.periods:
        t = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                        period_id=p.id, mode="shift",
                        token=new_checkin_token(), created_by=admin_user.id)
        db.add(t)
        tasks.append(t)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=tasks[0].id, activity_id=act.id,
                         shift_id=g.id, period_id=g.periods[0].id,
                         position_id=g.test_position_id, user_id=u.id,
                         method="manual", checkin_at=p1s + timedelta(minutes=10),
                         checkout_at=p1e))
    db.add(CheckinRecord(id=uid("ckr"), task_id=tasks[1].id, activity_id=act.id,
                         shift_id=g.id, period_id=g.periods[1].id,
                         position_id=g.test_position_id, user_id=u.id,
                         method="manual", checkin_at=p2s,
                         checkout_at=p2s + timedelta(hours=2)))
    db.commit()

    rows = client.get(f"/api/staff/activities/{act.id}/hours",
                      headers=admin).json()["items"]
    mine = next(x for x in rows if x["user"]["id"] == u.id)
    assert len(mine["parts"]) == 2
    assert sum(p["hours"] for p in mine["parts"]) == pytest.approx(4.83, abs=0.01)
    assert mine["hours"] == pytest.approx(5.0)


def test_签到码按时段各开一张(client, admin, db, take_student):
    base = now().replace(second=0, microsecond=0)
    act, g = _activity_with_shift(db, [
        {"start_time": base + timedelta(hours=1),
         "end_time": base + timedelta(hours=3)},
        {"start_time": base + timedelta(days=2),
         "end_time": base + timedelta(days=2, hours=3)},
    ])
    url = f"/api/staff/shifts/{g.id}/checkin-tasks"
    first = {"mode": "shift", "period_id": g.periods[0].id}
    assert client.post(url, headers=admin, json=first).status_code == 200
    second = {"mode": "shift", "period_id": g.periods[1].id}
    assert client.post(url, headers=admin, json=second).status_code == 200
    # 同一段不能同时开两张；不同的段互不影响
    again = client.post(url, headers=admin, json=first)
    assert again.status_code == 409
