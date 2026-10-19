"""定时任务：活动状态自动流转、签到提醒。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import scheduled
from backend.models import (Activity, ActivityManager, Enrollment,
                            Notification, Position, Shift, User, new_checkin_token,
                            now, uid)
from conftest import ADMIN, POSITION_TEXT, editing, login


@pytest.fixture
def make_activity(db):
    """按指定的报名截止/场次时间造一个活动，用于驱动状态流转。

    场次是普通的「报名即选岗位」场次，带一个真岗位：流转与签到提醒都不看
    岗位，排岗提醒另有 open_group 造的真实数据。
    """
    def _make(status: str, *, reg_end_in_hours: float, start_in_hours: float,
              duration_hours: float = 3):
        admin = db.query(User).filter(User.student_id == ADMIN).one()
        act = Activity(id=uid("act"), title=f"流转测试 {uid('t')}",
                       intro="x", location="x",
                       contacts=[{"name": "测试", "phone": "13900000000"}],
                       contact_group_qr="x", status=status,
                       reg_start=now() - timedelta(days=3),
                       reg_end=now() + timedelta(hours=reg_end_in_hours),
                       created_by=admin.id)
        db.add(act)
        db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                               user_id=admin.id, added_by=admin.id))
        g = Shift(id=uid("g"), activity_id=act.id, name="场次",
                  start_time=now() + timedelta(hours=start_in_hours),
                  end_time=now() + timedelta(hours=start_in_hours
                                             + duration_hours),
                  mode="pick", sort=0)
        db.add(g)
        db.flush()
        p = Position(id=uid("p"), shift_id=g.id, name="岗位甲", quota=5,
                     sort=0, **POSITION_TEXT)
        db.add(p)
        db.commit()
        g.test_position_id = p.id
        return act, g
    return _make


@pytest.mark.parametrize("status, reg_end_in, start_in, expected", [
    ("published", -1, 48, "closed"),       # 报名截止 → 已截止
    ("closed", -72, -10, "finished"),      # 场次都结束了 → 已结束
    ("published", -72, -10, "finished"),   # 两步都到点了，一轮走完
    ("draft", -72, -10, "draft"),          # 筹备中的不自动流转
    ("published", 24, 48, "published"),    # 还没到时间的不动
])
def test_活动状态自动流转(db, make_activity, status, reg_end_in, start_in,
                          expected):
    act, _g = make_activity(status, reg_end_in_hours=reg_end_in,
                            start_in_hours=start_in)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == expected


def _enroll(db, act, g, student_id):
    u = db.query(User).filter(User.student_id == student_id).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, position_id=g.test_position_id,
                      status="enrolled", joined_at=now()))
    db.commit()
    return u


def _staffing_notices(db, act):
    return (db.query(Notification)
            .filter(Notification.title == f"报名已截止，请排岗｜{act.title}")
            .all())


def _close_registration(db, act):
    """把报名截止时间拨到过去，再跑一轮流转。"""
    db.expire_all()
    db.get(Activity, act.id).reg_end = now() - timedelta(minutes=1)
    db.commit()
    scheduled.auto_advance_activities(db)


def test_截止时提醒管理者排岗_统一分配还没建岗位(client, db, open_group,
                                                 take_student):
    """线上的「截止后统一分配」：人都挂在占位岗位上，真岗位一个没有。"""
    act, g = open_group(5, mode="assign")
    for _ in range(2):
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=login(client, take_student()), json={})
        assert r.status_code == 200, r.text
    _close_registration(db, act)

    notices = _staffing_notices(db, act)
    assert notices, "占位岗位上还挂着人，截止时必须提醒排岗"
    assert "「测试场次」还没有岗位，2 人等待分配" in notices[0].content


def test_截止时提醒管理者排岗_岗位建了人没分完(client, admin, db, open_group,
                                               take_student):
    """场次里已有真岗位，但报名的人还在占位岗位上——最常见的漏排。"""
    act, g = open_group(5)               # 先按 pick 建好一个真岗位
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                         json={"mode": "assign", "capacity": 5})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()), json={})
    assert r.status_code == 200, r.text
    _close_registration(db, act)

    notices = _staffing_notices(db, act)
    assert notices and "「测试场次」还有 1 人没分到岗位" in notices[0].content


def test_统一分配场次没人报名截止时不打扰(db, open_group):
    act, _g = open_group(5, mode="assign")
    _close_registration(db, act)
    assert _staffing_notices(db, act) == []


def test_排完的活动截止时不打扰(client, admin, db, open_group, take_student):
    """照线上流程走一遍：直接在场次下建岗位 → 分配，人全离开占位。"""
    act, g = open_group(5, mode="assign")
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()), json={})
    assert r.status_code == 200, r.text
    with editing(client, admin, act.id):
        pid = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                          json={"name": "分拣", "quota": 5, **POSITION_TEXT}
                          ).json()["id"]
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id).one()
    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"source": "manual",
                          "plan": [{"enrollment_id": enr.id,
                                    "position_id": pid}]})
    assert r.status_code == 200, r.text

    _close_registration(db, act)
    db.refresh(act)
    assert act.status == "closed"
    assert _staffing_notices(db, act) == []


def test_日报任务真的在每轮里跑(db, monkeypatch, caplog):
    """写好却没接进 run_once 的话，线上永远不会发——只有这一条能发现。"""
    calls = []
    real = scheduled.send_roster_digests

    def spy(session):
        calls.append(session)
        return real(session)

    monkeypatch.setattr(scheduled, "send_roster_digests", spy)
    with caplog.at_level("ERROR", logger="backend.scheduled"):
        scheduled.run_once(db)
    assert calls == [db]
    # run_once 兜住了每个任务的异常；日报出错的话只会留一行日志
    assert not [r for r in caplog.records if "digests" in r.getMessage()]


def _reminders_of(db, user_id):
    return (db.query(Notification)
            .filter(Notification.user_id == user_id,
                    Notification.template_key == "checkin_reminder").count())


def test_reminder_fires_once_before_start(db, make_activity, take_student):
    """开始前 30 分钟内推一次，且只推一次。

    只数这个人收到几条：返回值是全库这一轮发出的总数，演示数据里
    赶巧也在 30 分钟内开始的场次会把它撑大。
    """
    act, g = make_activity("closed", reg_end_in_hours=-48, start_in_hours=0.4)
    u = _enroll(db, act, g, take_student())

    scheduled.send_checkin_reminders(db)
    assert _reminders_of(db, u.id) == 1

    scheduled.send_checkin_reminders(db)          # 再跑一轮不该重复发
    assert _reminders_of(db, u.id) == 1


def test_no_reminder_too_early(db, make_activity, take_student):
    act, g = make_activity("closed", reg_end_in_hours=-48, start_in_hours=5)
    u = _enroll(db, act, g, take_student())
    scheduled.send_checkin_reminders(db)
    assert _reminders_of(db, u.id) == 0


def test_no_backfill_for_started_sessions(db, make_activity, take_student):
    """服务停了一阵再起来，不该把早就开始的场次的提醒补发出去。"""
    act, g = make_activity("closed", reg_end_in_hours=-48, start_in_hours=-2)
    u = _enroll(db, act, g, take_student())
    scheduled.send_checkin_reminders(db)
    assert _reminders_of(db, u.id) == 0


def test_manual_reminder_skips_checked_in(client, db, admin, make_activity,
                                          take_student):
    act, g = make_activity("closed", reg_end_in_hours=-48, start_in_hours=1)
    u = _enroll(db, act, g, take_student())

    r = client.post(f"/api/staff/shifts/{g.id}/checkin-reminder", headers=admin)
    assert r.status_code == 200 and r.json()["count"] == 1

    # 该志愿者签到后再补发，就没人需要提醒了
    from backend.models import CheckinRecord, CheckinTask
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       mode="group", token=new_checkin_token(),
                       valid_from=g.start_time, valid_to=g.end_time,
                       created_by="system")
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, user_id=u.id, method="manual"))
    db.commit()
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-reminder", headers=admin)
    assert r.status_code == 400
    assert r.json()["detail"] == "全场次没有需要提醒的志愿者"


def test_值班交接提醒每周一次_按接班的星期几(db, monkeypatch, extra_core):
    from datetime import datetime
    from backend.models import Department, DutyHandover, Notification, uid
    dept = db.query(Department).filter(Department.name == "对外联络部").one()
    # 用现建的一位骨干：别人留下的交接流水不会混进这一轮的「接班时间」
    dept.duty_user_id = extra_core.id
    dept.duty_remind_on = None
    # 这一轮值班从 2026-10-14（周三）10:00 接班
    row = DutyHandover(id=uid("dty"), dept_id=dept.id, to_user_id=extra_core.id,
                       actor_id=extra_core.id,
                       created_at=datetime(2026, 10, 14, 10, 0))
    db.add(row)
    db.commit()
    try:
        # 下周二 21:30 还没到第 8 天，不触发
        monkeypatch.setattr(scheduled, "now", lambda: datetime(2026, 10, 20, 21, 30))
        assert scheduled.send_duty_handover_reminders(db) == 0
        # 下周三 20:30 不到 21 点
        monkeypatch.setattr(scheduled, "now", lambda: datetime(2026, 10, 21, 20, 30))
        assert scheduled.send_duty_handover_reminders(db) == 0
        # 下周三 21:05 触发一次，且同一天不重复发
        monkeypatch.setattr(scheduled, "now", lambda: datetime(2026, 10, 21, 21, 5))
        assert scheduled.send_duty_handover_reminders(db) >= 1
        assert scheduled.send_duty_handover_reminders(db) == 0
        ntf = (db.query(Notification)
               .filter(Notification.user_id == extra_core.id,
                       Notification.title == f"值班交接提醒｜{dept.name}")
               .first())
        assert ntf is not None and "值班交接" in ntf.content
        # 再下一周的周三 21:05 还会提醒
        monkeypatch.setattr(scheduled, "now", lambda: datetime(2026, 10, 28, 21, 5))
        assert scheduled.send_duty_handover_reminders(db) >= 1
    finally:
        db.delete(row)
        dept.duty_user_id = None
        dept.duty_remind_on = None
        db.commit()

