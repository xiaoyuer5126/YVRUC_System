"""定时任务：活动状态自动流转、签到提醒。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import scheduled
from backend.models import (Activity, ActivityManager, Enrollment,
                            Notification, Shift, User, now, uid)
from conftest import ADMIN


@pytest.fixture
def make_activity(db):
    """按指定的报名截止/场次时间造一个活动，用于驱动状态流转。"""
    def _make(status: str, *, reg_end_in_hours: float, start_in_hours: float,
              duration_hours: float = 3):
        admin = db.query(User).filter(User.student_id == ADMIN).one()
        act = Activity(id=uid("act"), title=f"流转测试 {uid('t')}", type="A",
                       intro="x", location="x",
                       contacts=[{"name": "测试", "phone": "13900000000"}],
                       qa_group_qr="x", status=status,
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
                          capacity=5, mode="single", sort=0)
        db.add(g)
        db.commit()
        return act, g
    return _make


def test_registration_deadline_closes_the_activity(db, make_activity):
    act, _g = make_activity("published", reg_end_in_hours=-1, start_in_hours=48)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == "closed"


def test_finished_sessions_finish_the_activity(db, make_activity):
    act, _g = make_activity("closed", reg_end_in_hours=-72, start_in_hours=-10)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == "finished"


def test_one_pass_settles_published_to_finished(db, make_activity):
    """报名早就截止、场次也早就结束的活动，一轮就该走完两步。"""
    act, _g = make_activity("published", reg_end_in_hours=-72,
                            start_in_hours=-10)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == "finished"


def test_draft_never_advances(db, make_activity):
    act, _g = make_activity("draft", reg_end_in_hours=-72, start_in_hours=-10)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == "draft"


def test_未到时间的活动不动(db, make_activity):
    act, _g = make_activity("published", reg_end_in_hours=24, start_in_hours=48)
    scheduled.auto_advance_activities(db)
    db.refresh(act)
    assert act.status == "published"


def _enroll(db, act, g, student_id):
    u = db.query(User).filter(User.student_id == student_id).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, status="enrolled", joined_at=now()))
    db.commit()
    return u


def _reminders_of(db, user_id):
    return (db.query(Notification)
            .filter(Notification.user_id == user_id,
                    Notification.template_key == "checkin_reminder").count())


def test_reminder_fires_once_before_start(db, make_activity, take_student):
    """开始前 30 分钟内推一次，且只推一次。"""
    act, g = make_activity("closed", reg_end_in_hours=-48, start_in_hours=0.4)
    u = _enroll(db, act, g, take_student())

    assert scheduled.send_checkin_reminders(db) == 1
    assert _reminders_of(db, u.id) == 1

    scheduled.send_checkin_reminders(db)          # 再跑一轮不该重复发
    assert _reminders_of(db, u.id) == 1
    db.refresh(g)
    assert g.reminder_sent_at is not None


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
                       mode="group", token=uid("T")[:6].upper(),
                       valid_from=g.start_time, valid_to=g.end_time,
                       created_by="system")
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, user_id=u.id, method="manual"))
    db.commit()
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-reminder", headers=admin)
    assert r.status_code == 400
