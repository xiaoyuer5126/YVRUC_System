"""暂停报名：管理者停掉某个场次 / 岗位的新报名，不用进编辑态。

已报名的人照常，可以退选；有人退选时候补照常递补——那是暂停前排的队。
"""
from __future__ import annotations

from backend.models import Enrollment, Position, Shift, uid
from conftest import POSITION_TEXT, login
from test_screening import _answers, _create_screened, _holders


def _enroll(client, act, g, sid, pid):
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=login(client, sid), json={"position_id": pid})


def test_暂停岗位与场次都拦新报名(client, admin, db, open_group, take_student):
    act, g = open_group(3)
    pid = g.test_position_id
    r = client.post(f"/api/staff/positions/{pid}/pause", headers=admin, json={"paused": True})
    assert r.status_code == 200, r.text          # 报名中的活动，不进编辑态也能切
    r = _enroll(client, act, g, take_student(), pid)
    assert r.status_code == 409 and "岗位「测试岗位」暂停报名" in r.json()["detail"]

    client.post(f"/api/staff/positions/{pid}/pause", headers=admin, json={"paused": False})
    client.post(f"/api/staff/shifts/{g.id}/pause", headers=admin, json={"paused": True})
    r = _enroll(client, act, g, take_student(), pid)
    assert r.status_code == 409 and "场次「测试场次」暂停报名" in r.json()["detail"]
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()["shifts"][0]
    assert d["enroll_paused"] is True

    client.post(f"/api/staff/shifts/{g.id}/pause", headers=admin, json={"paused": False})
    assert _enroll(client, act, g, take_student(), pid).status_code == 200


def test_暂停期间候补照常递补(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    pid = g.test_position_id
    a, b = take_student(), take_student()
    first = _enroll(client, act, g, a, pid).json()["enrollment_id"]
    queued = _enroll(client, act, g, b, pid).json()["enrollment_id"]
    assert db.get(Enrollment, queued).status == "waitlisted"
    client.post(f"/api/staff/positions/{pid}/pause", headers=admin, json={"paused": True})
    r = client.delete(f"/api/activities/enrollments/{first}", headers=login(client, a))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Enrollment, queued).status == "pending_confirm"


def test_待分配与培训不单独暂停_学生不能切(client, admin, db, open_group, take_student):
    act, g = open_group(3, mode="assign")
    r = client.post(f"/api/staff/positions/{g.placeholder().id}/pause", headers=admin,
                    json={"paused": True})
    assert r.status_code == 400 and "暂停整个场次" in r.json()["detail"]
    att = Shift(id=uid("g"), activity_id=act.id, name="彩排", kind="rehearsal",
                parent_id=g.id, start_time=g.start_time, end_time=g.end_time, sort=1)
    db.add(att)
    db.commit()
    r = client.post(f"/api/staff/shifts/{att.id}/pause", headers=admin, json={"paused": True})
    assert r.status_code == 400
    r = client.post(f"/api/staff/shifts/{g.id}/pause", headers=login(client, take_student()),
                    json={"paused": True})
    assert r.status_code == 403


def test_筛选申请也拦暂停的场次与截止后分配的真实岗位(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    client.post(f"/api/staff/shifts/{ids[0]}/pause", headers=admin, json={"paused": True})
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, take_student()),
                    json={"position_ids": _holders(db, ids[:1]), "answers": _answers(q)})
    assert r.status_code == 409 and "暂停报名" in r.json()["detail"]

    real = Position(id=uid("p"), shift_id=ids[1], name="引导", quota=2, sort=0, **POSITION_TEXT)
    db.add(real)
    db.commit()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, take_student()),
                    json={"position_ids": [real.id], "answers": _answers(q)})
    assert r.status_code == 400 and "只能勾「待分配」" in r.json()["detail"]
