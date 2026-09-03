"""青协四级：会长团 / 部长 / 部门骨干 / 部门成员。

演示账号：2025003 张远航 会长团；2025004 周砚 对外联络部部长；
2025005 林骁 对外联络部骨干；2025006 苏昀 对外联络部成员（学习权限）。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.routers import all_routers
from backend.models import Activity, ActivityManager, Department, User, now, uid
from conftest import ADMIN, POSITION_TEXT, login, student_row

COUNCIL, HEAD, CORE, MEMBER = "2025003", "2025004", "2025005", "2025006"


def _user(db, sid):
    return db.query(User).filter(User.student_id == sid).one()


def _liaison(db):
    return db.query(Department).filter(Department.name == "对外联络部").one()


@pytest.fixture
def dept_activity(client, db):
    """一个归口对外联络部、由部长建的草稿，带一个场次一个岗位。"""
    head = login(client, HEAD)
    r = client.post("/api/staff/activities", headers=head,
                    json={"contact_group_qr": "x", "title": f"部门活动 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    g = client.post(f"/api/staff/activities/{aid}/shifts", headers=head,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=9)).isoformat(),
                          "end_time": (now() + timedelta(days=9, hours=3)).isoformat()}
                    ).json()["id"]
    client.post(f"/api/staff/shifts/{g}/positions", headers=head,
                json={"name": "引导", "quota": 3, **POSITION_TEXT})
    return aid, g


# ======================= 部门成员：学习权限 =======================

def _staff_write_routes():
    """管理端所有会写库的路由：/api/staff 下非 GET 的。"""
    out = []
    for router in all_routers:
        for r in router.routes:
            path = getattr(r, "path", "")
            methods = getattr(r, "methods", None) or set()
            if not path.startswith("/api/staff"):
                continue
            for m in methods - {"GET", "HEAD", "OPTIONS"}:
                out.append((m, path, r.endpoint.__name__))
    return sorted(out)


def test_部门成员对每一个写接口都被拦在写库之前(client):
    """枚举全部路由而不是挑几个：以后新加接口忘了这件事，这里会红。"""
    member = login(client, MEMBER)
    routes = _staff_write_routes()
    assert len(routes) > 40, "路由数不对，枚举方式可能失效"
    leaked = []
    for method, path, name in routes:
        url = path
        for seg in ("{activity_id}", "{shift_id}", "{position_id}", "{task_id}",
                    "{enrollment_id}", "{user_id}", "{dept_id}", "{material_id}",
                    "{key}", "{batch_id}", "{appeal_id}", "{record_id}"):
            url = url.replace(seg, "x")
        r = client.request(method, url, headers=member, json={})
        detail = r.json().get("detail") if r.status_code == 403 else None
        trainee_hit = isinstance(detail, dict) and detail.get("code") == "TRAINEE_READONLY"
        if name == "assign_preview":
            ok = not trainee_hit          # 试算不写库，不该被学习权限拦
        else:
            # 被学习权限拦，或被更严的闸（仅管理员/会长团/部长）先拦，都算没写进去
            ok = r.status_code == 403
        if not ok:
            leaked.append(f"{method} {path} → {r.status_code}")
    assert not leaked, "这些接口没有被学习权限拦住：\n" + "\n".join(leaked)


def test_部门成员看得到本部门活动但改不了(client, db, dept_activity):
    aid, g = dept_activity
    member = login(client, MEMBER)
    ids = [a["id"] for a in client.get("/api/staff/activities", headers=member).json()]
    assert aid in ids, "本部门的活动要看得到"
    assert client.get(f"/api/activities/{aid}", headers=member).status_code == 200
    r = client.patch(f"/api/staff/activities/{aid}", headers=member,
                     json={"title": "改不了"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "TRAINEE_READONLY"
    db.expire_all()
    assert db.get(Activity, aid).title != "改不了"


def test_部门成员不能导出也不能查档案(client, db, dept_activity):
    aid, _g = dept_activity
    member = login(client, MEMBER)
    tok = member["Authorization"].split()[1]
    r = client.get(f"/api/staff/activities/{aid}/export.xlsx?token={tok}")
    assert r.status_code == 403
    assert client.get("/api/staff/users", headers=member).status_code == 403


def test_试算不被学习权限拦(client, db, dept_activity):
    aid, g = dept_activity
    member = login(client, MEMBER)
    r = client.post(f"/api/staff/shifts/{g}/assign/preview", headers=member, json={})
    assert r.status_code != 403, r.text


# ======================= 可见范围 =======================

def test_部长看本部门全部含草稿而骨干只看自己管的(client, db, dept_activity):
    aid, _g = dept_activity
    head, core = login(client, HEAD), login(client, CORE)
    assert aid in [a["id"] for a in client.get("/api/staff/activities", headers=head).json()]
    assert aid not in [a["id"] for a in client.get("/api/staff/activities", headers=core).json()]
    # 把骨干加进共管名单之后就看得到了
    core_user = _user(db, CORE)
    db.add(ActivityManager(id=uid("mgr"), activity_id=aid, user_id=core_user.id,
                           added_by=core_user.id))
    db.commit()
    assert aid in [a["id"] for a in client.get("/api/staff/activities", headers=core).json()]


def test_会长团除集成设置外与管理员同权(client, db):
    council = login(client, COUNCIL)
    assert client.get("/api/staff/overview", headers=council).status_code == 200
    assert client.get("/api/staff/roles", headers=council).status_code == 200
    assert client.get("/api/staff/users", headers=council).status_code == 200
    assert client.get("/api/staff/integrations", headers=council).status_code == 403


def test_骨干进不了审批档案与角色页(client):
    core = login(client, CORE)
    for path in ("/api/staff/approvals", "/api/staff/users", "/api/staff/roles",
                 "/api/staff/overview"):
        assert client.get(path, headers=core).status_code == 403, path


# ======================= 部长的角色管理范围 =======================

def test_部长只能在本部门设骨干或成员(client, db, take_student):
    head = login(client, HEAD)
    dept = _liaison(db)
    other = db.query(Department).filter(Department.id != dept.id).first()
    u = student_row(db, take_student())

    # 设成本部门成员：可以
    r = client.put(f"/api/staff/roles/{u.id}", headers=head,
                   json={"roles": ["student", "qingxie"], "qingxie_post": "member",
                         "department_id": dept.id})
    assert r.status_code == 200, r.text
    # 升成骨干：可以
    assert client.put(f"/api/staff/roles/{u.id}", headers=head,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "core",
                            "department_id": dept.id}).status_code == 200
    # 设成部长 / 会长团：不行
    for post in ("head", "council"):
        r = client.put(f"/api/staff/roles/{u.id}", headers=head,
                       json={"roles": ["student", "qingxie"], "qingxie_post": post,
                             "department_id": dept.id})
        assert r.status_code == 403, post
    # 设到别的部门：不行
    r = client.put(f"/api/staff/roles/{u.id}", headers=head,
                   json={"roles": ["student", "qingxie"], "qingxie_post": "member",
                         "department_id": other.id})
    assert r.status_code == 403
    # 给管理员身份：不行
    r = client.put(f"/api/staff/roles/{u.id}", headers=head,
                   json={"roles": ["student", "qingxie", "admin"],
                         "qingxie_post": "member", "department_id": dept.id})
    assert r.status_code == 403
    # 撤下本部门骨干：可以
    assert client.put(f"/api/staff/roles/{u.id}", headers=head,
                      json={"roles": ["student"]}).status_code == 200


def test_部长动不了会长团和别的部长(client, db):
    head = login(client, HEAD)
    for sid in (COUNCIL, ADMIN):
        target = _user(db, sid)
        r = client.put(f"/api/staff/roles/{target.id}", headers=head,
                       json={"roles": ["student"]})
        assert r.status_code == 403, sid


def test_部长的角色列表默认只有本部门(client, db):
    head = login(client, HEAD)
    rows = client.get("/api/staff/roles", headers=head).json()
    dept = _liaison(db)
    assert rows and all(r["department_id"] == dept.id for r in rows)


# ======================= 唯一管理者检查只数能写的人 =======================

def test_部门成员不算能操作的管理者(client, admin, db, take_student):
    """名单里只剩一个部门成员，等于没人能管；撤掉唯一能写的人要被拦。"""
    writer = student_row(db, take_student())
    writer.roles = ["student", "initiator"]
    member = _user(db, MEMBER)
    act = Activity(id=uid("act"), title=f"只剩学习权限 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=3), created_by=writer.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=writer.id,
                           added_by=writer.id))
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=member.id,
                           added_by=writer.id))
    db.commit()
    r = client.put(f"/api/staff/roles/{writer.id}", headers=admin,
                   json={"roles": ["student"]})
    assert r.status_code == 409 and "唯一能操作" in r.json()["detail"]
