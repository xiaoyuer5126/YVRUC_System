"""签到闭环：建码、扫码签到、再扫签退、非本场次拒绝。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, Position, Shift, User,
                            now, uid)
from conftest import ADMIN, login


@pytest.fixture
def live_group(client, db, admin, take_student):
    """一个正在进行的场次 + 一名已确认的志愿者。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"签到测试 {uid('t')}", type="A",
                   intro="x", location="集合点",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   qa_group_qr="x", status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=1),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="进行中场次",
              start_time=now() - timedelta(minutes=30),
              end_time=now() + timedelta(hours=3),
              mode="pick", sort=0)
    db.add(g)
    db.flush()
    pos = Position(id=uid("p"), shift_id=g.id, name="签到测试岗位",
                   location="集合点", quota=5, sort=0)
    db.add(pos)
    db.commit()

    sid = take_student()
    h = login(client, sid)
    client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                json={"position_id": pos.id}, headers=h)
    token = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                        json={"mode": "shift"}, headers=admin).json()["token"]
    return act, g, h, token


def test_scan_twice_is_checkin_then_checkout(client, live_group):
    _act, _g, h, token = live_group

    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["state"] == "active" and info["enrolled"] is True

    first = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert first["action"] == "checkin"

    second = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert second["action"] == "checkout"

    third = client.post(f"/api/checkin/task/{token}", headers=h)
    assert third.status_code == 409          # 签完退再扫不该重复记

    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["checked_in"] and info["checked_out"]


def test_outsider_cannot_check_in(client, live_group, take_student):
    _act, _g, _h, token = live_group
    outsider = login(client, take_student())
    r = client.post(f"/api/checkin/task/{token}", headers=outsider)
    assert r.status_code == 403


def test_checkin_needs_login(client, live_group):
    _act, _g, _h, token = live_group
    assert client.post(f"/api/checkin/task/{token}").status_code == 401
