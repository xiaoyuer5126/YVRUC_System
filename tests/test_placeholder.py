"""占位岗位：「截止后统一分配」的场次也有岗位。

名额只有一个来源——岗位。assign 场次建出来时自动带一个「待分配」占位岗位，
报名的人都落在它上面；报名截止后改成 pick 模式、建真实岗位、跑一键分配，
人从占位里出去，排不下的留在里面。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Enrollment, Position, Shift, now, uid
from conftest import POSITION_TEXT, editing, login, student_row

ASSIGN = {"name": "统一分配场", "mode": "assign", "capacity": 3,
          "waitlist_quota": 2}


@pytest.fixture
def assign_shift(client, admin, db):
    """一个草稿活动 + 一个 assign 场次（自动带占位岗位）。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"占位测试 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={**ASSIGN,
                          "start_time": (now() + timedelta(days=6)).isoformat(),
                          "end_time": (now() + timedelta(days=6, hours=4)).isoformat()})
    assert r.status_code == 200, r.text
    return aid, db.get(Shift, r.json()["id"])


@pytest.fixture
def published(client, admin, db, assign_shift):
    """同一个活动，直接置为报名中，省去审批流程。"""
    from backend.models import Activity

    aid, g = assign_shift
    db.get(Activity, aid).status = "published"
    db.commit()
    return aid, g


def test_建assign场次自动带占位岗位(db, assign_shift):
    _aid, g = assign_shift
    db.expire_all()
    g = db.get(Shift, g.id)
    holder = g.placeholder()
    assert holder is not None
    assert holder.is_placeholder and holder.name == "待分配"
    # 名额记在它头上，场次不再自己存一份
    assert holder.quota == 3 and holder.waitlist_quota == 2
    assert g.total_capacity() == 3
    assert g.real_positions() == []


def test_学生报名落在占位岗位上(client, db, published, take_student):
    aid, g = published
    sid = take_student()
    t = login(client, sid)
    r = client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                    headers=t, json={})
    assert r.status_code == 200, r.text
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == g.id,
                   Enrollment.user_id == student_row(db, sid).id).one())
    assert enr.position_id == g.placeholder().id, "报名不该留下没有岗位的记录"


def test_assign场次学生端看到的是说明而不是岗位名(client, db, published,
                                                  take_student):
    """在这一场里它不是一个岗位，是「这一场怎么报」的说明。"""
    aid, g = published
    t = login(client, take_student())
    d = client.get(f"/api/activities/{aid}", headers=t).json()
    pos = d["shifts"][0]["positions"]
    assert len(pos) == 1 and pos[0]["is_placeholder"] is True
    assert pos[0]["display_name"] == "暂不选岗位 · 由负责老师后续统一分配"
    assert pos[0]["name"] == "待分配", "管理端仍按「待分配」记账"


def test_切成pick后占位就是普通岗位(client, admin, db, published, take_student):
    """名字换回本名，也能像别的岗位一样被选中——人从里面往外挪。"""
    aid, g = published
    with editing(client, admin, aid):
        client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                     json={"mode": "pick"})
    t = login(client, take_student())
    d = client.get(f"/api/activities/{aid}", headers=t).json()
    holder_out = d["shifts"][0]["positions"][0]
    assert holder_out["is_placeholder"] is True
    assert holder_out["display_name"] == "待分配"
    assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=t,
                       json={"position_id": holder_out["id"]}).status_code == 200


def test_名额满了排到占位岗位的候补(client, db, published, take_student):
    aid, g = published
    for _ in range(3):                      # quota=3，占满
        t = login(client, take_student())
        assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                           headers=t, json={}).status_code == 200
    t = login(client, take_student())
    assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=t, json={}).status_code == 200
    rows = (db.query(Enrollment)
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.status == "waitlisted").all())
    assert len(rows) == 1
    assert rows[0].position_id == g.placeholder().id, "候补也排在占位岗位上"


# ======================= 截止后：改模式、建岗位、一键分配 =======================

def test_一键分配把人从占位里挪出去(client, admin, db, published, take_student):
    aid, g = published
    for _ in range(3):
        t = login(client, take_student())
        client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                    headers=t, json={})

    with editing(client, admin, aid):
        # 加岗位前必须先改模式——这条约束保留
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "分拣", "quota": 2, **POSITION_TEXT})
        assert r.status_code == 409 and "截止后统一分配" in r.json()["detail"]

        assert client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                            json={"mode": "pick"}).status_code == 200
        pid = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                          json={"name": "分拣", "quota": 2, **POSITION_TEXT}
                          ).json()["id"]

    # 占位岗位还在，且仍带着标记——一键分配据此不往里排人
    db.expire_all()
    g = db.get(Shift, g.id)
    holder = g.placeholder()
    assert holder is not None and holder.is_placeholder
    assert [p.name for p in g.real_positions()] == ["分拣"]

    r = client.post(f"/api/staff/shifts/{g.id}/assign/preview", headers=admin,
                    json={})
    assert r.status_code == 200, r.text
    d = r.json()
    # 真实岗位只有 2 个位子，3 个人 → 2 人排上，1 人留在占位里
    assert all(x["position_id"] == pid for x in d["plan"])
    assert len(d["plan"]) == 2 and len(d["unassigned"]) == 1

    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"plan": d["plan"]}).status_code == 200
    db.expire_all()
    rows = (db.query(Enrollment)
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.status == "enrolled").all())
    assert sum(1 for r_ in rows if r_.position_id == pid) == 2
    assert sum(1 for r_ in rows if r_.position_id == holder.id) == 1, \
        "排不下的人留在占位岗位里，不是凭空消失"


def test_占位岗位空了才删得掉(client, admin, db, published, take_student):
    """所有岗位一个规矩：里面有人就不能删。"""
    aid, g = published
    t = login(client, take_student())
    client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll", headers=t, json={})
    holder = g.placeholder()

    with editing(client, admin, aid):
        r = client.delete(f"/api/staff/positions/{holder.id}", headers=admin)
        assert r.status_code == 409 and "仍有 1 人已报名" in r.json()["detail"]

        # 把人挪走之后就删得掉了
        enr = (db.query(Enrollment)
               .filter(Enrollment.position_id == holder.id).one())
        client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                     json={"mode": "pick"})
        pid = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                          json={"name": "分拣", "quota": 5, **POSITION_TEXT}
                          ).json()["id"]
        assert client.post(
            f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
            json={"source": "manual",
                  "plan": [{"enrollment_id": enr.id, "position_id": pid}]}
        ).status_code == 200
        assert client.delete(f"/api/staff/positions/{holder.id}",
                             headers=admin).status_code == 200


def test_待分配人数把占位里的人算进去(client, admin, db, published, take_student):
    """管理者要一眼看出「还有几个人没安排」。"""
    aid, g = published
    for _ in range(2):
        t = login(client, take_student())
        client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                    headers=t, json={})
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    sh = d["shifts"][0]
    assert sh["unassigned_count"] == 2
    assert sh["position_quota_sum"] == 0, "占位不算真实岗位名额"
