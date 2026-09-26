"""报名闸门：名额、候补、时段冲突、临期退选占位、并发抢报。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

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


def test_扣留期间本人不能把位子拿回去(client, admin, db, open_group, take_student):
    """报名若被老师重新打开，扣留的约束仍然对本人有效。"""
    act, g = open_group(1, day_offset=0, start_in_hours=5)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g).json()["enrollment_id"]
    db.get(Activity, act.id).reg_end = now() - timedelta(hours=1)
    db.commit()
    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=h).json()["effect"] == "held"

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
    """20 人同时抢 5 个名额：确认的必须正好 5 个，其余的都是明确的「满了」。

    输家只看状态码的话，500 或锁等超时的 503 也会被当成「没抢到」混过去。
    """
    act, g = open_group(5, waitlist_quota=0)
    pid = g.test_position_id
    students = [take_student() for _ in range(20)]

    def grab(sid):
        with TestClient(app) as c:
            r = c.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       json={"position_id": pid}, headers=login(c, sid))
            return r.status_code, r.json()

    with ThreadPoolExecutor(max_workers=20) as pool:
        results = list(pool.map(grab, students))

    won = [body for code, body in results if code == 200]
    lost = [(code, body) for code, body in results if code != 200]
    assert [b["status"] for b in won] == ["enrolled"] * 5
    assert len(lost) == 15
    for code, body in lost:
        assert code == 409 and "名额已满" in body["detail"], (code, body)
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


def test_quota_zero_is_rejected(client, admin, open_group):
    """0 人的岗位没有意义：以前会被当成「不限人数」。"""
    act, g = open_group(5)
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "零名额", "quota": 0, **POSITION_TEXT})
        assert r.status_code == 422
        assert [e["loc"][-1] for e in r.json()["detail"]] == ["quota"]
        # 对照：同一个请求只把名额改成 1 就能建，拦下的确实是 0 这个数
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "一个名额", "quota": 1, **POSITION_TEXT})
        assert r.status_code == 200, r.text


def test_assign_shift_requires_capacity(client, admin, open_group):
    """截止后统一分配的场次，名额只能记在场次上，必须填。"""
    act, _g = open_group(5)
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                        json={"name": "待分配场次", "mode": "assign", "capacity": 0,
                              "start_time": (now() + timedelta(days=401)).isoformat(),
                              "end_time": (now() + timedelta(days=401, hours=2)).isoformat()})
        assert r.status_code == 400 and "人数上限" in r.json()["detail"]


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


def test_连点两下退选只退一次(client, admin, db, open_group, take_student):
    """连点两下退选：两个请求都在锁外读到「已报名」，第二个拿到锁时手里的
    记录已经旧了。锁里不重读的话，它会照着旧状态再退一遍——多记一条退选
    流水、再跑一轮递补，两下都回「退选成功」。

    两个请求真的并发打过去，而不是手工造一个旧会话去调路由函数：锁外那段
    只有一次查库，锁里要落库、递补、发通知，第二下几乎总是在第一下持锁期间
    读完旧状态、排到锁后面，正好落进这个窗口。
    """
    import threading

    act, g = open_group(1)
    h = login(client, take_student())
    enr_id = enroll(client, h, act, g).json()["enrollment_id"]
    queued = enroll(client, login(client, take_student()), act, g).json()
    assert queued["status"] == "waitlisted"

    both_ready = threading.Barrier(2)

    def cancel(_):
        with TestClient(app) as c:
            both_ready.wait()
            return c.delete(f"/api/activities/enrollments/{enr_id}",
                            headers=h).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        codes = sorted(pool.map(cancel, range(2)))

    assert codes == [200, 400]
    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    assert [c["action"] for c in roster["roster_changes"]].count("cancel") == 1
    db.expire_all()
    assert db.get(Enrollment, queued["enrollment_id"]).status == "pending_confirm"
