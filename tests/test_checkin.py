"""签到闭环：建码、扫码签到、再扫签退、非本场次拒绝。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import config
from backend.models import (Activity, ActivityManager, CheckinTask, Enrollment,
                            Position, Shift, User, now, uid)
from conftest import ADMIN, login, student_row


@pytest.fixture
def live_group(client, db, admin, take_student):
    """一个正在进行的场次 + 一名已确认的志愿者。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"签到测试 {uid('t')}",
                   intro="x", location="集合点",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
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
    db.flush()

    # 直接落库而不是走报名接口：场次已经开场了，接口会（正确地）拒绝。
    # 现实里这个人是昨天报的名，今天来签到。
    sid = take_student()
    h = login(client, sid)
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=student_row(db, sid).id, position_id=pos.id,
                      status="enrolled", joined_at=now() - timedelta(days=1)))
    db.commit()
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


# ======================= 多场次：码属于哪一场要看得出来 =======================

@pytest.fixture
def two_shift_act(client, db, admin):
    """一个活动 + 上午场（带岗位）+ 挂在它下面的赛前培训。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"多场次签到 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=1), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    am = Shift(id=uid("g"), activity_id=act.id, name="上午场",
               start_time=now() + timedelta(days=2),
               end_time=now() + timedelta(days=2, hours=4), mode="pick", sort=0)
    db.add(am)
    db.flush()
    db.add(Position(id=uid("p"), shift_id=am.id, name="引导", quota=5, sort=0))
    train = Shift(id=uid("g"), activity_id=act.id, name="赛前培训",
                  kind="training", parent_id=am.id,
                  start_time=now() + timedelta(days=1),
                  end_time=now() + timedelta(days=1, hours=2),
                  mode="pick", hours_mode="checkin", sort=1)
    db.add(train)
    db.commit()
    return act, am, train


def test_默认可签时段比场次起止各放宽半小时(client, admin, db, two_shift_act):
    """卡着场次时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了。"""
    _act, am, _train = two_shift_act
    tid = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks",
                      json={"mode": "shift"}, headers=admin).json()["id"]
    task = db.get(CheckinTask, tid)
    pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
    assert task.valid_from == am.start_time - pad
    assert task.valid_to == am.end_time + pad


def test_自己传的时段不会被覆盖(client, admin, db, two_shift_act):
    _act, am, _train = two_shift_act
    f = (am.start_time - timedelta(hours=3)).isoformat()
    t = (am.end_time + timedelta(hours=3)).isoformat()
    tid = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                      json={"mode": "shift", "valid_from": f,
                            "valid_to": t}).json()["id"]
    task = db.get(CheckinTask, tid)
    assert task.valid_from == am.start_time - timedelta(hours=3)
    assert task.valid_to == am.end_time + timedelta(hours=3)


def test_码表带上场次让前端分得出是哪一场(client, admin, two_shift_act):
    """列表按活动查，两场的码混在一起；没有 shift_id 就没法分辨，也没法把
    手动补签锁到当前大屏那一场上。"""
    act, am, train = two_shift_act
    client.post(f"/api/staff/shifts/{am.id}/checkin-tasks",
                json={"mode": "shift"}, headers=admin)
    client.post(f"/api/staff/shifts/{train.id}/checkin-tasks",
                json={"mode": "shift"}, headers=admin)

    rows = client.get(f"/api/staff/activities/{act.id}/checkin-tasks",
                      headers=admin).json()
    by_shift = {r["shift_id"]: r for r in rows}
    assert set(by_shift) == {am.id, train.id}
    assert by_shift[am.id]["shift_name"] == "上午场"
    assert by_shift[am.id]["shift_kind"] == "service"
    # 培训场次也能建码：hours_mode 按签到计时，签不了到就没时长
    assert by_shift[train.id]["shift_name"] == "赛前培训"
    assert by_shift[train.id]["shift_kind"] == "training"
    assert by_shift[train.id]["shift_kind_label"] == "培训"


def test_分岗位签到只认本场次的岗位(client, admin, two_shift_act):
    """换场次时前端会清空岗位选择；后端也得拦住跨场次的岗位 id。"""
    _act, am, train = two_shift_act
    pos_id = client.get(f"/api/staff/shifts/{am.id}/roster",
                        headers=admin).json()["positions"][0]["id"]
    r = client.post(f"/api/staff/shifts/{train.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_id": pos_id})
    assert r.status_code == 400 and "本场次" in r.json()["detail"]
