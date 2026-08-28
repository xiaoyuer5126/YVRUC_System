"""岗位组：同组岗位共用一名组长。"""
from __future__ import annotations

from backend.models import Enrollment, Position, uid
from conftest import POSITION_TEXT, login


def _group_of(client, admin, db, enrolled_activity, take_student, *,
              group_name="东门片区", n_each=2):
    """把已有岗位并入一个组，再加一个同组岗位，两边各报几个人。"""
    act, shift, first = enrolled_activity
    p1 = shift.positions[0]
    p1.group_name = group_name
    p1.needs_leader = True
    p2 = Position(id=uid("p"), shift_id=shift.id, name="东门检录", quota=5,
                  waitlist_quota=2, group_name=group_name, sort=1,
                  **POSITION_TEXT)
    db.add(p2)
    db.commit()

    for pos in (p1, p2):
        for _ in range(n_each):
            t = login(client, take_student())
            r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                            headers=t, json={"position_id": pos.id,
                                             "leader_willing": True})
            assert r.status_code == 200, r.text
    return act, shift, p1, p2


def _leaders(db, *positions):
    return (db.query(Enrollment)
            .filter(Enrollment.position_id.in_([p.id for p in positions]),
                    Enrollment.is_leader.is_(True),
                    Enrollment.status == "enrolled").all())


def test_同组岗位只产生一名组长(client, admin, db, enrolled_activity,
                              take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    assert len(_leaders(db, p1, p2)) == 1


def test_组长可以来自组里任一岗位(client, admin, db, enrolled_activity,
                                take_student):
    """按报名先后选，不管他报的是组里哪个岗位。"""
    act, shift, first = enrolled_activity
    p1 = shift.positions[0]
    p1.group_name = "西门片区"
    p1.needs_leader = False          # 只有第二个岗位标了需要组长
    p2 = Position(id=uid("p"), shift_id=shift.id, name="西门检录", quota=5,
                  group_name="西门片区", needs_leader=True, sort=1,
                  **POSITION_TEXT)
    db.add(p2)
    db.commit()

    # 先报 p1 的人，后报 p2 的人 → 组长应是先报的那个（尽管他在 p1）
    t1 = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                    headers=t1, json={"position_id": p1.id,
                                      "leader_willing": True})
    assert r.status_code == 200
    t2 = login(client, take_student())
    client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                headers=t2, json={"position_id": p2.id, "leader_willing": True})

    leaders = _leaders(db, p1, p2)
    assert len(leaders) == 1
    assert leaders[0].position_id == p1.id


def test_改判组长会摘掉同组的原组长(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    old = _leaders(db, p1, p2)[0]
    other = (db.query(Enrollment)
             .filter(Enrollment.position_id == p2.id,
                     Enrollment.is_leader.is_(False),
                     Enrollment.status == "enrolled").first())
    r = client.post(f"/api/staff/enrollments/{other.id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 200
    db.expire_all()
    leaders = _leaders(db, p1, p2)
    assert len(leaders) == 1 and leaders[0].id == other.id
    assert leaders[0].id != old.id


def test_组长退选后由组内下一位接任(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    leader = _leaders(db, p1, p2)[0]
    from backend.models import User
    u = db.get(User, leader.user_id)
    t = login(client, u.student_id)
    assert client.delete(f"/api/activities/enrollments/{leader.id}",
                         headers=t).status_code == 200
    db.expire_all()
    leaders = _leaders(db, p1, p2)
    assert len(leaders) == 1 and leaders[0].id != leader.id


def test_不同组各有各的组长(client, admin, db, enrolled_activity, take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    p3 = Position(id=uid("p"), shift_id=shift.id, name="南门引导", quota=5,
                  group_name="南门片区", needs_leader=True, sort=2,
                  **POSITION_TEXT)
    db.add(p3)
    db.commit()
    t = login(client, take_student())
    client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                headers=t, json={"position_id": p3.id, "leader_willing": True})
    assert len(_leaders(db, p1, p2)) == 1
    assert len(_leaders(db, p3)) == 1


def test_岗位详情展示同组岗位(client, admin, db, enrolled_activity, take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    pos = {p["name"]: p for p in d["shifts"][0]["positions"]}
    assert set(pos[p1.name]["group_positions"]) == {p1.name, p2.name}
    assert pos[p1.name]["leader_name"]      # 管理者看得到
    # 组里任一岗位问到的都是同一个组长
    assert pos[p1.name]["leader_name"] == pos[p2.name]["leader_name"]
