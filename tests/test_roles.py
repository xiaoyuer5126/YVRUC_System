"""角色管理：只有管理员能改，且改不出「谁也进不去」的局面。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, Department, User, now,
                            uid)
from conftest import ADMIN, login, student_row


def _me(db, sid=ADMIN):
    return db.query(User).filter(User.student_id == sid).one()


def _find(rows, student_id):
    return next((r for r in rows if r["student_id"] == student_id), None)


def test_只有管理员进得来(client, admin, initiator, db, take_student):
    assert client.get("/api/staff/roles", headers=admin).status_code == 200
    assert client.get("/api/staff/roles", headers=initiator).status_code == 403
    t = login(client, take_student())
    assert client.get("/api/staff/roles", headers=t).status_code == 403


def test_默认只列有管理身份的人搜索才找得到学生(client, admin, db, take_student):
    sid = take_student()
    rows = client.get("/api/staff/roles?size=100", headers=admin).json()["items"]
    assert _find(rows, sid) is None, "普通学生不该出现在默认列表里"
    assert all(set(r["roles"]) & {"admin", "qingxie", "initiator"} for r in rows)

    rows = client.get(f"/api/staff/roles?q={sid}", headers=admin).json()["items"]
    assert _find(rows, sid) is not None, "按学号搜得到"


def test_给学生加发起方身份(client, admin, db, take_student):
    sid = take_student()
    u = student_row(db, sid)
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student", "initiator"]})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert set(db.get(User, u.id).roles) == {"student", "initiator"}
    # 从此进得了工作台
    t = login(client, sid)
    assert client.get("/api/staff/activities", headers=t).status_code == 200


def test_不能移除自己的管理员身份(client, admin, db):
    me = _me(db)
    original = {"roles": list(me.roles), "qingxie_post": me.qingxie_post,
                "department_id": me.department_id or ""}
    r = client.put(f"/api/staff/roles/{me.id}", headers=admin,
                   json={"roles": ["qingxie"]})
    assert r.status_code == 400 and "不能移除自己的管理员" in r.json()["detail"]
    db.expire_all()
    assert "admin" in db.get(User, me.id).roles

    # 别的身份照常能加减。管理员是全场共用的，改完必须复原
    try:
        assert client.put(f"/api/staff/roles/{me.id}", headers=admin,
                          json={"roles": ["admin", "qingxie", "initiator"],
                                "qingxie_post": "council"}).status_code == 200
        db.expire_all()
        assert "initiator" in db.get(User, me.id).roles
    finally:
        assert client.put(f"/api/staff/roles/{me.id}", headers=admin,
                          json=original).status_code == 200
    db.expire_all()
    assert db.get(User, me.id).roles == original["roles"]


def test_至少保留一个身份(client, admin, db, take_student):
    u = student_row(db, take_student())
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin, json={"roles": []})
    assert r.status_code == 400 and "至少要保留一个身份" in r.json()["detail"]


def test_未知身份被拒(client, admin, db, take_student):
    u = student_row(db, take_student())
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student", "校长"]})
    assert r.status_code == 400 and "未知身份" in r.json()["detail"]


# ======================= 撤销时的唯一管理者检查 =======================

@pytest.fixture
def sole_manager(client, admin, db, take_student):
    """一个未结束的活动，其唯一管理者是某位发起方。"""
    sid = take_student()
    u = student_row(db, sid)
    u.roles = ["student", "initiator"]
    act = Activity(id=uid("act"), title=f"孤儿活动 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=3), created_by=u.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=u.id,
                           added_by=u.id))
    db.commit()
    return u, act


def test_撤销唯一管理者的身份会被拦住(client, admin, db, sole_manager):
    u, act = sole_manager
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student"]})
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert "唯一能操作的管理者" in detail and act.title in detail
    db.expire_all()
    assert "initiator" in db.get(User, u.id).roles, "被拦下就不该改"


def test_补一个管理者之后就能撤(client, admin, db, sole_manager):
    u, act = sole_manager
    other = _me(db)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=other.id, added_by=other.id))
    db.commit()
    assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                      json={"roles": ["student"]}).status_code == 200
    db.expire_all()
    assert db.get(User, u.id).roles == ["student"]


def test_已结束的活动不拦(client, admin, db, sole_manager):
    """已结束是终态、只读，没人管也不要紧。"""
    u, act = sole_manager
    db.get(Activity, act.id).status = "finished"
    db.commit()
    assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                      json={"roles": ["student"]}).status_code == 200
    db.expire_all()
    assert db.get(User, u.id).roles == ["student"]


def test_保留能写的青协层级就不算撤(client, admin, db, sole_manager):
    """改成部门骨干仍然能操作活动，没有孤儿问题；改成部门成员就等于撤了。"""
    u, _act = sole_manager
    dept = db.query(Department).first()
    assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "core",
                            "department_id": dept.id}).status_code == 200
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student", "qingxie"], "qingxie_post": "member",
                         "department_id": dept.id})
    assert r.status_code == 409 and "唯一能操作" in r.json()["detail"]


# ======================= 青协职务与部门 =======================

def test_设部长必须指定部门(client, admin, db, take_student):
    u = student_row(db, take_student())
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student", "qingxie"],
                         "qingxie_post": "head", "department_id": ""})
    assert r.status_code == 400 and "必须指定所属部门" in r.json()["detail"]


def test_同一部门可设多位部长(client, admin, db, take_student):
    dept = db.query(Department).filter(Department.name == "对外联络部").one()
    old_head = (db.query(User)
                .filter(User.department_id == dept.id,
                        User.qingxie_post == "head").one())
    u = student_row(db, take_student())
    try:
        assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                          json={"roles": ["student", "qingxie"],
                                "qingxie_post": "head",
                                "department_id": dept.id}).status_code == 200
        db.expire_all()
        assert db.get(User, u.id).qingxie_post == "head"
        assert db.get(User, old_head.id).qingxie_post == "head", \
            "一个部门支持同时设多位部长"
    finally:
        # 复原，免得影响别的用例
        assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                          json={"roles": ["student"]}).status_code == 200
    db.expire_all()
    assert db.get(User, old_head.id).qingxie_post == "head"


def test_不再是青协就清掉职务与部门(client, admin, db, take_student):
    """留着「非青协的部长」，审批那边会找到一个没有权限的人。

    用骨干而不是部长来验：清理逻辑不分层级，而设部长会把那个部门原来的
    部长顶掉，全场共用的演示数据就被改了。
    """
    dept = db.query(Department).filter(Department.name == "权益服务部").first() \
        or db.query(Department).first()
    u = student_row(db, take_student())
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student", "qingxie"], "qingxie_post": "core",
                         "department_id": dept.id})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(User, u.id)
    assert row.qingxie_post == "core" and row.department_id == dept.id

    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student"]})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(User, u.id)
    assert row.qingxie_post == "member" and row.department_id is None


def test_会长团设得上去(client, admin, db, take_student):
    u = student_row(db, take_student())
    assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                      json={"roles": ["student", "qingxie"],
                            "qingxie_post": "council"}).status_code == 200
    db.expire_all()
    assert db.get(User, u.id).is_council()


def test_改身份要留痕(client, admin, db, take_student):
    from backend.models import AuditLog

    u = student_row(db, take_student())
    assert client.put(f"/api/staff/roles/{u.id}", headers=admin,
                      json={"roles": ["student", "initiator"]}).status_code == 200
    row = (db.query(AuditLog)
           .filter(AuditLog.target_type == "user", AuditLog.target_id == u.id)
           .order_by(AuditLog.created_at.desc()).first())
    assert row is not None and "调整身份" in row.summary
    assert "活动发起方" in row.summary
    assert row.visible is False, "权限变更不对志愿者公开"


# ======================= 批量调整身份 =======================

def test_批量添加与移除青协身份(client, admin, db, take_student):
    """预览不落库；执行时身份、层级、部门、归属骨干一次落全；撤下时清干净。"""
    dept = db.query(Department).first()
    boss = student_row(db, take_student())
    boss.roles, boss.qingxie_post, boss.department_id = \
        ["student", "qingxie"], "core", dept.id
    db.commit()
    s1, s2 = take_student(), take_student()
    body = {"action": "add", "role": "qingxie", "qingxie_post": "member",
            "department_id": dept.id, "supervisor_id": boss.id,
            "student_ids": [s1, s2, "9999999999"]}

    r = client.post("/api/staff/roles/batch", headers=admin,
                    json={**body, "dry_run": True})
    assert r.status_code == 200, r.text
    d = r.json()
    assert {x["student_id"] for x in d["updated"]} == {s1, s2}
    assert d["not_found"] == ["9999999999"] and d["skipped"] == []
    db.expire_all()
    assert "qingxie" not in (student_row(db, s1).roles or []), "预览不落库"

    r = client.post("/api/staff/roles/batch", headers=admin, json=body)
    assert r.status_code == 200 and len(r.json()["updated"]) == 2, r.text
    db.expire_all()
    u1 = student_row(db, s1)
    assert set(u1.roles) == {"student", "qingxie"}
    assert (u1.qingxie_post, u1.department_id, u1.supervisor_id) == \
        ("member", dept.id, boss.id)

    r = client.post("/api/staff/roles/batch", headers=admin,
                    json={"action": "remove", "role": "qingxie",
                          "student_ids": [s1, s2]})
    assert r.status_code == 200 and len(r.json()["updated"]) == 2
    db.expire_all()
    u1 = student_row(db, s1)
    assert u1.roles == ["student"] and u1.department_id is None


def test_批量调整的边界(client, admin, db, take_student):
    """管理员/学生不批量，部长不批量；部长批量时越界的人跳过并写明原因。"""
    dept = db.query(Department).first()
    r = client.post("/api/staff/roles/batch", headers=admin,
                    json={"action": "add", "role": "admin", "student_ids": ["x"]})
    assert r.status_code == 400 and "批量只支持" in r.json()["detail"]
    assert client.post("/api/staff/roles/batch", headers=admin,
                       json={"action": "eat", "role": "initiator",
                             "student_ids": ["x"]}).status_code == 400
    assert client.post("/api/staff/roles/batch", headers=admin,
                       json={"action": "add", "role": "qingxie",
                             "student_ids": []}).status_code == 400
    # 批量设部长：一个部门只有一位，谁顶谁说不清，逐个来
    r = client.post("/api/staff/roles/batch", headers=admin,
                    json={"action": "add", "role": "qingxie", "qingxie_post": "head",
                          "department_id": dept.id, "student_ids": ["x"]})
    assert r.status_code == 400 and "逐个设置" in r.json()["detail"]

    head_dept = db.query(Department).filter(Department.name == "对外联络部").one()
    head = db.query(User).filter(User.department_id == head_dept.id,
                                 User.qingxie_post == "head").one()
    core = db.query(User).filter(User.department_id == head_dept.id,
                                 User.qingxie_post == "core").first()
    assert core is not None
    outsider = student_row(db, take_student())
    outsider.roles, outsider.qingxie_post = ["student", "qingxie"], "member"
    outsider.department_id = db.query(Department).filter(
        Department.id != head_dept.id).first().id
    db.commit()

    h = login(client, head.student_id)
    fresh = take_student()
    r = client.post("/api/staff/roles/batch", headers=h,
                    json={"action": "add", "role": "qingxie", "qingxie_post": "member",
                          "department_id": head_dept.id, "supervisor_id": core.id,
                          "student_ids": [fresh]})
    assert r.status_code == 200 and len(r.json()["updated"]) == 1, r.text
    db.expire_all()
    assert student_row(db, fresh).department_id == head_dept.id

    r = client.post("/api/staff/roles/batch", headers=h,
                    json={"action": "remove", "role": "qingxie",
                          "student_ids": [outsider.student_id, fresh]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert [x["student_id"] for x in d["updated"]] == [fresh]
    assert d["skipped"][0]["student_id"] == outsider.student_id
    assert "别的部门" in d["skipped"][0]["reason"]
    db.expire_all()
    assert "qingxie" in student_row(db, outsider.student_id).roles, "跳过的人不动"


def test_学生志愿者身份与教师活动发起方身份不可清除(client, admin, db, take_student):
    u = student_row(db, take_student())
    r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["initiator"]})
    assert r.status_code == 400 and "学生的「志愿者」身份不可清除" in r.json()["detail"]

    teacher = db.query(User).filter(User.user_type == "teacher",
                                    User.student_id != ADMIN).first()
    assert teacher is not None
    r = client.put(f"/api/staff/roles/{teacher.id}", headers=admin,
                   json={"roles": ["student"]})
    assert r.status_code == 400 and "教师的「活动发起方」身份不可清除" in r.json()["detail"]


def test_会长团可配置催办接收部门范围(client, admin, db, take_student):
    from backend.routers.staff_ops import council_members
    depts = db.query(Department).order_by(Department.sort).limit(2).all()
    d1, d2 = depts[0], depts[1]
    u = student_row(db, take_student())
    try:
        r = client.put(f"/api/staff/roles/{u.id}", headers=admin,
                       json={"roles": ["student", "qingxie"],
                             "qingxie_post": "council",
                             "remind_dept_ids": [d1.id]})
        assert r.status_code == 200
        db.expire_all()
        assert db.get(User, u.id).remind_dept_ids == [d1.id]
        assert u.id in {m.id for m in council_members(db, d1.id)}
        assert u.id not in {m.id for m in council_members(db, d2.id)}
    finally:
        client.put(f"/api/staff/roles/{u.id}", headers=admin,
                   json={"roles": ["student"]})

