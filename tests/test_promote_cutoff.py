"""候补自动转正的截止点，以及之后的手动转正。"""
from __future__ import annotations

from datetime import timedelta

from backend import enroll_logic as logic
from backend.models import Activity, Enrollment, Shift, now
from conftest import login


def _fill_and_queue(client, db, open_group, take_student, *,
                    reg_end_in_hours=None, **kw):
    """名额 1 的场次：一个人占满，一个人排队。

    reg_end 默认设在场次开始前 2 小时——夹具给的一年后是个占位值，
    会让「自动转正截止点」也落到一年后。
    """
    act, g = open_group(1, **kw)
    first = take_student()
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, first),
                    json={"position_id": g.test_position_id})
    assert r.json()["status"] == "confirmed"
    queued = take_student()
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, queued),
                    json={"position_id": g.test_position_id})
    assert r.json()["status"] == "waitlisted"

    # 报满之后才改报名截止时间：先关了就没人报得进来
    a = db.get(Activity, act.id)
    a.reg_end = (now() + timedelta(hours=reg_end_in_hours)
                 if reg_end_in_hours is not None
                 else g.start_time - timedelta(hours=2))
    db.commit()
    return act, g, first, queued


def test_截止点是报名截止与开始前24h的较晚者(db, open_group):
    act, g = open_group(1, day_offset=0, start_in_hours=100)
    a = db.get(Activity, act.id)

    a.reg_end = now() + timedelta(hours=1)      # 报名很早就截止
    db.commit()
    # 此时「开始前 24 小时」更晚，取它
    assert logic.auto_promote_deadline(db, db.get(Shift, g.id)) \
        == g.start_time - timedelta(hours=24)

    a.reg_end = g.start_time - timedelta(hours=2)   # 报名截止得很晚
    db.commit()
    assert logic.auto_promote_deadline(db, db.get(Shift, g.id)) == a.reg_end


def _free_the_seat(db, shift_id):
    """腾出一个名额，且不走「临近退选扣留」那条路。

    24 小时内退选是占位不释放的，所以这个时段里想验证「转正」，
    必须用管理者调整名额／移人这类真正腾位的动作来造场景。
    """
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == shift_id,
                   Enrollment.status == "confirmed").one())
    enr.status = "cancelled"
    enr.cancel_type = "normal"
    enr.seat_held = False
    db.commit()
    return enr


def test_报名窗口还开着时照常自动转正(client, db, open_group, take_student):
    """哪怕活动就在 10 小时后：报名都还没截止，候补递补当然继续。"""
    act, g, first, queued = _fill_and_queue(client, db, open_group, take_student,
                                            day_offset=0, start_in_hours=10,
                                            reg_end_in_hours=5)
    _free_the_seat(db, g.id)
    assert logic.promote_all(db, db.get(Shift, g.id)) == 1
    db.commit()
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "pending_confirm").count() == 1


def test_截止点之后不再自动转正(client, db, open_group, take_student):
    """场次 10 小时后开始（已过「开始前 24 小时」）且报名已截止。

    两个条件要同时满足——报名窗口还开着时人还能自己报名，
    候补递补自然也该继续。
    """
    act, g, first, queued = _fill_and_queue(client, db, open_group, take_student,
                                            day_offset=0, start_in_hours=10,
                                            reg_end_in_hours=-1)
    _free_the_seat(db, g.id)
    assert logic.promote_all(db, db.get(Shift, g.id)) == 0, "截止点之后不该自动转正"
    db.commit()
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "pending_confirm").count() == 0
    # 队列原地不动，人还在排
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "waitlisted").count() == 1


def test_截止后管理者可手动转正(client, admin, db, open_group, take_student):
    act, g, first, queued = _fill_and_queue(client, db, open_group, take_student,
                                            day_offset=0, start_in_hours=10,
                                            reg_end_in_hours=-1)
    _free_the_seat(db, g.id)

    waiting = (db.query(Enrollment)
               .filter(Enrollment.shift_id == g.id,
                       Enrollment.status == "waitlisted").one())
    r = client.post(f"/api/staff/enrollments/{waiting.id}/promote",
                    headers=admin)
    assert r.status_code == 200
    db.expire_all()
    assert db.get(Enrollment, waiting.id).status == "confirmed"
    # 手动转正不走 2 小时确认窗：活动就在眼前，再等两小时名额只会继续空着
    assert db.get(Enrollment, waiting.id).confirm_deadline is None


def test_没空位时不能手动转正(client, admin, db, open_group, take_student):
    act, g, first, queued = _fill_and_queue(client, db, open_group, take_student,
                                            day_offset=0, start_in_hours=10,
                                            reg_end_in_hours=-1)
    waiting = (db.query(Enrollment)
               .filter(Enrollment.shift_id == g.id,
                       Enrollment.status == "waitlisted").one())
    r = client.post(f"/api/staff/enrollments/{waiting.id}/promote",
                    headers=admin)
    assert r.status_code == 409 and "名额" in r.json()["detail"]


def test_名单页给出自动转正截止点(client, admin, db, open_group, take_student):
    act, g, *_ = _fill_and_queue(client, db, open_group, take_student,
                                 day_offset=0, start_in_hours=10, reg_end_in_hours=-1)
    d = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    assert d["auto_promote_deadline"]
    assert d["auto_promote_over"] is True
