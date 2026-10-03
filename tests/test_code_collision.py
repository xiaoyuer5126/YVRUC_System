"""短码撞码：固定签到码、服装码、动态签到码生成时撞上已有的码要换一个。"""
from __future__ import annotations

import secrets

from backend import locks
from backend.models import Position, uid
from backend.routers import attire, staff_ops
from conftest import POSITION_TEXT


def _fresh(n: int = 6) -> str:
    """一个库里肯定没有的码（用例自己造，别跟真实生成的撞）。"""
    return "Z" + secrets.token_hex(8).upper()[:n - 1]


def _feed(codes):
    """依次吐出给定的码，吐完就报错——多调了一次说明重试逻辑不对。"""
    it = iter(codes)
    return lambda: next(it)


def _new_task(client, admin, g) -> dict:
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                    json={"mode": "shift"}, headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


# ======================= 固定签到码 =======================

def test_固定签到码撞上已有的会换一个(client, admin, open_group, monkeypatch):
    _act, g1 = open_group(5)
    _act, g2 = open_group(5)
    taken = _new_task(client, admin, g1)["token"]

    fresh = _fresh()
    monkeypatch.setattr(staff_ops, "new_checkin_token", _feed([taken, taken, fresh]))
    d = _new_task(client, admin, g2)
    assert d["token"] == fresh
    # 原来那张码还在原任务上，没被挤掉
    act1 = client.get(f"/api/staff/activities/{g1.activity_id}/checkin-tasks",
                      headers=admin).json()
    assert [t["token"] for t in act1] == [taken]


def test_一次生成多张码时彼此也不撞(client, admin, open_group, db, monkeypatch):
    """分岗位多选一次开好几张：第二张抽到第一张刚生成的码，也得换掉。"""
    _act, g = open_group(5)
    p2 = Position(id=uid("p"), shift_id=g.id, name="第二岗位", quota=3, sort=1,
                  **POSITION_TEXT)
    db.add(p2)
    db.commit()
    a, b = _fresh(), _fresh()
    monkeypatch.setattr(staff_ops, "new_checkin_token", _feed([a, a, b]))
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                    json={"mode": "position",
                          "position_ids": [g.test_position_id, p2.id]})
    assert r.status_code == 200, r.text
    assert sorted(t["token"] for t in r.json()["tasks"]) == sorted([a, b])


def test_固定签到码一直撞就报503且不落半截(client, admin, open_group, monkeypatch):
    _act, g1 = open_group(5)
    _act, g2 = open_group(5)
    taken = _new_task(client, admin, g1)["token"]
    monkeypatch.setattr(staff_ops, "new_checkin_token", lambda: taken)
    r = client.post(f"/api/staff/shifts/{g2.id}/checkin-tasks",
                    json={"mode": "shift"}, headers=admin)
    assert r.status_code == 503
    assert client.get(f"/api/staff/activities/{g2.activity_id}/checkin-tasks",
                      headers=admin).json() == []


# ======================= 服装码 =======================

def test_服装码用的是secrets(client, admin, open_group, monkeypatch):
    """random 的序列能从输出里推出来；码得出自 secrets。"""
    assert not hasattr(attire, "random")
    picked = iter("WXYZ2345" * 2)
    monkeypatch.setattr(attire.secrets, "choice", lambda _alphabet: next(picked))
    act, _g = open_group(5)
    d = client.post(f"/api/staff/activities/{act.id}/attire-tasks",
                    headers=admin, json={}).json()
    assert d["take"]["token"] == "WXYZ23"
    assert d["return"]["token"] == "45WXYZ"


def test_服装码撞上已有的会换一个(client, admin, open_group, monkeypatch):
    act1, _g = open_group(5)
    act2, _g = open_group(5)
    first = client.post(f"/api/staff/activities/{act1.id}/attire-tasks",
                        headers=admin, json={}).json()
    taken = first["take"]["token"]

    a, b = _fresh(), _fresh()
    monkeypatch.setattr(attire, "_token", _feed([taken, a, first["return"]["token"], b]))
    r = client.post(f"/api/staff/activities/{act2.id}/attire-tasks",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    assert r.json() == {"take": {"token": a, "active": True},
                        "return": {"token": b, "active": True}}
    # 第一场的码原样不动
    again = client.post(f"/api/staff/activities/{act1.id}/attire-tasks",
                        headers=admin, json={}).json()
    assert again == first


# ======================= 动态签到码 =======================

def test_动态码撞上别的任务正在用的码会换一张(client, admin, open_group, monkeypatch):
    _act, g1 = open_group(5)
    _act, g2 = open_group(5)
    t1 = _new_task(client, admin, g1)["id"]
    t2 = _new_task(client, admin, g2)["id"]
    held = client.get(f"/api/staff/checkin-tasks/{t1}/code",
                      headers=admin).json()["code"]

    fresh = _fresh(locks.CHECKIN_CODE_LEN)
    monkeypatch.setattr(locks, "_new_checkin_code", _feed([held, held, fresh]))
    d = client.get(f"/api/staff/checkin-tasks/{t2}/code", headers=admin).json()
    assert d["code"] == fresh
    # 被撞的那张码仍指向原任务：另一场的人扫它不会签进这一场
    assert locks.redeem_checkin_code(held) == t1
    assert locks.redeem_checkin_code(fresh) == t2
    # 原任务续期拿到的还是它自己那张
    assert client.get(f"/api/staff/checkin-tasks/{t1}/code",
                      headers=admin).json()["code"] == held


def test_动态码一直撞就报503(client, admin, open_group, monkeypatch):
    _act, g1 = open_group(5)
    _act, g2 = open_group(5)
    t1 = _new_task(client, admin, g1)["id"]
    t2 = _new_task(client, admin, g2)["id"]
    held = client.get(f"/api/staff/checkin-tasks/{t1}/code",
                      headers=admin).json()["code"]
    monkeypatch.setattr(locks, "_new_checkin_code", lambda: held)
    r = client.get(f"/api/staff/checkin-tasks/{t2}/code", headers=admin)
    assert r.status_code == 503
    assert locks.redeem_checkin_code(held) == t1
