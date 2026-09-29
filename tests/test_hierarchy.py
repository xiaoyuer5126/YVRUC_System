"""青协四级：会长团 / 部长 / 部门骨干 / 部门成员。

演示账号：2025003 张远航 会长团；2025004 周砚 对外联络部部长；
2025005 林骁 对外联络部骨干；2025006 苏昀 对外联络部成员（学习权限）。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.routers import all_routers
from backend.models import Activity, ActivityManager, Department, User, now, uid
from conftest import ADMIN, POSITION_TEXT, dl_url, login, student_row

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


# 这些接口的门槛比「管理身份」更高（管理员/会长团/部长/骨干），部门成员在
# 那一道就被拦下，轮不到学习权限出场。点名列出：新接口不在表里就必须是学习权限拦的。
STRICTER_GATE = {"create_department", "update_department", "delete_department",
                 "pin", "put_integration", "set_roles", "handle_feedback"}


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
        elif name in STRICTER_GATE:
            # 被更严的身份闸先拦下，同样没写进去
            ok = isinstance(detail, str) and detail.startswith("需要")
        else:
            # 只认学习权限那一道：别处的 403（比如「不是该活动管理者」）
            # 发生在处理函数里，说明请求已经越过了这道闸
            ok = trainee_hit
        if not ok:
            leaked.append(f"{method} {path} → {r.status_code}")
    assert not leaked, "这些接口没有被学习权限拦住：\n" + "\n".join(leaked)


def _add_core_manager(db, activity_id):
    """把 2025006 归属的那位骨干加进共管名单——成员的可见范围跟着他走。"""
    core_user = _user(db, CORE)
    db.add(ActivityManager(id=uid("mgr"), activity_id=activity_id,
                           user_id=core_user.id, added_by=core_user.id))
    db.commit()


def test_部门成员跟着归属的骨干看同样的活动(client, db, dept_activity):
    """成员是跟着某个人学做事的：骨干看得到什么，他就看得到什么，但只能看。"""
    aid, g = dept_activity
    member = login(client, MEMBER)
    core_user = _user(db, CORE)
    # 骨干还没被加进这个活动，所以成员也看不到
    assert aid not in [a["id"] for a in
                       client.get("/api/staff/activities", headers=member).json()["items"]]

    db.add(ActivityManager(id=uid("mgr"), activity_id=aid, user_id=core_user.id,
                           added_by=core_user.id))
    db.commit()
    ids = [a["id"] for a in client.get("/api/staff/activities", headers=member).json()["items"]]
    assert aid in ids, "归属骨干管的活动要跟着看得到"
    assert client.get(f"/api/activities/{aid}", headers=member).status_code == 200
    r = client.patch(f"/api/staff/activities/{aid}", headers=member,
                     json={"title": "改不了"})
    assert r.status_code == 403 and r.json()["detail"]["code"] == "TRAINEE_READONLY"
    db.expire_all()
    assert db.get(Activity, aid).title != "改不了"


def test_部门成员不能导出也不能查档案(client, db, dept_activity):
    aid, _g = dept_activity
    _add_core_manager(db, aid)
    member = login(client, MEMBER)
    r = client.get(dl_url(client, member,
                          f"/api/staff/activities/{aid}/export.xlsx"))
    assert r.status_code == 403
    assert client.get("/api/staff/users", headers=member).status_code == 403


def test_部门成员的导出一个都下不到(client, db, dept_activity):
    """回归：原先只拦了排班表，时长记录（带身份证号）和报名问卷结果漏了。
    以后新加的导出（.xlsx/.zip 下载或附件数）也得点名拦，这里按路由表查一遍。"""
    from backend.deps import TRAINEE_DENIED_READS
    exports = set()
    for router in all_routers:
        for r in router.routes:
            path = getattr(r, "path", "")
            if (path.startswith("/api/staff") and "GET" in (getattr(r, "methods", None) or ())
                    and (path.endswith((".xlsx", ".zip")) or path.endswith("/attachments"))):
                exports.add(r.endpoint.__name__)
    assert exports and exports <= TRAINEE_DENIED_READS, exports - TRAINEE_DENIED_READS

    aid, _g = dept_activity
    _add_core_manager(db, aid)
    member = login(client, MEMBER)
    for tail in ("hours.xlsx", "applications.xlsx", "export/attachments.zip",
                 "applications/attachments.zip"):
        r = client.get(dl_url(client, member, f"/api/staff/activities/{aid}/{tail}"))
        assert r.status_code == 403, tail
    for tail in ("export/attachments", "applications/attachments"):
        r = client.get(f"/api/staff/activities/{aid}/{tail}", headers=member)
        assert r.status_code == 403 and r.json()["detail"]["code"] == "TRAINEE_READONLY"


def test_试算不被学习权限拦(client, db, dept_activity):
    aid, g = dept_activity
    _add_core_manager(db, aid)
    member = login(client, MEMBER)
    r = client.post(f"/api/staff/shifts/{g}/assign/preview", headers=member, json={})
    assert r.status_code == 200, r.text


# ======================= 可见范围 =======================

def test_部长看本部门全部含草稿而骨干只看自己管的(client, db, dept_activity):
    aid, _g = dept_activity
    head, core = login(client, HEAD), login(client, CORE)
    assert aid in [a["id"] for a in client.get("/api/staff/activities", headers=head).json()["items"]]
    assert aid not in [a["id"] for a in client.get("/api/staff/activities", headers=core).json()["items"]]
    # 把骨干加进共管名单之后就看得到了
    core_user = _user(db, CORE)
    db.add(ActivityManager(id=uid("mgr"), activity_id=aid, user_id=core_user.id,
                           added_by=core_user.id))
    db.commit()
    assert aid in [a["id"] for a in client.get("/api/staff/activities", headers=core).json()["items"]]


def test_会长团除集成设置外与管理员同权(client, db):
    council = login(client, COUNCIL)
    assert client.get("/api/staff/overview", headers=council).status_code == 200
    assert client.get("/api/staff/roles", headers=council).status_code == 200
    assert client.get("/api/staff/users", headers=council).status_code == 200
    assert client.get("/api/staff/integrations", headers=council).status_code == 403


def test_骨干进不了档案与总览(client):
    core = login(client, CORE)
    for path in ("/api/staff/users", "/api/staff/overview"):
        assert client.get(path, headers=core).status_code == 403, path
    # 审批页例外：对外联络部的骨干有值班资格，要看得到谁在班、接得了班；
    # 不在班时只看得到点名转给自己的（值班见 test_liaison_duty.py）
    r = client.get("/api/staff/approvals", headers=core)
    assert r.status_code == 200
    assert all(a["assignee"] == "林骁" for a in r.json())


def test_骨干只能把本部门成员归到自己名下(client, admin, db, take_student):
    """角色页对骨干只开一个口子：认领成员。身份、层级、部门一概动不了。"""
    core = login(client, CORE)
    me = _user(db, CORE)
    rows = client.get("/api/staff/roles", headers=core).json()
    assert rows and all(r["qingxie_post"] == "member"
                        and r["department_id"] == me.department_id for r in rows)
    member = _user(db, MEMBER)
    base = {"roles": member.roles, "qingxie_post": "member",
            "department_id": member.department_id}
    r = client.put(f"/api/staff/roles/{member.id}", headers=core,
                   json={**base, "supervisor_id": me.id})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(User, member.id).supervisor_id == me.id
    # 归给别人、升成骨干、加发起方身份：都不行。
    # 「别人」现立一位本部门骨干，演示数据里未必有第二个
    other_core = student_row(db, take_student())
    assert client.put(f"/api/staff/roles/{other_core.id}", headers=admin,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "core",
                            "department_id": me.department_id}).status_code == 200
    try:
        assert client.put(f"/api/staff/roles/{member.id}", headers=core,
                          json={**base, "supervisor_id": other_core.id}).status_code == 403
    finally:
        # 部门骨干名单是共享状态，别让它多出一个人影响别的用例
        assert client.put(f"/api/staff/roles/{other_core.id}", headers=admin,
                          json={"roles": ["student"]}).status_code == 200
    db.expire_all()
    assert db.get(User, member.id).supervisor_id == me.id
    assert client.put(f"/api/staff/roles/{member.id}", headers=core,
                      json={**base, "qingxie_post": "core"}).status_code == 403
    assert client.put(f"/api/staff/roles/{member.id}", headers=core,
                      json={**base, "roles": member.roles + ["initiator"],
                            "supervisor_id": me.id}).status_code == 403
    # 动骨干、部长：不行
    assert client.put(f"/api/staff/roles/{_user(db, HEAD).id}", headers=core,
                      json={**base, "supervisor_id": me.id}).status_code == 403


# ======================= 部长的角色管理范围 =======================

def test_部长只能在本部门设骨干或成员(client, db, take_student):
    head = login(client, HEAD)
    dept = _liaison(db)
    other = db.query(Department).filter(Department.id != dept.id).first()
    u = student_row(db, take_student())

    # 设成本部门成员：要指定归属的骨干，不指定不给过
    body = {"roles": ["student", "qingxie"], "qingxie_post": "member",
            "department_id": dept.id}
    r = client.put(f"/api/staff/roles/{u.id}", headers=head, json=body)
    assert r.status_code == 400 and "归属" in r.json()["detail"]
    cores = client.get(f"/api/staff/roles/cores?department_id={dept.id}",
                       headers=head).json()
    assert cores, "对外联络部应当有骨干"
    r = client.put(f"/api/staff/roles/{u.id}", headers=head,
                   json={**body, "supervisor_id": cores[0]["id"]})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(User, u.id).supervisor_id == cores[0]["id"]
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
                         "department_id": other.id, "supervisor_id": cores[0]["id"]})
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


def test_部长不能动青协以外的身份(client, db):
    """学生身份也在「青协以外」之列：部长不能把成员的学生身份勾掉。"""
    head = login(client, HEAD)
    member = _user(db, MEMBER)
    core = _user(db, CORE)
    body = {"qingxie_post": "member", "department_id": member.department_id,
            "supervisor_id": core.id}
    r = client.put(f"/api/staff/roles/{member.id}", headers=head,
                   json={**body, "roles": ["qingxie"]})
    assert r.status_code == 403 and "青协身份" in r.json()["detail"]
    r = client.put(f"/api/staff/roles/{member.id}", headers=head,
                   json={**body, "roles": ["student", "qingxie", "initiator"]})
    assert r.status_code == 403
    # 原样保存（只动归属）可以
    assert client.put(f"/api/staff/roles/{member.id}", headers=head,
                      json={**body, "roles": member.roles}).status_code == 200


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


def test_共管名单的快捷候选按层级给(client, admin, db, dept_activity):
    """常加的就那几个人：会长团、部长、自己手下的骨干与成员。"""
    aid, _g = dept_activity
    head = login(client, HEAD)
    groups = client.get(f"/api/staff/activities/{aid}/manager-candidates",
                        headers=head).json()["groups"]
    labels = [g["label"] for g in groups]
    assert labels[0] == "会长团"
    assert "本部门骨干" in labels and "本部门成员" in labels
    # 建活动的人自己已经在名单里，不该再作为候选出现
    ids = {u["student_id"] for g in groups for u in g["users"]}
    assert HEAD not in ids and CORE in ids

    # 管理员没有部门，只看到会长团与各部门部长
    labels = [g["label"] for g in
              client.get(f"/api/staff/activities/{aid}/manager-candidates",
                         headers=admin).json()["groups"]]
    assert set(labels) <= {"会长团", "各部门部长"} and "会长团" in labels

    # 骨干看得到自己名下的成员
    core = login(client, CORE)
    client.post(f"/api/staff/activities/{aid}/managers", headers=head,
                json={"student_id": CORE})
    labels = [g["label"] for g in
              client.get(f"/api/staff/activities/{aid}/manager-candidates",
                         headers=core).json()["groups"]]
    assert "我管理的成员" in labels
