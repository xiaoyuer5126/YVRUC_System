"""报名闸门：名额、候补、时段冲突、临近退选占位、并发抢报。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.database import SessionLocal
from backend.main import app
from backend.models import (Activity, ActivityManager, Enrollment, Position,
                            Shift, User, now, uid)
from conftest import ADMIN, editing, login, student_row


@pytest.fixture
def open_group(db):
    """一个报名中的活动 + 一个空场次，名额由用例自己定。

    每次调用把场次往后挪一天：用例之间的场次不能重叠，否则同一个学生
    在别处占的时段会把这里的报名挡掉。需要重叠的用例自己指定 day_offset。
    """
    seq = iter(range(30, 300))

    def _make(capacity: int, *, mode: str = "pick", waitlist_quota: int = 3,
              day_offset: int | None = None, start_in_hours: float = 4,
              requirement_tags: dict | None = None, needs_leader: bool = False):
        start = timedelta(days=day_offset if day_offset is not None
                          else next(seq), hours=start_in_hours)
        admin = db.query(User).filter(User.student_id == ADMIN).one()
        act = Activity(id=uid("act"), title=f"测试活动 {uid('t')}", type="A",
                       intro="用例生成", location="测试地点",
                       contacts=[{"name": "测试", "phone": "13900000000"}],
                       qa_group_qr="x", status="published",
                       reg_start=now() - timedelta(days=1),
                       reg_end=now() + timedelta(days=365),
                       created_by=admin.id)
        db.add(act)
        db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                               user_id=admin.id, added_by=admin.id))
        g = Shift(id=uid("g"), activity_id=act.id, name="测试场次",
                  start_time=now() + start,
                  end_time=now() + start + timedelta(hours=3),
                  capacity=capacity, mode=mode,
                  waitlist_quota=waitlist_quota, sort=0)
        db.add(g)
        db.flush()
        # 名额在岗位上，所以每个测试场次都带一个岗位；assign 模式的场次
        # 报名时不选岗位，名额看场次自己的 capacity。
        p = Position(id=uid("p"), shift_id=g.id, name="测试岗位",
                     location="测试地点", quota=capacity,
                     waitlist_quota=waitlist_quota,
                     requirement_tags=requirement_tags or {},
                     needs_leader=needs_leader, sort=0)
        db.add(p)
        db.commit()
        g.test_position_id = p.id
        return act, g
    return _make


def enroll(client, headers, act, g, **body):
    """pick 模式必须带岗位；用例没指定就用场次自带的那个测试岗位。"""
    if "position_id" not in body and not g.needs_assign():
        body["position_id"] = g.test_position_id
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       json=body, headers=headers)


def test_full_group_falls_back_to_waitlist(client, open_group, take_student):
    act, g = open_group(1)
    first, second = take_student(), take_student()

    assert enroll(client, login(client, first), act, g
                  ).json()["status"] == "confirmed"
    assert enroll(client, login(client, second), act, g
                  ).json()["status"] == "waitlisted"


def test_duplicate_enroll_rejected(client, open_group, take_student):
    act, g = open_group(5)
    h = login(client, take_student())
    assert enroll(client, h, act, g).status_code == 200
    assert enroll(client, h, act, g).status_code == 409


def test_time_conflict_blocks_enroll(client, open_group, take_student):
    """两个场次时间重叠，第二个报不了。"""
    act1, g1 = open_group(5, day_offset=200, start_in_hours=4)
    act2, g2 = open_group(5, day_offset=200, start_in_hours=6)   # 与 g1 重叠
    h = login(client, take_student())

    assert enroll(client, h, act1, g1).status_code == 200
    r = enroll(client, h, act2, g2)
    assert r.status_code == 409
    assert "冲突" in r.json()["detail"]["message"]


def test_waitlisted_group_also_locks_the_slot(client, open_group, take_student):
    """排了队就等于预留了这段时间，同时段的别的活动报不了。"""
    act1, g1 = open_group(1, day_offset=210)
    act2, g2 = open_group(5, day_offset=210)
    holder, queued = take_student(), take_student()

    enroll(client, login(client, holder), act1, g1)              # 占满 g1
    h = login(client, queued)
    assert enroll(client, h, act1, g1).json()["status"] == "waitlisted"
    assert enroll(client, h, act2, g2).status_code == 409


def test_waitlist_ban_does_not_block_normal_enroll(client, db, open_group,
                                                   take_student):
    """连续放弃候补惩罚的是排队权，有空位时照样能正常报名。"""
    act, g = open_group(5)
    student = student_row(db, take_student())
    student.waitlist_banned_until = now() + timedelta(days=30)
    db.commit()

    r = enroll(client, login(client, student.student_id), act, g)
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


def test_waitlist_ban_blocks_queueing(client, db, open_group, take_student):
    act, g = open_group(1)
    enroll(client, login(client, take_student()), act, g)        # 先占满

    banned = student_row(db, take_student())
    banned.waitlist_banned_until = now() + timedelta(days=30)
    db.commit()

    r = enroll(client, login(client, banned.student_id), act, g)
    assert r.status_code == 403 and "候补资格" in r.json()["detail"]


def test_late_cancel_holds_the_seat(client, db, open_group, take_student):
    """开始前 24 小时内退选：扣分，名额扣留——别人补不进来，本人也回不去。"""
    act, g = open_group(1, day_offset=0, start_in_hours=5)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g).json()["enrollment_id"]

    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=h).json()["effect"] == "penalized"
    db.expunge_all()
    assert db.get(Enrollment, enr_id).seat_held is True

    # 名额没有回到池子里
    r = enroll(client, login(client, take_student()), act, g)
    assert r.json()["status"] == "waitlisted"
    # 本人也不能再报一次把位子拿回去
    assert enroll(client, h, act, g).status_code == 409

    group = client.get(f"/api/activities/{act.id}", headers=h).json()["shifts"][0]
    assert group["remaining"] == 0   # 占位不释放，名额没回到池子里


def test_normal_cancel_promotes_waitlist_head(client, db, open_group,
                                              take_student):
    act, g = open_group(1)
    h1 = login(client, take_student())
    h2 = login(client, take_student())

    enr_id = enroll(client, h1, act, g).json()["enrollment_id"]
    wl_id = enroll(client, h2, act, g).json()["enrollment_id"]

    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=h1).json()["effect"] == "released"
    db.expunge_all()
    assert db.get(Enrollment, wl_id).status == "pending_confirm"
    assert client.post(f"/api/activities/enrollments/{wl_id}/confirm-promotion",
                       headers=h2).status_code == 200


def test_concurrent_enroll_does_not_oversell(open_group, take_student):
    """20 人同时抢 5 个名额：确认的必须正好 5 个。"""
    act, g = open_group(5, waitlist_quota=0)
    pid = g.test_position_id
    students = [take_student() for _ in range(20)]

    def grab(sid):
        with TestClient(app) as c:
            r = c.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                           json={"position_id": pid}, headers=login(c, sid))
            return r.json().get("status") if r.status_code == 200 else None

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(grab, students))

    assert results.count("confirmed") == 5
    check = SessionLocal()
    try:
        assert check.query(Enrollment).filter(
            Enrollment.shift_id == g.id,
            Enrollment.status == "confirmed").count() == 5
    finally:
        check.close()


def test_allow_reassign_recorded(client, open_group, take_student):
    """报名时的调剂意愿要能回读，也要能改。"""
    act, g = open_group(5)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g,
                    allow_reassign=False).json()["enrollment_id"]

    mine = client.get("/api/me/enrollments", headers=h).json()
    row = next(r for r in mine if r["id"] == enr_id)
    assert row["allow_reassign"] is False

    client.patch(f"/api/activities/enrollments/{enr_id}/reassign-pref",
                 json={"allow_reassign": True}, headers=h)
    group = client.get(f"/api/activities/{act.id}", headers=h).json()["shifts"][0]
    assert group["my_enrollment"]["allow_reassign"] is True


def test_quota_zero_is_rejected(client, admin):
    """0 人的岗位没有意义：以前会被当成「不限人数」。"""
    target = client.get("/api/staff/activities", headers=admin).json()[0]["id"]
    with editing(client, admin, target):
        r = client.post(f"/api/staff/activities/{target}/shifts", headers=admin,
                        json={"name": "测试场次", "mode": "pick",
                              "start_time": (now() + timedelta(days=400)).isoformat(),
                              "end_time": (now() + timedelta(days=400, hours=2)).isoformat()})
        assert r.status_code == 200
        shift_id = r.json()["id"]
        r = client.post(f"/api/staff/shifts/{shift_id}/positions", headers=admin,
                        json={"name": "零名额", "quota": 0})
        assert r.status_code == 422


def test_assign_shift_requires_capacity(client, admin):
    """截止后统一分配的场次，名额只能记在场次上，必须填。"""
    target = client.get("/api/staff/activities", headers=admin).json()[0]["id"]
    with editing(client, admin, target):
        r = client.post(f"/api/staff/activities/{target}/shifts", headers=admin,
                        json={"name": "待分配场次", "mode": "assign", "capacity": 0,
                              "start_time": (now() + timedelta(days=401)).isoformat(),
                              "end_time": (now() + timedelta(days=401, hours=2)).isoformat()})
        assert r.status_code == 400


def test_候补名次按岗位算而不是按场次(client, db, open_group, take_student):
    """排在自己岗位第一位的人，不该因为别的岗位排了人就被说成第三位。"""
    act, g = open_group(1)
    p1_id = g.test_position_id
    p2 = Position(id=uid("p"), shift_id=g.id, name="第二岗位",
                  quota=1, waitlist_quota=3, sort=1)
    db.add(p2)
    db.commit()
    p2_id = p2.id

    def go(position_id):
        t = login(client, take_student())
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=t, json={"position_id": position_id})
        assert r.status_code == 200, r.text
        return r.json()

    assert go(p1_id)["status"] == "confirmed"      # 占满 p1
    assert go(p2_id)["status"] == "confirmed"      # 占满 p2
    go(p1_id); go(p1_id)                            # p1 排两个候补

    third = go(p2_id)                               # p2 的第一个候补
    assert third["status"] == "waitlisted"
    assert third["waitlist_position"] == 1
