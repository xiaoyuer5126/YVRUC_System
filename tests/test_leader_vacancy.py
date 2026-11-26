"""组长空缺提醒：名单锁定时统一提醒，不在打开「需要组长」时提醒。

名单敲定之前组长空着很正常（岗位刚建好、报名还没开始），那时提醒管理者
只是噪音。锁定时（自动或手动）把这一场缺组长的组合成一条提醒；不自动锁定的
场次在报名截止时兜底提醒一次。锁定前组长退选不提醒，锁定后立即提醒。
"""
from __future__ import annotations

from datetime import timedelta

from backend import scheduled
from backend.models import Activity, Enrollment, Position, User, now, uid
from conftest import ADMIN, POSITION_TEXT, editing, inbox, login, mk_shift


def _alerts(db, act):
    return inbox(db, ADMIN, f"[组长空缺] {act.title}")


def _enroll(client, act, g, sid, pid=None):
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, sid),
                    json={"position_id": pid or g.test_position_id,
                          "leader_willing": True})
    assert r.status_code == 200, r.text
    return r.json()["enrollment_id"]


def _lock(client, admin, g, locked=True):
    r = client.post(f"/api/staff/shifts/{g.id}/lock", headers=admin,
                    json={"locked": locked})
    assert r.status_code == 200, r.text


def _appoint(client, admin, enr_id):
    r = client.post(f"/api/staff/enrollments/{enr_id}/leader", headers=admin,
                    json={"is_leader": True})
    assert r.status_code == 200, r.text


def _quit(client, db, enr_id):
    u = db.get(User, db.get(Enrollment, enr_id).user_id)
    r = client.delete(f"/api/activities/enrollments/{enr_id}",
                      headers=login(client, u.student_id))
    assert r.status_code == 200, r.text


def test_打开需要组长开关不提醒(client, admin, db, open_group):
    act, g = open_group(5)
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{g.test_position_id}",
                         headers=admin,
                         json={"needs_leader": True,
                               "leader_task": "带队进场、清点人数"})
        assert r.status_code == 200, r.text
    assert _alerts(db, act) == []


def test_手动锁定时缺组长提醒一次_解锁再锁再提醒(client, admin, db, open_group,
                                               take_student):
    act, g = open_group(5, needs_leader=True)
    _enroll(client, act, g, take_student())
    _lock(client, admin, g)
    rows = _alerts(db, act)
    assert len(rows) == 1
    assert "测试场次·测试岗位" in rows[0].content
    assert rows[0].data.get("tab") == "roster"
    _lock(client, admin, g)                      # 已经锁着，再点不重复提醒
    assert len(_alerts(db, act)) == 1
    _lock(client, admin, g, locked=False)
    assert len(_alerts(db, act)) == 1            # 解锁不提醒
    _lock(client, admin, g)
    assert len(_alerts(db, act)) == 2


def test_锁定时有组长不提醒(client, admin, db, open_group, take_student):
    act, g = open_group(5, needs_leader=True)
    _appoint(client, admin, _enroll(client, act, g, take_student()))
    _lock(client, admin, g)
    assert _alerts(db, act) == []


def test_多个组缺组长合成一条(client, admin, db, open_group, take_student):
    act, g = open_group(5, needs_leader=True)
    db.add(Position(id=uid("p"), shift_id=g.id, name="南门引导", quota=5,
                    needs_leader=True, sort=1, **POSITION_TEXT))
    db.add(Position(id=uid("p"), shift_id=g.id, name="不要组长", quota=5,
                    sort=2, **POSITION_TEXT))
    db.commit()
    _lock(client, admin, g)
    rows = _alerts(db, act)
    assert len(rows) == 1
    assert "测试岗位" in rows[0].content and "南门引导" in rows[0].content
    assert "不要组长" not in rows[0].content


def test_锁定前组长退选不提醒_锁定后退选立即提醒(client, admin, db, open_group,
                                              take_student):
    act, g = open_group(5, needs_leader=True)
    first = _enroll(client, act, g, take_student())
    _appoint(client, admin, first)
    _quit(client, db, first)
    assert _alerts(db, act) == [], "锁定前不提醒，等锁定时统一提醒"

    second = _enroll(client, act, g, take_student())
    _appoint(client, admin, second)
    _lock(client, admin, g)
    assert _alerts(db, act) == []
    _quit(client, db, second)
    assert len(_alerts(db, act)) == 1


def test_锁定后才打开需要组长立即提醒(client, admin, db, open_group, take_student):
    act, g = open_group(5)
    _enroll(client, act, g, take_student())
    _lock(client, admin, g)
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{g.test_position_id}",
                         headers=admin,
                         json={"needs_leader": True,
                               "leader_task": "带队进场、清点人数"})
        assert r.status_code == 200, r.text
    assert len(_alerts(db, act)) == 1


def test_到自动锁定点定时任务提醒一次(client, admin, db, open_group):
    act, g = open_group(5, needs_leader=True, day_offset=0, start_in_hours=10,
                        lock_hours=12)
    later, g2 = open_group(5, needs_leader=True, day_offset=0,
                           start_in_hours=20, lock_hours=12)
    scheduled.send_leader_vacancy_alerts(db)
    assert len(_alerts(db, act)) == 1
    assert _alerts(db, later) == [], "还没到锁定点"
    scheduled.send_leader_vacancy_alerts(db)
    assert len(_alerts(db, act)) == 1, "同一次锁定只提醒一次"


def _shift_with_leader_post(db, act, base, *, name, pos_name, group_name="",
                            hours_later=1):
    """同一活动里再加一个要组长的场次，返回这个场次。"""
    g = mk_shift(id=uid("g"), activity_id=act.id, name=name,
                 start_time=base.start_time + timedelta(hours=hours_later),
                 end_time=base.end_time + timedelta(hours=hours_later),
                 mode="pick", sort=1, lock_hours=base.lock_hours)
    db.add(g)
    db.flush()
    db.add(Position(id=uid("p"), shift_id=g.id, name=pos_name, quota=5,
                    group_name=group_name, needs_leader=True, sort=0,
                    **POSITION_TEXT))
    db.commit()
    return g


def test_同活动几场同时到点合成一条(client, admin, db, open_group):
    """一个活动的几场常常同时到锁定点（同一个锁定小时数）：合成一条发，
    别让管理者收到几条标题一样的通知。"""
    act, g = open_group(5, needs_leader=True, day_offset=0, start_in_hours=10,
                        lock_hours=12)
    _shift_with_leader_post(db, act, g, name="第二场", pos_name="第二岗")
    scheduled.send_leader_vacancy_alerts(db)
    rows = _alerts(db, act)
    assert len(rows) == 1, rows
    assert "测试场次·测试岗位" in rows[0].content
    assert "第二场·第二岗" in rows[0].content


def test_跨场次的同一个组只说一次(client, admin, db, open_group):
    """组跨场次：两场里都有「东门片区」的岗位，缺的还是同一个组长。"""
    act, g = open_group(5, needs_leader=True, day_offset=0, start_in_hours=10,
                        lock_hours=12)
    first = db.query(Position).filter(Position.shift_id == g.id).one()
    first.group_name = "东门片区"
    db.commit()
    _shift_with_leader_post(db, act, g, name="第二场", pos_name="东门检录",
                            group_name="东门片区")
    scheduled.send_leader_vacancy_alerts(db)
    rows = _alerts(db, act)
    assert len(rows) == 1, rows
    assert rows[0].content.count("东门片区") == 1, rows[0].content
    assert "测试岗位" in rows[0].content and "东门检录" in rows[0].content


def test_不自动锁定的场次报名截止时兜底提醒(client, admin, db, open_group):
    act, g = open_group(5, needs_leader=True, lock_hours=0)
    scheduled.send_leader_vacancy_alerts(db)
    assert _alerts(db, act) == [], "报名还没截止"
    db.get(Activity, act.id).reg_end = now() - timedelta(minutes=1)
    db.commit()
    scheduled.send_leader_vacancy_alerts(db)
    assert len(_alerts(db, act)) == 1
    scheduled.send_leader_vacancy_alerts(db)
    assert len(_alerts(db, act)) == 1
