"""报酬方式按段选：同一场活动里一段岗位可选劳务、一段只记志愿时长。

一个人在一场活动里只有一笔时长，但这笔里各段（场次 + 岗位）的报酬方式
各选各的：开了「可选劳务」的段能选劳务，其余段只能记志愿时长。
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from openpyxl import load_workbook

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, HoursRecord, Position,
                            Shift, User, new_checkin_token, now, uid)
from conftest import mk_shift
from conftest import ADMIN, POSITION_TEXT, login, student_row


@pytest.fixture
def mixed(db, take_student):
    """已结束的活动，两场服务：上午 4 小时（岗位可选劳务）、下午 3 小时
    （岗位只记志愿时长）。一名志愿者两场都签到签退过。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"按段报酬 {uid('t')}",
                   intro="x", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="finished", reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5), vb_names=["按段报酬志愿服务"],
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    day = now().replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=2)
    am = mk_shift(id=uid("g"), activity_id=act.id, name="上午场", start_time=day,
               end_time=day + timedelta(hours=4), mode="pick", sort=0)
    pm = mk_shift(id=uid("g"), activity_id=act.id, name="下午场",
               start_time=day + timedelta(hours=5),
               end_time=day + timedelta(hours=8), mode="pick", sort=1)
    db.add_all([am, pm])
    db.flush()
    paid = Position(id=uid("p"), shift_id=am.id, name="搬运", quota=5, sort=0,
                    pay_optional=True, pay_note="100 元/天，活动后统一发放",
                    **POSITION_TEXT)
    plain = Position(id=uid("p"), shift_id=pm.id, name="引导", quota=5, sort=0,
                     **POSITION_TEXT)
    db.add_all([paid, plain])
    db.flush()
    u = student_row(db, take_student())
    u.id_card = "110101200001011234"
    for g, pos in ((am, paid), (pm, plain)):
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, position_id=pos.id, status="enrolled",
                          joined_at=now()))
        task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                           period_id=g.periods[0].id,
                           mode="shift", token=new_checkin_token(),
                           created_by=admin.id,
                           valid_from=g.start_time, valid_to=g.end_time)
        db.add(task)
        db.flush()
        db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                             shift_id=g.id, period_id=g.periods[0].id,
                             position_id=pos.id, user_id=u.id,
                             method="manual", checkin_at=g.start_time,
                             checkout_at=g.end_time))
    db.commit()
    return act, am, pm, paid, plain, u


def _confirm_all(client, admin, act, *, approve=True):
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    if approve:
        r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                        headers=admin, json={})
        assert r.status_code == 200, r.text


def _mine(client, h):
    return client.get("/api/me/hours", headers=h).json()


def _pick(client, h, rec_id, *choices):
    return client.post(f"/api/me/hours/{rec_id}/confirm", headers=h, json={
        "parts": [{"shift_id": g.id, "position_id": p.id, "reward": r}
                  for g, p, r in choices]})


def _record(db, act):
    db.expire_all()
    return db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).one()


def test_按段选劳务_只有可劳务的段能选(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    rec = _mine(client, stu)["records"][0]
    assert rec["hours"] == 7.0 and rec["pay_allowed"]
    by_shift = {p["shift_id"]: p for p in rec["parts"]}
    assert by_shift[am.id]["pay_ok"] and by_shift[am.id]["position_id"] == paid.id
    assert "100 元" in by_shift[am.id]["pay_note"]
    assert not by_shift[pm.id]["pay_ok"] and by_shift[pm.id]["reward"] == "hours"

    r = _pick(client, stu, rec["id"], (am, paid, "pay"), (pm, plain, "hours"))
    assert r.status_code == 200, r.text
    assert r.json()["counted_hours"] == 3.0 and r.json()["pay_hours"] == 4.0
    assert r.json()["reward_label"] == "志愿时长 3h + 劳务 4h"

    d = _mine(client, stu)
    assert d["total"] == 3.0
    by_shift = {p["shift_id"]: p for p in d["records"][0]["parts"]}
    assert by_shift[am.id]["reward"] == "pay" and by_shift[pm.id]["reward"] == "hours"

    # 管理端那一格看得出这笔里有劳务
    item = client.get(f"/api/staff/activities/{act.id}/hours",
                      headers=admin).json()["items"][0]
    assert item["pay_hours"] == 4.0 and item["volunteer_confirmed_at"]


def test_不可劳务的段提交劳务被拒(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    rec_id = _mine(client, stu)["records"][0]["id"]
    r = _pick(client, stu, rec_id, (am, paid, "pay"), (pm, plain, "pay"))
    assert r.status_code == 400 and "没有开放劳务" in r.json()["detail"]
    # 拒了就整笔不动：可劳务那段也没被记成劳务，也没算确认
    rec = _record(db, act)
    assert rec.confirmed_at is None and rec.pay_hours() == 0
    # 张冠李戴（拿下午场配上午的岗位）也不行
    r = _pick(client, stu, rec_id, (pm, paid, "pay"))
    assert r.status_code == 400 and "没有这一段" in r.json()["detail"]


def test_重算不丢选择_改了数就撤销核准并清掉劳务(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    rec_id = _mine(client, stu)["records"][0]["id"]
    _pick(client, stu, rec_id, (am, paid, "pay"))

    _confirm_all(client, admin, act, approve=False)        # 重算一遍，数没变
    rec = _record(db, act)
    assert rec.hours == 7.0 and rec.pay_hours() == 4.0 and rec.counted_hours() == 3.0
    assert rec.confirmed_at is not None
    assert db.get(Activity, act.id).hours_approved_at is not None

    # 逐个点名改合计：数变了，本人对着旧数选的劳务清掉、确认作废，核准撤销
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm", headers=admin,
                    json={"items": [{"student_id": u.student_id, "hours": 6}]})
    assert r.status_code == 200, r.text
    assert r.json()["reapproval_required"] is True
    rec = _record(db, act)
    assert rec.hours == 6.0 and rec.pay_hours() == 0 and rec.counted_hours() == 6.0
    assert rec.confirmed_at is None
    assert db.get(Activity, act.id).hours_approved_at is None
    # 没核准时本人选不了
    assert _pick(client, stu, rec_id, (am, paid, "pay")).status_code == 409

    # 重新核准时通知到他，他再选一次
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert _pick(client, stu, rec_id, (am, paid, "pay")).status_code == 200
    rec = _record(db, act)
    assert rec.pay_hours() == 4.0 and rec.counted_hours() == 2.0


def test_岗位关了劳务_重算时那段回到志愿时长(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    _pick(client, stu, _mine(client, stu)["records"][0]["id"], (am, paid, "pay"))
    db.get(Position, paid.id).pay_optional = False
    db.commit()
    _confirm_all(client, admin, act, approve=False)
    assert _record(db, act).pay_hours() == 0


def _close_and_lock(client, admin, db, act):
    from backend import scheduled

    db.expire_all()
    db.get(Activity, act.id).appeal_deadline = now() - timedelta(minutes=1)
    db.commit()
    scheduled.submit_lock_when_due(db)
    rows = [x for x in client.get("/api/staff/hours-requests", headers=admin).json()
            if x["activity_id"] == act.id and x["kind"] == "lock"]
    assert len(rows) == 1, rows
    r = client.post(f"/api/staff/hours-requests/{rows[0]['id']}/decide",
                    headers=admin, json={"approve": True, "reply": "核对无误"})
    assert r.status_code == 200, r.text


def test_锁定时确认过的保留_没表态的全记志愿时长(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    rec_id = _mine(client, stu)["records"][0]["id"]
    _pick(client, stu, rec_id, (am, paid, "pay"))
    _close_and_lock(client, admin, db, act)
    rec = _record(db, act)
    assert rec.pay_hours() == 4.0 and _mine(client, stu)["locked"] == 3.0


def test_锁定时没表态的劳务退回志愿时长(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    stu = login(client, u.student_id)
    rec_id = _mine(client, stu)["records"][0]["id"]
    _pick(client, stu, rec_id, (am, paid, "pay"))
    # 只选了劳务、没点确认，再锁定：劳务退回志愿时长
    rec = _record(db, act)
    rec.confirmed_at = None
    db.commit()
    assert _record(db, act).pay_hours() == 4.0
    _close_and_lock(client, admin, db, act)
    rec = _record(db, act)
    assert rec.confirmed_at is not None and rec.pay_hours() == 0
    assert _mine(client, stu)["locked"] == 7.0


def test_导出累计概览统计都按段求和(client, admin, db, mixed):
    act, am, pm, paid, plain, u = mixed
    _confirm_all(client, admin, act)
    before = client.get("/api/staff/overview", headers=admin).json()["cards"]["hours_total"]
    stu = login(client, u.student_id)
    _pick(client, stu, _mine(client, stu)["records"][0]["id"], (am, paid, "pay"))

    assert _mine(client, stu)["total"] == 3.0
    ov = client.get(f"/api/staff/activities/{act.id}/overview", headers=admin).json()
    assert ov["hours"]["total"] == 3.0 and ov["hours"]["records"] == 1
    after = client.get("/api/staff/overview", headers=admin).json()["cards"]["hours_total"]
    assert round(before - after, 1) == 4.0

    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    assert r.status_code == 200
    rows = list(load_workbook(BytesIO(r.content)).active.values)
    assert len(rows) == 2
    # 只导计时长的那部分：时长 3，备注只有下午那段
    assert rows[1][3] == 3.0
    assert "引导" in rows[1][4] and "搬运" not in rows[1][4]

