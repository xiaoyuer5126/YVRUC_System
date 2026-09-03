"""时长申诉：志愿者提异议，管理者采纳或驳回，与确认/回传的关系。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, HoursRecord, Position,
                            Shift, User, now, uid)
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


def _confirm(client, admin, act):
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
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


def test_申诉待处理时不回传这条(client, admin, db, served):
    """回传即定稿，之后改不了——不能把正在申诉的记录顺手锁死。"""
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})

    r = client.post(f"/api/staff/activities/{act.id}/hours/upload", headers=admin)
    assert r.status_code == 400 and "申诉" in r.json()["detail"]
    assert _record(client, stu)["status"] != "uploaded"


def test_已回传的不能再申诉(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    assert client.post(f"/api/staff/activities/{act.id}/hours/upload",
                       headers=admin).status_code == 200
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert not rec["can_appeal"]
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    assert r.status_code == 409


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
