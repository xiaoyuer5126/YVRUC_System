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


def _make_leader(client, admin, db, *positions):
    """组长不再自动产生：测试里由管理者指定第一个有意愿的人。"""
    head = next(r for r in _enrolled(db, *positions) if r.leader_willing)
    assert _appoint(client, admin, head.id).status_code == 200
    db.expire_all()
    return db.get(Enrollment, head.id)


def _leaders(db, *positions):
    return (db.query(Enrollment)
            .filter(Enrollment.position_id.in_([p.id for p in positions]),
                    Enrollment.is_leader.is_(True),
                    Enrollment.status == "enrolled").all())


def _appoint(client, admin, enr_id, on=True):
    return client.post(f"/api/staff/enrollments/{enr_id}/leader",
                       headers=admin, json={"is_leader": on})


def _enrolled(db, *positions):
    return (db.query(Enrollment)
            .filter(Enrollment.position_id.in_([p.id for p in positions]),
                    Enrollment.status == "enrolled")
            .order_by(Enrollment.joined_at).all())


def test_报名不会自动产生组长(client, admin, db, enrolled_activity, take_student):
    """组长只由管理者指定。四个人都勾了愿意，也一个都不自动上。"""
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    assert _leaders(db, p1, p2) == []
    # 意愿保留着，给管理者看（_group_of 里四个人都勾了）
    assert sum(r.leader_willing for r in _enrolled(db, p1, p2)) == 4


def test_管理者指定组长且同组只有一个(client, admin, db, enrolled_activity,
                                    take_student):
    """组长可以来自组里任一岗位；指定新人会摘掉原组长。"""
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    rows = _enrolled(db, p1, p2)
    a = next(r for r in rows if r.position_id == p1.id)
    b = next(r for r in rows if r.position_id == p2.id)

    assert _appoint(client, admin, a.id).status_code == 200
    db.expire_all()
    assert [x.id for x in _leaders(db, p1, p2)] == [a.id]

    assert _appoint(client, admin, b.id).status_code == 200
    db.expire_all()
    leaders = _leaders(db, p1, p2)
    assert len(leaders) == 1 and leaders[0].id == b.id


def test_组长退选后不自动补位而是提醒管理者(client, admin, db,
                                          enrolled_activity, take_student):
    from backend.models import Notification, User

    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    leader = _enrolled(db, p1, p2)[0]
    assert _appoint(client, admin, leader.id).status_code == 200
    u = db.get(User, leader.user_id)
    t = login(client, u.student_id)
    assert client.delete(f"/api/activities/enrollments/{leader.id}",
                         headers=t).status_code == 200
    db.expire_all()
    assert _leaders(db, p1, p2) == [], "不该有人自动顶上"
    admin_user = db.query(User).filter(User.student_id == "2025001").one()
    n = (db.query(Notification)
         .filter(Notification.user_id == admin_user.id,
                 Notification.title.like("[组长空缺]%")).count())
    assert n >= 1, "管理者要收到重新指定的提醒"


def test_取消组长身份不自动补位(client, admin, db, enrolled_activity,
                              take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    a = next(r for r in _enrolled(db, p1, p2) if r.leader_willing)
    _appoint(client, admin, a.id)
    assert _appoint(client, admin, a.id, on=False).status_code == 200
    db.expire_all()
    assert _leaders(db, p1, p2) == []
    # 意愿不动：取消组长不等于他不想当了，管理者改主意可以再指回来
    assert db.get(Enrollment, a.id).leader_willing is True


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
    x = _enrolled(db, p1, p2)[0]
    y = _enrolled(db, p3)[0]
    _appoint(client, admin, x.id)
    _appoint(client, admin, y.id)
    db.expire_all()
    assert len(_leaders(db, p1, p2)) == 1
    assert len(_leaders(db, p3)) == 1


def test_岗位详情展示同组岗位(client, admin, db, enrolled_activity, take_student):
    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    _make_leader(client, admin, db, p1, p2)
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    pos = {p["name"]: p for p in d["shifts"][0]["positions"]}
    assert set(pos[p1.name]["group_positions"]) == {p1.name, p2.name}
    assert pos[p1.name]["leader_name"]      # 管理者看得到
    # 组里任一岗位问到的都是同一个组长
    assert pos[p1.name]["leader_name"] == pos[p2.name]["leader_name"]


def test_组长可以看组员联系方式(client, admin, db, enrolled_activity,
                              take_student):
    """组长要在现场找得到人；这是把手机号给另一个同学，所以每次都留痕。"""
    from backend.models import AuditLog, User

    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    leader = _make_leader(client, admin, db, p1, p2)
    who = db.get(User, leader.user_id)
    h = login(client, who.student_id)

    r = client.get(f"/api/me/enrollments/{leader.id}/team", headers=h)
    assert r.status_code == 200, r.text
    d = r.json()
    # 同组两个岗位的人都在，且带着联系方式
    assert {m["position"] for m in d["members"]} == {p1.name, p2.name}
    expected = (db.query(Enrollment)
                .filter(Enrollment.position_id.in_([p1.id, p2.id]),
                        Enrollment.status == "enrolled").count())
    assert len(d["members"]) == expected
    assert next(m for m in d["members"] if m["is_me"])["is_leader"] is True
    assert all("phone" in m and "wechat" in m for m in d["members"])

    logs = db.query(AuditLog).filter(AuditLog.action == "view_team",
                                     AuditLog.activity_id == act.id).all()
    assert len(logs) == 1 and not logs[0].visible      # 志愿者侧看不到这条


def test_非组长不能看组员联系方式(client, admin, db, enrolled_activity,
                                take_student):
    from backend.models import User

    act, shift, p1, p2 = _group_of(client, admin, db, enrolled_activity,
                                   take_student)
    leader = _make_leader(client, admin, db, p1, p2)
    plain = (db.query(Enrollment)
             .filter(Enrollment.position_id.in_([p1.id, p2.id]),
                     Enrollment.is_leader.is_(False),
                     Enrollment.status == "enrolled").first())
    h = login(client, db.get(User, plain.user_id).student_id)

    assert client.get(f"/api/me/enrollments/{plain.id}/team",
                      headers=h).status_code == 403
    # 也不能拿组长的报名 id 去查
    assert client.get(f"/api/me/enrollments/{leader.id}/team",
                      headers=h).status_code == 404
