"""时长申诉：志愿者提异议，管理者采纳或驳回，与确认/回传的关系。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, HoursRecord, Notification,
                            Position, Shift, User, now, uid)
from conftest import ADMIN, POSITION_TEXT, login, student_row


@pytest.fixture
def served(db, take_student):
    """一个已结束的活动 + 一名签到签退过的志愿者（4 小时）。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"申诉测试 {uid('t')}",
                   intro="x", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="finished", reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    start = now().replace(microsecond=0) - timedelta(days=2)
    g = Shift(id=uid("g"), activity_id=act.id, name="服务场",
              start_time=start, end_time=start + timedelta(hours=4),
              mode="pick", sort=0)
    db.add(g)
    db.flush()
    pos = Position(id=uid("p"), shift_id=g.id, name="引导", quota=5, sort=0,
                   **POSITION_TEXT)
    db.add(pos)
    db.flush()
    u = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, position_id=pos.id, status="enrolled",
                      joined_at=now()))
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       mode="shift", token=uid("T")[:6].upper(),
                       created_by=admin.id,
                       valid_from=g.start_time, valid_to=g.end_time)
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, position_id=pos.id, user_id=u.id,
                         method="manual", checkin_at=g.start_time,
                         checkout_at=g.end_time))
    db.commit()
    return act, g, u


def _confirm(client, admin, act, *, approve=True):
    """算出时长；默认接着核准，志愿者这才看得到、也才能申诉。"""
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    if approve:
        r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                        headers=admin, json={})
        assert r.status_code == 200, r.text


def _record(client, h):
    return client.get("/api/me/hours", headers=h).json()["records"][0]


def test_申诉全流程_采纳(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["hours"] == 4.0 and rec["can_appeal"]

    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "忘记签退，实际 15:30 就走了"})
    assert r.status_code == 200, r.text

    rows = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                      headers=admin).json()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["old_hours"] == 4.0 and rows[0]["claimed_hours"] == 3.0

    r = client.post(f"/api/staff/hours-appeals/{rows[0]['id']}/resolve",
                    headers=admin, json={"accept": True, "reply": "已按实际核减"})
    assert r.status_code == 200 and r.json()["hours"] == 3.0

    after = _record(client, stu)
    assert after["hours"] == 3.0
    assert after["appeal"]["status"] == "accepted" and not after["can_appeal"]


def test_驳回后可以再提一次(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 8, "reason": "我觉得应该算八小时"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": False, "reply": "签到记录显示 4 小时"})

    after = _record(client, stu)
    assert after["hours"] == 4.0 and after["appeal"]["status"] == "rejected"
    assert after["can_appeal"]        # 驳回了还能再说一次
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json={"claimed_hours": 5,
                             "reason": "补充：结束后还留下收拾了一小时"}
                       ).status_code == 200


def test_同时只能有一条待处理(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    body = {"claimed_hours": 3, "reason": "忘记签退了，实际提前走的"}
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json=body).status_code == 200
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json=body).status_code == 409


def test_理由太短会被挡住(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "不对"})
    assert r.status_code == 400


def test_不能申诉别人的时长(client, admin, db, served, take_student):
    act, _g, _u = served
    _confirm(client, admin, act)
    other = login(client, take_student())
    rec = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).one()
    r = client.post(f"/api/me/hours/{rec.id}/appeal", headers=other,
                    json={"claimed_hours": 3, "reason": "这条不是我的记录"})
    assert r.status_code == 404


def test_采纳过的时长不会被一键确认推回去(client, admin, db, served):
    """改判是人判的，再点一次「确认」不该把它冲掉。"""
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": True})

    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert "保留不覆盖" in r.json()["message"]
    assert _record(client, stu)["hours"] == 3.0


def test_核准前看不到也申诉不了(client, admin, db, served):
    """确认只是把账算出来放在管理端，核准了才通知本人、才开申诉窗口。"""
    act, _g, u = served
    _confirm(client, admin, act, approve=False)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["approved"] is False and rec["can_appeal"] is False
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    assert r.status_code == 409 and "还没核准" in r.json()["detail"]

    # 核准会通知每个人，并给出申诉截止时间
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["count"] == 1
    rec = _record(client, stu)
    assert rec["approved"] and rec["can_appeal"] and rec["appeal_deadline"]
    assert (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("志愿时长已核准%")).count() == 1)


def test_到点无人申诉自动锁定并标记已完成(client, admin, db, served):
    from backend import scheduled

    act, _g, u = served
    _confirm(client, admin, act)
    db.expire_all()
    row = db.get(Activity, act.id)
    row.appeal_deadline = now() - timedelta(minutes=1)
    db.commit()

    assert scheduled.lock_hours_when_due(db) >= 1
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.hours_locked_at is not None and row.status == "completed"
    assert (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("志愿时长已锁定%")).count() == 1)

    # 锁定之后：志愿者申诉不了，管理者也改不动
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["locked"] and not rec["can_appeal"]
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json={"claimed_hours": 3, "reason": "忘记签退了，实际提前走"}
                       ).status_code == 409
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 409 and "已于" in r.json()["detail"]


def test_有人申诉就不自动锁定(client, admin, db, served):
    from backend import scheduled

    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    db.expire_all()
    db.get(Activity, act.id).appeal_deadline = now() - timedelta(minutes=1)
    db.commit()

    scheduled.lock_hours_when_due(db)
    db.expire_all()
    assert db.get(Activity, act.id).hours_locked_at is None

    # 处理完申诉，管理者也可以直接锁定，不必再等一轮
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": True})
    r = client.post(f"/api/staff/activities/{act.id}/hours/lock", headers=admin,
                    json={"note": "已按申诉改判"})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).status == "completed"


def test_锁定后改时长要报审批(client, admin, db, served):
    act, _g, _u = served
    _confirm(client, admin, act)
    client.post(f"/api/staff/activities/{act.id}/hours/lock", headers=admin,
                json={})
    # 理由太短拦下
    assert client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                       headers=admin, json={"reason": "改"}).status_code == 400
    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏算了两位同学的加班时长"})
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    # 一次只能挂一条
    assert client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                       headers=admin,
                       json={"reason": "再来一条应当被拦下"}).status_code == 409

    rows = client.get("/api/staff/hours-unlocks", headers=admin).json()
    assert [x["id"] for x in rows] == [req_id]
    r = client.post(f"/api/staff/hours-unlocks/{req_id}/decide", headers=admin,
                    json={"approve": True, "reply": "同意，改完重新核准"})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.hours_locked_at is None and row.status == "finished"
    # 解锁之后又能改了
    assert client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin, json={}).status_code == 200


def test_没有时长记录的活动核准即锁定(client, admin, db, served):
    """一条时长都没有的活动没什么可申诉的，直接结账。"""
    act, _g, _u = served
    db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).delete()
    db.commit()
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["locked"] is True
    db.expire_all()
    assert db.get(Activity, act.id).status == "completed"


def test_非本活动管理者不能处理申诉(client, admin, initiator, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    assert client.post(f"/api/staff/hours-appeals/{a['id']}/resolve",
                       headers=initiator, json={"accept": True}).status_code == 403
