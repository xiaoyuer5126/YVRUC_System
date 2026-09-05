"""志愿服装：一场活动两个码（领取/归还），台账一人一条。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, Enrollment, Position,
                            Shift, User, AttireRecord, now, uid)
from conftest import ADMIN, POSITION_TEXT, login, student_row


@pytest.fixture
def attire_activity(db, take_student):
    """一个活动：服装统一发放的岗位一个、自备的一个，各报一人。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"服装测试 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=2), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="当日",
              start_time=now() + timedelta(days=3),
              end_time=now() + timedelta(days=3, hours=4), mode="pick", sort=0)
    db.add(g)
    db.flush()
    need = Position(id=uid("p"), shift_id=g.id, name="引导", quota=5,
                    attire="issued", attire_note="北门物资桌领取", sort=0, **POSITION_TEXT)
    plain = Position(id=uid("p"), shift_id=g.id, name="内勤", quota=5,
                     attire="own", attire_note="白色上衣", sort=1, **POSITION_TEXT)
    db.add_all([need, plain])
    db.flush()

    a = student_row(db, take_student())
    b = student_row(db, take_student())
    for u, pos in ((a, need), (b, plain)):
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, position_id=pos.id, status="enrolled",
                          joined_at=now()))
    db.commit()
    return act, a, b


def _tokens(client, admin, act):
    r = client.post(f"/api/staff/activities/{act.id}/attire-tasks", headers=admin,
                    json={})
    assert r.status_code == 200, r.text
    d = r.json()
    return d["take"]["token"], d["return"]["token"]


def test_一场活动两个码且幂等(client, admin, attire_activity):
    act, _a, _b = attire_activity
    take1, ret1 = _tokens(client, admin, act)
    take2, ret2 = _tokens(client, admin, act)
    assert take1 != ret1              # 领取与归还是两个不同的码
    assert (take1, ret1) == (take2, ret2)   # 再点一次不会换码


def test_扫码领取与归还(client, admin, db, attire_activity):
    act, a, _b = attire_activity
    take, ret = _tokens(client, admin, act)
    h = login(client, a.student_id)

    info = client.get(f"/api/attire/task/{take}", headers=h).json()
    assert info["action"] == "take" and info["need_attire"] is True

    assert client.post(f"/api/attire/scan/{take}", headers=h).status_code == 200
    rec = db.query(AttireRecord).filter(AttireRecord.activity_id == act.id,
                                      AttireRecord.user_id == a.id).one()
    assert rec.taken_at is not None and rec.returned_at is None

    # 重复领取被拦
    r = client.post(f"/api/attire/scan/{take}", headers=h)
    assert r.status_code == 409 and "已经领过" in r.json()["detail"]

    assert client.post(f"/api/attire/scan/{ret}", headers=h, json={}).status_code == 200
    db.expire_all()
    assert db.query(AttireRecord).filter(
        AttireRecord.activity_id == act.id, AttireRecord.user_id == a.id).one().returned_at


def test_没领过不能归还(client, admin, attire_activity):
    act, a, _b = attire_activity
    _take, ret = _tokens(client, admin, act)
    h = login(client, a.student_id)
    r = client.post(f"/api/attire/scan/{ret}", headers=h, json={})
    assert r.status_code == 409 and "没有您的领取记录" in r.json()["detail"]


def test_岗位服装自备的人扫不了(client, admin, attire_activity):
    act, _a, b = attire_activity
    take, _ret = _tokens(client, admin, act)
    h = login(client, b.student_id)
    assert client.get(f"/api/attire/task/{take}",
                      headers=h).json()["need_attire"] is False
    r = client.post(f"/api/attire/scan/{take}", headers=h, json={})
    assert r.status_code == 403 and "不由平台统一发放" in r.json()["detail"]


def test_台账与手动登记(client, admin, db, attire_activity):
    act, a, _b = attire_activity
    _tokens(client, admin, act)
    url = f"/api/staff/activities/{act.id}/attire"

    d = client.get(url, headers=admin).json()
    assert d["expected"] == 1 and d["taken"] == 0     # 只有统一发放的岗位算应领
    assert [x["name"] for x in d["items"]] == [a.name]

    r = client.post(f"{url}/batch", headers=admin,
                    json={"action": "take", "student_ids": [a.student_id]})
    assert r.status_code == 200 and r.json()["done"] == 1
    d = client.get(url, headers=admin).json()
    assert d["taken"] == 1 and d["outstanding"] == 1
    assert d["items"][0]["taken"] is True

    # 未归还可以一键提醒；领了的人就不该再被催领取
    assert client.post(f"{url}/remind", headers=admin,
                       json={"action": "return"}).json()["count"] == 1
    assert client.post(f"{url}/remind", headers=admin,
                       json={"action": "take"}).status_code == 400

    client.post(f"{url}/batch", headers=admin,
                json={"action": "return", "student_ids": [a.student_id]})
    d = client.get(url, headers=admin).json()
    assert d["returned"] == 1 and d["outstanding"] == 0
    assert client.post(f"{url}/remind", headers=admin,
                       json={"action": "return"}).status_code == 400   # 没人可催了


def test_催领取服装与签收(client, admin, db, attire_activity):
    """活动当天没穿统一服装，现场认不出是志愿者，所以也要能催领取。"""
    from backend.models import Notification

    act, a, _b = attire_activity
    _tokens(client, admin, act)
    url = f"/api/staff/activities/{act.id}/attire"

    assert client.get(url, headers=admin).json()["not_taken"] == 1

    r = client.post(f"{url}/remind", headers=admin,
                    json={"action": "take", "need_ack": True})
    assert r.status_code == 200 and r.json()["count"] == 1
    batch = r.json()["batch_id"]
    assert batch

    n = (db.query(Notification)
         .filter(Notification.user_id == a.id,
                 Notification.title.like("请领取志愿服装%")).one())
    assert n.need_ack and n.batch_id == batch

    # 领了之后就不在催领取的名单里了
    client.post(f"{url}/batch", headers=admin,
                json={"action": "take", "student_ids": [a.student_id]})
    assert client.get(url, headers=admin).json()["not_taken"] == 0


def test_非本活动管理者看不到台账(client, initiator, attire_activity):
    act, _a, _b = attire_activity
    assert client.get(f"/api/staff/activities/{act.id}/attire",
                      headers=initiator).status_code == 403


def test_统一发放或自备都必须写服装备注(client, admin, db, open_group):
    """志愿者报名时看到的就是这行备注：去哪领、穿什么。没有它勾选等于没说。"""
    from conftest import editing

    act, g = open_group(2)
    pos_id = g.test_position_id
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{pos_id}", headers=admin,
                         json={"attire": "issued"})
        assert r.status_code == 400 and "服装备注" in r.json()["detail"]
        r = client.patch(f"/api/staff/positions/{pos_id}", headers=admin,
                         json={"attire": "own", "attire_note": "白色上衣"})
        assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    pos = next(p for s in d["shifts"] for p in s["positions"] if p["id"] == pos_id)
    assert pos["attire_text"] == "志愿者自备，白色上衣"
    # 自备的不进领取台账；改成统一发放后才出现在「应领」里
    assert not d["attire_issued"]
