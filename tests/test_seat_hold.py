"""临近退选的名额：只在报名已截止时扣留，扣留可由管理者释放。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Activity, Enrollment, now
from conftest import login


def _one_seat(client, db, open_group, take_student, *, reg_end_in_hours):
    """名额 1 的场次，一人占满、一人候补；报名截止时间可控。"""
    act, g = open_group(1, day_offset=0, start_in_hours=10)
    taker = take_student()
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, taker),
                    json={"position_id": g.test_position_id})
    assert r.json()["status"] == "enrolled"
    queued = take_student()
    client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                headers=login(client, queued),
                json={"position_id": g.test_position_id})
    db.get(Activity, act.id).reg_end = now() + timedelta(hours=reg_end_in_hours)
    db.commit()
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == g.id,
                   Enrollment.status == "enrolled").one())
    return act, g, taker, enr


def test_报名未截止时临近退选不扣留(client, db, open_group, take_student):
    """平台还在招人，把位子废掉自相矛盾。"""
    act, g, taker, enr = _one_seat(client, db, open_group, take_student,
                                   reg_end_in_hours=5)
    r = client.delete(f"/api/activities/enrollments/{enr.id}",
                      headers=login(client, taker))
    assert r.status_code == 200
    body = r.json()
    assert body["effect"] == "released"            # 名额照常释放
    assert "已释放" in body["message"]
    db.expire_all()
    assert db.get(Enrollment, enr.id).seat_held is False
    # 名额放回去了，候补该递补
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "pending_confirm").count() == 1


def test_报名已截止时临近退选扣留名额(client, db, open_group, take_student):
    act, g, taker, enr = _one_seat(client, db, open_group, take_student,
                                   reg_end_in_hours=-1)
    r = client.delete(f"/api/activities/enrollments/{enr.id}",
                      headers=login(client, taker))
    assert r.json()["effect"] == "held"
    assert "扣留" in r.json()["message"]
    db.expire_all()
    assert db.get(Enrollment, enr.id).seat_held is True
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "pending_confirm").count() == 0


def test_管理者可以释放扣留的名额(client, admin, db, open_group, take_student):
    act, g, taker, enr = _one_seat(client, db, open_group, take_student,
                                   reg_end_in_hours=-1)
    client.delete(f"/api/activities/enrollments/{enr.id}",
                  headers=login(client, taker))

    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    assert len(roster["held"]) == 1, "扣留的记录要单独列出来，否则老师找不到"

    r = client.post(f"/api/staff/enrollments/{enr.id}/release-seat", headers=admin)
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Enrollment, enr.id).seat_held is False

    # 已过自动转正截止点：位子放出来了但不会自动被占，接口要说清楚还差一步，
    # 否则老师以为补完了，位子一直空着
    assert r.json()["auto_promote_over"] is True
    assert r.json()["promoted"] == 0
    assert "手动" in r.json()["message"]

    waiting = (db.query(Enrollment)
               .filter(Enrollment.shift_id == g.id,
                       Enrollment.status == "waitlisted").one())
    assert client.post(f"/api/staff/enrollments/{waiting.id}/promote",
                       headers=admin).status_code == 200


def test_释放不改岗位名额(client, admin, db, open_group, take_student):
    """扣留从来不动老师填的数字，释放同样不动。"""
    act, g, taker, enr = _one_seat(client, db, open_group, take_student,
                                   reg_end_in_hours=-1)
    from backend.models import Position
    quota_before = db.get(Position, g.test_position_id).quota
    client.delete(f"/api/activities/enrollments/{enr.id}",
                  headers=login(client, taker))
    client.post(f"/api/staff/enrollments/{enr.id}/release-seat", headers=admin)
    db.expire_all()
    assert db.get(Position, g.test_position_id).quota == quota_before


def test_没扣留的记录不能释放(client, admin, db, open_group, take_student):
    act, g, taker, enr = _one_seat(client, db, open_group, take_student,
                                   reg_end_in_hours=5)
    r = client.post(f"/api/staff/enrollments/{enr.id}/release-seat", headers=admin)
    assert r.status_code == 409
