"""报名闸门：名额、候补、时段冲突、临期退选占位、并发抢报。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend.database import SessionLocal
from backend.main import app
from backend.models import Activity, Enrollment, Position, now, uid
from conftest import POSITION_TEXT, editing, login, student_row


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
                  ).json()["status"] == "enrolled"
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


def test_late_cancel_holds_the_seat(client, admin, db, open_group, take_student):
    """开始前 24 小时内 + 报名已截止：名额被扣留占位。

    报名还开着时只记临期退选、不扣留（见 test_seat_hold.py）：平台仍在招人，
    这时把位子废掉，等于新人能报、候补能转正，唯独这个位子谁都不能用。
    """
    act, g = open_group(1, day_offset=0, start_in_hours=5)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g).json()["enrollment_id"]

    db.get(Activity, act.id).reg_end = now() - timedelta(hours=1)
    db.commit()

    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=h).json()["effect"] == "held"
    db.expunge_all()
    assert db.get(Enrollment, enr_id).seat_held is True

    # 位子仍被占着：确认人数掉到 0，但剩余名额还是 0
    shift = client.get(f"/api/activities/{act.id}", headers=h).json()["shifts"][0]
    assert shift["enrolled"] == 0
    assert shift["remaining"] == 0
    assert shift["held_seats"] == 1

    # 老师能在名单里找到它，并放回去
    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    assert [x["enrollment_id"] for x in roster["held"]] == [enr_id]
    assert client.post(f"/api/staff/enrollments/{enr_id}/release-seat",
                       headers=admin).status_code == 200
    shift = client.get(f"/api/activities/{act.id}", headers=h).json()["shifts"][0]
    assert shift["remaining"] == 1


def test_扣留期间本人不能把位子拿回去(client, admin, db, open_group, take_student):
    """报名若被老师重新打开，扣留的约束仍然对本人有效。"""
    act, g = open_group(1, day_offset=0, start_in_hours=5)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g).json()["enrollment_id"]
    db.get(Activity, act.id).reg_end = now() - timedelta(hours=1)
    db.commit()
    client.delete(f"/api/activities/enrollments/{enr_id}", headers=h)

    db.get(Activity, act.id).reg_end = now() + timedelta(days=1)   # 又开放报名
    db.commit()
    r = enroll(client, h, act, g)
    assert r.status_code == 409 and "扣留" in r.json()["detail"]


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

    assert results.count("enrolled") == 5
    check = SessionLocal()
    try:
        assert check.query(Enrollment).filter(
            Enrollment.shift_id == g.id,
            Enrollment.status == "enrolled").count() == 5
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
    target = client.get("/api/staff/activities", headers=admin).json()["items"][0]["id"]
    with editing(client, admin, target):
        r = client.post(f"/api/staff/activities/{target}/shifts", headers=admin,
                        json={"name": "测试场次", "mode": "pick",
                              "start_time": (now() + timedelta(days=400)).isoformat(),
                              "end_time": (now() + timedelta(days=400, hours=2)).isoformat()})
        assert r.status_code == 200
        shift_id = r.json()["id"]
        r = client.post(f"/api/staff/shifts/{shift_id}/positions", headers=admin,
                        json={"name": "零名额", "quota": 0, **POSITION_TEXT})
        assert r.status_code == 422


def test_assign_shift_requires_capacity(client, admin):
    """截止后统一分配的场次，名额只能记在场次上，必须填。"""
    target = client.get("/api/staff/activities", headers=admin).json()["items"][0]["id"]
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
                  quota=1, waitlist_quota=3, sort=1, **POSITION_TEXT)
    db.add(p2)
    db.commit()
    p2_id = p2.id

    def go(position_id):
        t = login(client, take_student())
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=t, json={"position_id": position_id})
        assert r.status_code == 200, r.text
        return r.json()

    assert go(p1_id)["status"] == "enrolled"      # 占满 p1
    assert go(p2_id)["status"] == "enrolled"      # 占满 p2
    go(p1_id); go(p1_id)                            # p1 排两个候补

    third = go(p2_id)                               # p2 的第一个候补
    assert third["status"] == "waitlisted"
    assert third["waitlist_position"] == 1
