"""权限跟随当前身份：前端每个请求带 X-Identity，后端只在这一个身份里判权。

一个人可以同时是管理员、青协骨干、学生。从前后端收不到「现在以哪个身份
在操作」，按所有身份的并集判权，各接口的优先级又不一致——管理员兼骨干
在角色管理里看到的是空列表。现在：
  · 带了头、且确实持有这个身份 → 只按它判权（只会收窄，不会放宽）；
  · 没带头、或带了自己没有的身份 → 忽略，按并集（与从前一致）；
  · 登录与 /me 下发的身份列表、「不能移除自己的管理员身份」这类身份事实，
    永远按真实身份。
"""
from __future__ import annotations

import pytest

from backend.models import Department, User
from conftest import ADMIN, dl_url, login, student_row

HEAD, CORE = "2025004", "2025005"


def _as(headers: dict, identity: str) -> dict:
    return {**headers, "X-Identity": identity}


def _sids(rows) -> set[str]:
    return {r["student_id"] for r in rows}


def _liaison(db):
    return db.query(Department).filter(Department.name == "对外联络部").one()


def _other_dept(db):
    return (db.query(Department).filter(Department.name != "对外联络部")
            .order_by(Department.sort).first())


@pytest.fixture
def make_user(db, take_student):
    """现建一个带指定身份的人；用完把身份收回成学生，免得留在共享库里。"""
    made = []

    def _make(roles, post="member", dept_id=None, supervisor_id=None) -> str:
        sid = take_student()
        u = student_row(db, sid)
        u.roles = roles
        u.qingxie_post = post
        u.department_id = dept_id
        u.supervisor_id = supervisor_id
        db.commit()
        made.append(sid)
        return sid
    yield _make
    db.rollback()
    db.expire_all()
    for sid in made:
        u = student_row(db, sid)
        u.roles = ["student"]
        u.qingxie_post = "member"
        u.department_id = None
        u.supervisor_id = None
    db.commit()


# ---------- a. 管理员兼骨干：身份决定能看多少 ----------

def test_管理员兼骨干在管理员身份下看得到全部(client, make_user):
    sid = make_user(["admin", "qingxie", "student"], post="core")
    h = login(client, sid)

    r = client.get("/api/staff/roles", headers=_as(h, "admin"))
    assert r.status_code == 200, r.text
    rows = _sids(r.json())
    assert {ADMIN, HEAD, CORE} <= rows, "管理员身份下应看到全部管理类身份的人"

    # 切到青协身份：就是一个没有部门的骨干，只能认领本部门成员
    r = client.get("/api/staff/roles", headers=_as(h, "qingxie"))
    assert r.status_code == 200, r.text
    assert not ({ADMIN, HEAD, CORE} & _sids(r.json()))

    # 不带头按并集：管理员身份在，就不该被骨干的范围卡成空列表
    r = client.get("/api/staff/roles", headers=h)
    assert {ADMIN, HEAD, CORE} <= _sids(r.json())


def test_学生身份下进不了工作台(client, admin):
    assert client.get("/api/staff/roles", headers=_as(admin, "student")).status_code == 403
    assert client.get("/api/staff/activities",
                      headers=_as(admin, "student")).status_code == 403
    assert client.get("/api/staff/roles", headers=_as(admin, "admin")).status_code == 200


# ---------- b. 骨干名单按部门裁剪 ----------

def test_部长骨干查不到别部门的骨干(client, db, admin, make_user):
    other = _other_dept(db)
    foreign = make_user(["qingxie", "student"], post="core", dept_id=other.id)

    got = client.get(f"/api/staff/roles/cores?department_id={other.id}",
                     headers=admin).json()
    assert foreign in _sids(got), "管理员可以查任意部门"

    for sid in (HEAD, CORE):
        h = login(client, sid)
        r = client.get(f"/api/staff/roles/cores?department_id={other.id}", headers=h)
        assert r.status_code in (200, 403), r.text
        if r.status_code == 200:
            assert foreign not in _sids(r.json()), f"{sid} 越权看到了别部门的骨干"
        # 本部门的照常查得到
        mine = client.get("/api/staff/roles/cores", headers=h).json()
        assert CORE in _sids(mine)


def test_部长默认列表不越出本部门_骨干只看本部门成员(client, db, make_user):
    other = _other_dept(db)
    foreign = make_user(["qingxie", "student"], post="core", dept_id=other.id)
    for sid in (HEAD, CORE):
        rows = _sids(client.get("/api/staff/roles", headers=login(client, sid)).json())
        assert foreign not in rows
        assert ADMIN not in rows


# ---------- c. 伪造身份不提权 ----------

def test_伪造身份头不会提权(client, db):
    h = login(client, CORE)
    plain = _sids(client.get("/api/staff/roles", headers=h).json())
    forged = client.get("/api/staff/roles", headers=_as(h, "admin"))
    assert forged.status_code == 200
    assert _sids(forged.json()) == plain
    assert ADMIN not in _sids(forged.json())
    assert client.get("/api/staff/integrations",
                      headers=_as(h, "admin")).status_code == 403
    assert client.get("/api/staff/overview",
                      headers=_as(h, "admin")).status_code == 403
    # 乱写的值同样忽略
    assert client.get("/api/staff/roles",
                      headers=_as(h, "root")).status_code == 200


# ---------- d. 不带头与从前一致 ----------

def test_不带头按全部身份判权(client, admin):
    assert client.get("/api/staff/integrations", headers=admin).status_code == 200
    assert client.get("/api/staff/overview", headers=admin).status_code == 200
    h = login(client, CORE)
    assert client.get("/api/staff/integrations", headers=h).status_code == 403
    assert client.get("/api/staff/roles", headers=h).status_code == 200


# ---------- e. 管理员身份不受青协层级牵连；/me 始终给全部身份 ----------

def test_管理员兼部门成员_身份决定能不能写(client, db, make_user):
    liaison = _liaison(db)
    core = student_row(db, CORE)
    sid = make_user(["admin", "qingxie", "student"], post="member",
                    dept_id=liaison.id, supervisor_id=core.id)
    h = login(client, sid)
    target = student_row(db, make_user(["student"]))
    body = {"roles": ["student", "initiator"]}

    # 青协身份下就是一个部门成员：学习权限，写请求被拦
    r = client.put(f"/api/staff/roles/{target.id}", headers=_as(h, "qingxie"), json=body)
    assert r.status_code == 403, r.text
    # 管理员身份下照常能改
    r = client.put(f"/api/staff/roles/{target.id}", headers=_as(h, "admin"), json=body)
    assert r.status_code == 200, r.text
    assert client.get("/api/staff/integrations",
                      headers=_as(h, "admin")).status_code == 200
    assert client.get("/api/staff/integrations",
                      headers=_as(h, "qingxie")).status_code == 403

    # /me 下发的是身份事实，与当前身份无关
    for view in ("admin", "qingxie", "student", None):
        me = client.get("/api/auth/me",
                        headers=_as(h, view) if view else h).json()["user"]
        assert set(me["roles"]) == {"admin", "qingxie", "student"}, view
        assert me["qingxie_post"] == "member", view


def test_值班资格按真实身份下发(client, admin):
    h = login(client, CORE)
    for view in ("student", "qingxie", None):
        me = client.get("/api/auth/me", headers=_as(h, view) if view else h).json()
        assert me["user"]["duty_eligible"] is True, view
        assert me["user"]["qingxie_post"] == "core", view


# ---------- f. 下载票按签发时的身份生效 ----------

def test_下载票沿用签发时的身份(client, db, admin, make_user, open_group):
    liaison = _liaison(db)
    core = student_row(db, CORE)
    sid = make_user(["admin", "qingxie", "student"], post="member",
                    dept_id=liaison.id, supervisor_id=core.id)
    h = login(client, sid)
    act, _ = open_group(3)
    path = f"/api/staff/activities/{act.id}/export.xlsx"

    # 以管理员身份换的票：能导出
    assert client.get(dl_url(client, _as(h, "admin"), path)).status_code == 200
    # 以青协身份（部门成员）换的票：学习权限，不能导出
    r = client.get(dl_url(client, _as(h, "qingxie"), path))
    assert r.status_code == 403, r.text
    # 不带头换的票：并集，与从前一致
    assert client.get(dl_url(client, h, path)).status_code == 200

    # 留痕里记着是以哪个身份导出的
    logs = client.get(f"/api/staff/audit-logs?activity_id={act.id}&target_type=export",
                      headers=admin).json()["items"]
    assert "admin" in [l.get("actor_identity") for l in logs]
    assert all(not any(c.get("field", "").startswith("_") for c in l["changes"])
               for l in logs), "身份记录不该混进变更明细"


# ---------- g. 不能移除自己的管理员身份：按真实身份 ----------

def test_青协身份下也不能移除自己的管理员身份(client, db, make_user):
    sid = make_user(["admin", "qingxie", "student"], post="council")
    u = student_row(db, sid)
    h = login(client, sid)
    r = client.put(f"/api/staff/roles/{u.id}", headers=_as(h, "qingxie"),
                   json={"roles": ["qingxie", "student"], "qingxie_post": "council"})
    assert r.status_code == 400 and "不能移除自己的管理员" in r.json()["detail"], r.text
    db.expire_all()
    assert "admin" in db.get(User, u.id).roles


def test_管理员身份下修改自己不会丢掉青协层级(client, db, make_user):
    """改的是自己时，库里查出来的「目标」就是当前用户本人；判断目标的
    层级、部门要按真实身份，不能被当前的管理员身份遮住。"""
    other = _other_dept(db)
    sid = make_user(["admin", "qingxie", "student"], post="core", dept_id=other.id)
    h = login(client, sid)
    rows = client.get(f"/api/staff/roles?q={sid}", headers=_as(h, "admin")).json()
    me = next(r for r in rows if r["student_id"] == sid)
    assert me["is_me"] and me["qingxie_post"] == "core"
