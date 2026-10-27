"""岗位、场次都没填地点时，通知里的地点用活动地点。"""
from __future__ import annotations

from backend.models import Enrollment
from conftest import inbox


def test_岗位没填地点时组长通知写活动地点(client, admin, db, enrolled_activity):
    act, shift, u = enrolled_activity
    pos = shift.positions[0]
    pos.location = ""
    shift.location = ""
    pos.needs_leader = True
    pos.leader_task = "带队进场"
    db.commit()
    enr = db.query(Enrollment).filter(Enrollment.user_id == u.id).one()

    r = client.post(f"/api/staff/enrollments/{enr.id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 200, r.text

    notes = inbox(db, u.student_id, "您已成为岗位组长")
    assert notes and "地点：世纪馆" in notes[-1].content


def test_场次填了地点就用场次的(client, admin, db, enrolled_activity):
    act, shift, u = enrolled_activity
    pos = shift.positions[0]
    pos.location = ""
    shift.location = "东门广场"
    pos.needs_leader = True
    pos.leader_task = "带队进场"
    db.commit()
    enr = db.query(Enrollment).filter(Enrollment.user_id == u.id).one()

    r = client.post(f"/api/staff/enrollments/{enr.id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 200, r.text

    notes = inbox(db, u.student_id, "您已成为岗位组长")
    assert notes and "地点：东门广场" in notes[-1].content
