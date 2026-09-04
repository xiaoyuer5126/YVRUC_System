"""活动审批：提交 → 部长通过/驳回 → 发起方自己发布；转交与超时提醒。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Department, User, now, uid
from conftest import ADMIN, POSITION_TEXT, login

QINGXIE_HEAD = "2025004"        # 周砚，对外联络部部长
COUNCIL = "2025003"             # 张远航，会长团
CORE = "2025005"                # 林骁，对外联络部骨干


@pytest.fixture
def draft(client, admin, db):
    """一个结构完整、可以提交审批的草稿。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"待审批活动 {uid('t')}", "organizer": "文学院青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    g = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=9)).isoformat(),
                          "end_time": (now() + timedelta(days=9, hours=3)).isoformat()}
                    ).json()["id"]
    client.post(f"/api/staff/shifts/{g}/positions", headers=admin,
                json={"name": "引导", "quota": 3, **POSITION_TEXT})
    return aid


def _liaison(db):
    return db.query(Department).filter(Department.name == "对外联络部").one()


def test_草稿不能绕过审批直接发布(client, admin, draft):
    r = client.post(f"/api/staff/activities/{draft}/publish", headers=admin)
    assert r.status_code == 409 and "先提交校青协审批" in r.json()["detail"]


def test_提交后由部长通过即发布(client, admin, db, draft):
    dept = _liaison(db)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                    json={"department_id": dept.id, "note": "校庆相关"})
    assert r.status_code == 200 and r.json()["assignee"] == "周砚"
    db.expire_all()
    assert db.get(Activity, draft).status == "pending"

    # 待审批列表里，部长看得到自己那条
    head = login(client, QINGXIE_HEAD)
    ids = [a["id"] for a in client.get("/api/staff/approvals", headers=head).json()]
    assert draft in ids

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True, "comment": "同意"})
    assert r.status_code == 200 and r.json()["status"] == "published"
    db.expire_all()
    assert db.get(Activity, draft).status == "published"
    # 报名开始时间没到也照样发布，广场上显示倒计时
    assert db.get(Activity, draft).reg_start is not None


def test_关掉通过即发布就停在待发布(client, admin, db, draft):
    """想踩着某个时刻自己放出去的，通过后活动还在发起方手里。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id, "auto_publish": False})
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "approved"
    db.expire_all()
    assert db.get(Activity, draft).status == "approved"
    assert client.post(f"/api/staff/activities/{draft}/publish",
                       headers=admin).status_code == 200
    db.expire_all()
    assert db.get(Activity, draft).status == "published"


def test_审批期间把安排改坏了就通不过(client, admin, db, draft):
    """提交之后发起方还能改；通过的那一刻活动就上线，不能带着窟窿出去。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    act = db.get(Activity, draft)
    act.contact_group_qr = ""
    db.commit()
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True})
    assert r.status_code == 400 and "联络群" in r.json()["detail"]
    db.expire_all()
    assert db.get(Activity, draft).status == "pending"


def test_驳回必须写理由并退回草稿(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    head = login(client, QINGXIE_HEAD)

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": False, "comment": ""})
    assert r.status_code == 400 and "必须写明理由" in r.json()["detail"]

    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": False, "comment": "岗位要求写得太笼统"})
    assert r.status_code == 200
    db.expire_all()
    act = db.get(Activity, draft)
    assert act.status == "draft" and "笼统" in act.approval_comment

    # 改完可以重新提交
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": dept.id}).status_code == 200


def test_不是审批人就拍不了板(client, admin, db, draft, take_student):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    other = login(client, take_student())
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=other,
                    json={"approve": True})
    assert r.status_code == 403


def test_会长团可以接管任何一条(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    council = login(client, COUNCIL)
    assert draft in [a["id"] for a in
                     client.get("/api/staff/approvals", headers=council).json()]
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=council,
                    json={"approve": True, "comment": "会长团直接通过"})
    assert r.status_code == 200


def test_转交要写理由且换审批人(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    head = login(client, QINGXIE_HEAD)

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head, json={"student_id": COUNCIL, "reason": ""})
    assert r.status_code == 400 and "转交理由" in r.json()["detail"]

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head,
                    json={"student_id": COUNCIL, "reason": "该活动跨部门，请会长定"})
    assert r.status_code == 200 and r.json()["assignee"] == "张远航"

    # 转出之后原审批人就拍不了了
    assert client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                       json={"approve": True}).status_code == 403

    events = client.get(f"/api/staff/activities/{draft}/approval",
                        headers=admin).json()["events"]
    assert [e["kind"] for e in events] == ["submit", "transfer"]
    assert "跨部门" in events[-1]["comment"]


def test_没有部长的部门不能被选(client, admin, db, draft):
    empty = (db.query(Department)
             .filter(Department.name == "支教项目部").one())
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                    json={"department_id": empty.id})
    assert r.status_code == 400 and "还没有指定部长" in r.json()["detail"]


def test_部长自审要在流水里标明本人(client, db, draft):
    """部长自己办的活动归口自己部门、由自己审：允许，但留痕写清是本人。"""
    from backend.models import ActivityManager, ApprovalEvent

    head_user = db.query(User).filter(User.student_id == QINGXIE_HEAD).one()
    db.add(ActivityManager(id=uid("mgr"), activity_id=draft,
                           user_id=head_user.id, added_by=head_user.id))
    db.commit()
    head = login(client, QINGXIE_HEAD)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=head,
                    json={"department_id": _liaison(db).id})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                    json={"approve": True, "comment": "自己批"})
    assert r.status_code == 200, r.text
    ev = (db.query(ApprovalEvent)
          .filter(ApprovalEvent.activity_id == draft,
                  ApprovalEvent.kind == "approve").one())
    assert ev.comment.startswith("【本人审批】")


def test_青协建的活动自动归口自己部门(client, db, draft):
    """提交时不选部门也能交：归口已经按创建者所在部门预填。"""
    head = login(client, QINGXIE_HEAD)
    r = client.post("/api/staff/activities", headers=head,
                    json={"contact_group_qr": "x", "title": f"归口预填 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    db.expire_all()
    assert db.get(Activity, aid).approval_dept_id == _liaison(db).id
    # 部长在活动列表里直接看得到本部门的草稿
    assert aid in [a["id"] for a in
                   client.get("/api/staff/activities", headers=head).json()]


def test_审批超时只催人不放行(client, admin, db, draft):
    from backend import scheduled
    from backend.models import Notification

    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    act = db.get(Activity, draft)
    act.submitted_at = now() - timedelta(hours=48)
    db.commit()

    assert scheduled.remind_stale_approvals(db) == 1
    db.expire_all()
    assert db.get(Activity, draft).status == "pending"      # 绝不自动通过

    head_user = db.query(User).filter(User.student_id == QINGXIE_HEAD).one()
    assert db.query(Notification).filter(
        Notification.user_id == head_user.id,
        Notification.title.like("审批已等待%")).count() == 1

    # 催过一次就不再催
    assert scheduled.remind_stale_approvals(db) == 0


# ======================= 审批人看得到活动 =======================

def test_审批人能看未公开活动的完整安排(client, admin, db, draft, take_student):
    """看不到岗位与培训内容就没法批。审批页上那个「查看完整安排」要真能点开。

    归口部门的部长本来就按管理者视角看本部门活动；这里验的是被**转交**到手、
    又不是这个活动管理者的人——比如本部门的骨干。
    """
    head, core = login(client, QINGXIE_HEAD), login(client, CORE)
    assert client.get(f"/api/activities/{draft}", headers=core).status_code == 404

    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    assert client.post(f"/api/staff/activities/{draft}/transfer-approval",
                       headers=head, json={"student_id": CORE, "reason": "请骨干先看"}
                       ).status_code == 200
    d = client.get(f"/api/activities/{draft}", headers=core)
    assert d.status_code == 200, d.text
    d = d.json()
    assert d["review_preview"] is True and d["status_label"] == "待审批"
    # 培训内容是判断活动办不办得成的依据，按管理者视角给
    assert d["shifts"][0]["positions"][0]["training_visible"] is True

    # 无关的人照样看不见
    other = login(client, take_student())
    assert client.get(f"/api/activities/{draft}", headers=other).status_code == 404


def test_批过之后仍能回看自己批的那条(client, admin, db, draft):
    """通过到发起方点发布之间活动还不公开，审批人要能核对自己批了什么。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    client.post(f"/api/staff/activities/{draft}/transfer-approval", headers=head,
                json={"student_id": COUNCIL, "reason": "请会长定"})
    # 已经不是审批人了，但转交这一下留在流水里
    assert client.post(f"/api/staff/activities/{draft}/decide", headers=head,
                       json={"approve": True}).status_code == 403
    assert client.get(f"/api/activities/{draft}", headers=head).status_code == 200


def test_活动一旦公开审批人就退回普通视角(client, admin, db, draft, take_student):
    """公开之后他和别人看到的该是同一页——那时活动里已经有报名的人了。"""
    head, core = login(client, QINGXIE_HEAD), login(client, CORE)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    client.post(f"/api/staff/activities/{draft}/transfer-approval", headers=head,
                json={"student_id": CORE, "reason": "请骨干看"})
    client.post(f"/api/staff/activities/{draft}/decide", headers=core,
                json={"approve": True})

    d = client.get(f"/api/activities/{draft}", headers=core).json()
    assert d["review_preview"] is False
    assert d["shifts"][0]["positions"][0]["training_visible"] is False


def test_转交审批就是改归口(client, admin, db, draft, take_student):
    """转给哪个部门，活动就归那个部门；原部门部长的列表里随之消失。"""
    head = login(client, QINGXIE_HEAD)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": _liaison(db).id})
    other = db.query(Department).filter(Department.name == "环保项目部").one()
    # 给环保项目部临时立一位部长，转交才有人接
    new_head = db.query(User).filter(User.student_id == take_student()).one()
    assert client.put(f"/api/staff/roles/{new_head.id}", headers=admin,
                      json={"roles": ["student", "qingxie"], "qingxie_post": "head",
                            "department_id": other.id}).status_code == 200
    assert client.post(f"/api/staff/activities/{draft}/transfer-approval",
                       headers=head, json={"department_id": other.id,
                                           "reason": "环保类活动归他们"}
                       ).status_code == 200
    db.expire_all()
    assert db.get(Activity, draft).approval_dept_id == other.id
    assert draft not in [a["id"] for a in
                         client.get("/api/staff/activities", headers=head).json()]
    # 复原：撤掉这位临时部长，别影响别的用例
    client.put(f"/api/staff/roles/{new_head.id}", headers=admin,
               json={"roles": ["student"]})


def test_只有管理员能指定部长(client, admin, db):
    head = login(client, QINGXIE_HEAD)
    dept = db.query(Department).filter(Department.name == "环保项目部").one()
    assert client.patch(f"/api/staff/departments/{dept.id}", headers=head,
                        json={"head_student_id": QINGXIE_HEAD}).status_code == 403

    r = client.patch(f"/api/staff/departments/{dept.id}", headers=admin,
                     json={"head_student_id": QINGXIE_HEAD})
    assert r.status_code == 200
    rows = client.get("/api/staff/departments", headers=admin).json()
    assert next(d for d in rows if d["name"] == "环保项目部")["head_name"] == "周砚"
    # 换部门后原部门的部长位就空了
    assert next(d for d in rows if d["name"] == "对外联络部")["head_name"] == ""

    # 用例之间共用一个库，改完的全局状态要放回去
    client.patch(f"/api/staff/departments/{_liaison(db).id}", headers=admin,
                 json={"head_student_id": QINGXIE_HEAD})


def test_提交说明要出现在审批页上(client, admin, db, draft):
    """审批人就是在那一屏点通过/驳回的，提交时写的话必须在那里看得到。

    以前它只存进流水、发进通知，审批列表接口压根不返回，等于让人在看不到
    提交说明的情况下拍板。
    """
    NOTE = "下午场人力偏紧，麻烦重点看一下"
    dept = _liaison(db)
    assert client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                       json={"department_id": dept.id,
                             "note": NOTE}).status_code == 200

    row = next(x for x in client.get("/api/staff/approvals",
                                     headers=admin).json() if x["id"] == draft)
    assert row["submit_note"] == NOTE


def test_没写说明就给空串而不是缺字段(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    row = next(x for x in client.get("/api/staff/approvals",
                                     headers=admin).json() if x["id"] == draft)
    assert row["submit_note"] == ""
