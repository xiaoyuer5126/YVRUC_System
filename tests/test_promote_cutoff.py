"""候补自动转正的截止点，以及之后的手动转正。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Activity, Enrollment, iso, now
from conftest import login


def _enroll(client, act, g, sid):
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=login(client, sid),
                       json={"position_id": g.test_position_id})


def _fill_and_queue(client, db, open_group, take_student, *,
                    reg_end_in_hours=None, **kw):
    """名额 1 的场次：一个人占满，一个人排队。返回 (活动, 场次, 候补学号, 候补的报名 id)。

    reg_end 默认设在场次开始前 2 小时——夹具给的一年后是个占位值，
    会让「自动转正截止点」也落到一年后。
    """
    act, g = open_group(1, **kw)
    assert _enroll(client, act, g, take_student()).json()["status"] == "enrolled"
    queued = take_student()
    r = _enroll(client, act, g, queued)
    assert r.json()["status"] == "waitlisted"

    # 报满之后才改报名截止时间：先关了就没人报得进来
    a = db.get(Activity, act.id)
    a.reg_end = (now() + timedelta(hours=reg_end_in_hours)
                 if reg_end_in_hours is not None
                 else g.start_time - timedelta(hours=2))
    db.commit()
    return act, g, queued, r.json()["enrollment_id"]


def _raise_quota(client, admin, act, g, quota):
    """管理者加名额：进编辑态、改岗位、保存并发布。返回发布接口的结果。

    这是开场前 24 小时内真正腾出位子的路——那个时段里的退选是扣留，不放位子。
    """
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    r = client.patch(f"/api/staff/positions/{g.test_position_id}", headers=admin,
                     json={"quota": quota})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _roster(client, admin, g):
    return client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()


def test_截止点是报名截止与开始前24h的较晚者(client, admin, db, open_group):
    act, g = open_group(1, day_offset=0, start_in_hours=100)
    a = db.get(Activity, act.id)

    a.reg_end = now() + timedelta(hours=1)      # 报名很早就截止
    db.commit()
    # 此时「开始前 24 小时」更晚，取它
    assert _roster(client, admin, g)["auto_promote_deadline"] \
        == iso(g.start_time - timedelta(hours=24))

    a.reg_end = g.start_time - timedelta(hours=2)   # 报名截止得很晚
    db.commit()
    assert _roster(client, admin, g)["auto_promote_deadline"] == iso(a.reg_end)


# 「报名窗口还开着就照常自动转正」见 test_seat_hold.py 的
# test_报名未截止时临期退选不扣留：同样是 10 小时后开场、报名未截止，候补照常递补。

def test_截止点之后不再自动转正(client, admin, db, open_group, take_student):
    """场次 10 小时后开始（已过「开始前 24 小时」）且报名已截止。

    两个条件要同时满足——报名窗口还开着时人还能自己报名，
    候补递补自然也该继续。
    """
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             day_offset=0, start_in_hours=10,
                                             reg_end_in_hours=-1)
    assert _raise_quota(client, admin, act, g, 2)["promoted"] == 0, \
        "截止点之后不该自动转正"
    # 队列原地不动，人还在排
    assert [x["enrollment_id"] for x in _roster(client, admin, g)["waitlist"]] == [wl_id]
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "waitlisted"


def test_截止后管理者手动转正仍要本人确认(client, admin, db, open_group, take_student):
    act, g, queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                            day_offset=0, start_in_hours=10,
                                            reg_end_in_hours=-1)
    _raise_quota(client, admin, act, g, 2)

    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 200
    db.expire_all()
    # 补不补人由管理者定，来不来由本人定：和自动递补一样开 2 小时确认窗
    row = db.get(Enrollment, wl_id)
    assert row.status == "pending_confirm"
    assert row.confirm_deadline is not None
    r = client.post(f"/api/activities/enrollments/{wl_id}/confirm-promotion",
                    headers=login(client, queued))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "enrolled"


def test_没空位时不能手动转正(client, admin, db, open_group, take_student):
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             day_offset=0, start_in_hours=10,
                                             reg_end_in_hours=-1)
    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 409 and "名额" in r.json()["detail"]


def test_名单页给出自动转正截止点(client, admin, db, open_group, take_student):
    act, g, *_ = _fill_and_queue(client, db, open_group, take_student,
                                 day_offset=0, start_in_hours=10, reg_end_in_hours=-1)
    d = _roster(client, admin, g)
    # 报名截止（1 小时前）晚于「开始前 24 小时」，截止点就是报名截止那一刻
    db.expire_all()
    assert d["auto_promote_deadline"] == iso(db.get(Activity, act.id).reg_end)
    assert d["auto_promote_over"] is True


def test_扩容后保存并发布会补齐候补(client, admin, db, open_group, take_student):
    """加名额是常见改动，名额空出来就该轮到候补，不必等下一个人退选。"""
    act, g = open_group(1, day_offset=0, start_in_hours=10)
    assert _enroll(client, act, g, take_student()).json()["status"] == "enrolled"
    for _ in range(2):
        assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"

    assert _raise_quota(client, admin, act, g, 3)["promoted"] == 2
    db.expire_all()
    assert db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status == "pending_confirm").count() == 2


def test_待确认期间名额不会被别人抢走(client, db, open_group, take_student):
    """确认窗内位子是给他留的，这两小时里别人报名只能进候补。"""
    act, g = open_group(1, day_offset=0, start_in_hours=10)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=login(client, taker)).json()["effect"] == "released"
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "pending_confirm"

    assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"
