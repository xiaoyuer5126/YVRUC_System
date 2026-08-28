"""活动审批：提交 → 部长通过/驳回 → 发起方自己发布；转交与超时提醒。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Department, User, now, uid
from conftest import ADMIN, POSITION_TEXT, login

QINGXIE_HEAD = "2025004"        # 周砚，对外联络部部长
PRESIDENT = "2025003"           # 张远航，会长


@pytest.fixture
def draft(client, admin, db):
    """一个结构完整、可以提交审批的草稿。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": f"待审批活动 {uid('t')}", "organizer": "文学院青协",
                          "type": "A", "intro": "x", "location": "世纪馆",
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


def test_提交后由部长通过再由发起方发布(client, admin, db, draft):
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
    assert r.status_code == 200 and r.json()["status"] == "approved"
    db.expire_all()
    assert db.get(Activity, draft).status == "approved"      # 不自动上线

    # 通过后才轮到发起方发布
    assert client.post(f"/api/staff/activities/{draft}/publish",
                       headers=admin).status_code == 200


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


def test_会长可以接管任何一条(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    president = login(client, PRESIDENT)
    assert draft in [a["id"] for a in
                     client.get("/api/staff/approvals", headers=president).json()]
    r = client.post(f"/api/staff/activities/{draft}/decide", headers=president,
                    json={"approve": True, "comment": "会长直接通过"})
    assert r.status_code == 200


def test_转交要写理由且换审批人(client, admin, db, draft):
    dept = _liaison(db)
    client.post(f"/api/staff/activities/{draft}/submit", headers=admin,
                json={"department_id": dept.id})
    head = login(client, QINGXIE_HEAD)

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head, json={"student_id": PRESIDENT, "reason": ""})
    assert r.status_code == 400 and "转交理由" in r.json()["detail"]

    r = client.post(f"/api/staff/activities/{draft}/transfer-approval",
                    headers=head,
                    json={"student_id": PRESIDENT, "reason": "该活动跨部门，请会长定"})
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


def test_部长不能审自己部门的活动(client, db, draft):
    """自己交自己批就不成其为审批。"""
    from backend.models import ActivityManager

    head_user = db.query(User).filter(User.student_id == QINGXIE_HEAD).one()
    db.add(ActivityManager(id=uid("mgr"), activity_id=draft,
                           user_id=head_user.id, added_by=head_user.id))
    db.commit()
    head = login(client, QINGXIE_HEAD)
    r = client.post(f"/api/staff/activities/{draft}/submit", headers=head,
                    json={"department_id": _liaison(db).id})
    assert r.status_code == 400 and "不能选择自己所在的部门" in r.json()["detail"]


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
