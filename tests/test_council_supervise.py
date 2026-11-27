"""分管会长：会长团成员在角色管理里勾「分管部门」，部门的审批超时催办抄送给
分管它的人；不勾 = 不分管，哪个部门的都不收。部门没人分管时发标为会长的
那位，会长也没标才发全体会长团。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import scheduled
from backend.models import Activity, Department, Notification, User, now
from conftest import (ADMIN, LIAISON_HEAD, QINGXIE, make_draft,
                      student_row)


def _liaison(db) -> Department:
    return db.query(Department).filter(Department.name == "对外联络部").one()


def _other(db) -> Department:
    return (db.query(Department).filter(Department.name != "对外联络部")
            .order_by(Department.sort).first())


@pytest.fixture
def councils(client, admin, db, take_student, council_reset):
    """三个新的会长团成员：A 分管外联部，B 分管别的部门，C 什么都不勾。"""
    made = {}
    for key, depts in (("A", [_liaison(db).id]), ("B", [_other(db).id]), ("C", [])):
        sid = take_student()
        u = student_row(db, sid)
        r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                       json={"roles": ["student", "qingxie"],
                             "qingxie_post": "council", "remind_dept_ids": depts})
        assert r.status_code == 200, r.text
        made[key] = sid
    yield made
    for sid in made.values():
        u = student_row(db, sid)
        client.put(f"/api/staff/roles/{u.id}", headers=admin, json={"roles": ["student"]})


def _stale_approval(client, admin, db) -> Activity:
    """提交到对外联络部、已经等了 48 小时的活动审批。"""
    aid = make_draft(client, admin)
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                    json={"department_id": _liaison(db).id})
    assert r.status_code == 200, r.text
    db.expire_all()
    act = db.get(Activity, aid)
    act.submitted_at = now() - timedelta(hours=49)
    db.commit()
    return act


def _reminded(db, act) -> set[str]:
    db.expire_all()
    ids = {n.user_id for n in db.query(Notification)
           .filter(Notification.title.startswith("审批已等待"),
                   Notification.title.endswith(act.title)).all()}
    return {u.student_id for u in db.query(User).filter(User.id.in_(ids)).all()}


def test_只抄送分管这个部门的会长团成员(client, admin, db, councils):
    act = _stale_approval(client, admin, db)
    scheduled.remind_stale_approvals(db)
    got = _reminded(db, act)
    assert LIAISON_HEAD in got                       # 照旧催部长
    assert councils["A"] in got                      # 勾了外联部的收
    assert councils["B"] not in got                  # 分管别的部门的不收
    assert councils["C"] not in got                  # 不勾 = 不分管，不收
    assert QINGXIE not in got and ADMIN not in got   # 有人分管时会长不兜底


def test_部门没人分管时抄送会长(client, admin, db, councils):
    a = student_row(db, councils["A"])
    assert client.put(f"/api/staff/roles/{a.id}", headers=admin,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "council",
                            "remind_dept_ids": [_other(db).id]}).status_code == 200
    act = _stale_approval(client, admin, db)
    scheduled.remind_stale_approvals(db)
    got = _reminded(db, act)
    assert QINGXIE in got                            # 会长（张远航）兜底
    assert not {councils["A"], councils["B"], councils["C"]} & got


def test_没人分管也没标会长时发全体会长团(client, admin, db, councils):
    db.query(User).filter(User.qingxie_post == "council").update(
        {"is_president": False, "remind_dept_ids": []}, synchronize_session=False)
    db.commit()
    act = _stale_approval(client, admin, db)
    scheduled.remind_stale_approvals(db)
    got = _reminded(db, act)
    assert {QINGXIE, ADMIN, councils["A"], councils["B"], councils["C"]} <= got


def test_页面上取到的分管部门就是存下的(client, admin, db, councils):
    items = client.get("/api/staff/roles", headers=admin,
                       params={"q": councils["A"]}).json()["items"]
    mine = next(x for x in items if x["student_id"] == councils["A"])
    assert mine["remind_dept_ids"] == [_liaison(db).id]
    items = client.get("/api/staff/roles", headers=admin,
                       params={"q": councils["C"]}).json()["items"]
    assert next(x for x in items if x["student_id"] == councils["C"])["remind_dept_ids"] == []
