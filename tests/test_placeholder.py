"""占位岗位：「截止后统一分配」的场次也有岗位。

名额只有一个来源——岗位。assign 场次建出来时自动带一个「待分配」占位岗位，
报名的人都落在它上面，名额按整场数。分工定下来后直接在场次下建真实岗位，
一键分配或导入排班把人从「待分配」挪过去；已经在岗位上的人一键分配不动。
有人报名之后场次不能再切换模式。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Shift, now, uid
from conftest import POSITION_TEXT, editing, login, upload_qr

ASSIGN = {"name": "统一分配场", "mode": "assign", "capacity": 3,
          "waitlist_quota": 2}


@pytest.fixture
def assign_shift(client, admin, db):
    """一个草稿活动 + 一个 assign 场次（自动带占位岗位）。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": upload_qr(client, admin), "title": f"占位测试 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={**ASSIGN,
                          "periods": [{"start_time": (now() + timedelta(days=6)).isoformat(),
                          "end_time": (now() + timedelta(days=6, hours=4)).isoformat()}]})
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


def _shift_view(client, admin, aid) -> dict:
    """管理者视角的活动详情里唯一那个场次。"""
    r = client.get(f"/api/activities/{aid}", headers=admin)
    assert r.status_code == 200, r.text
    [sh] = r.json()["shifts"]
    return sh


def _roster(client, admin, g) -> dict:
    r = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def test_建assign场次自动带占位岗位(client, admin, assign_shift):
    aid, _g = assign_shift
    sh = _shift_view(client, admin, aid)
    [holder] = sh["positions"]
    assert holder["is_placeholder"] and holder["name"] == "待分配"
    # 名额记在它头上，场次不另存一份
    assert holder["quota"] == 3 and holder["waitlist_quota"] == 2
    assert sh["capacity"] == 3
    assert sh["position_quota_sum"] == 0         # 没有真实岗位


def test_学生报名落在占位岗位上(client, admin, db, published, take_student):
    aid, g = published
    sid = take_student()
    t = login(client, sid)
    r = client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                    headers=t, json={})
    assert r.status_code == 200, r.text
    [row] = [x for x in _roster(client, admin, g)["enrolled"]
             if x["user"]["student_id"] == sid]
    assert row["position_id"] == g.placeholder().id, "报名不该留下没有岗位的记录"


def test_assign场次学生端看到的是说明而不是岗位名(client, db, published,
                                                  take_student):
    """在这一场里它不是一个岗位，是「这一场怎么报」的说明。"""
    aid, g = published
    t = login(client, take_student())
    d = client.get(f"/api/activities/{aid}", headers=t).json()
    pos = d["shifts"][0]["positions"]
    assert len(pos) == 1 and pos[0]["is_placeholder"] is True
    assert pos[0]["display_name"] == "暂不选岗位 · 由负责人后续统一分配"
    assert pos[0]["name"] == "待分配", "管理端仍按「待分配」记账"


def test_有人报名后不能切模式_没人时切到pick删掉占位(client, admin, db, published,
                                             take_student):
    """两种场次的人记在不同的账上，有人之后再切，总有人的去处说不清。"""
    aid, g = published
    with editing(client, admin, aid):
        assert client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                            json={"mode": "pick"}).status_code == 200
    sh = _shift_view(client, admin, aid)
    assert sh["mode"] == "pick" and sh["positions"] == [], "没人报过，占位直接删掉"

    with editing(client, admin, aid):
        assert client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                            json={"mode": "assign", "capacity": 3}).status_code == 200
    t = login(client, take_student())
    assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=t, json={}).status_code == 200
    with editing(client, admin, aid):
        r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin, json={"mode": "pick"})
        assert r.status_code == 409 and "已有 1 人报名" in r.json()["detail"]


def test_名额满了排到占位岗位的候补(client, admin, db, published, take_student):
    aid, g = published
    for _ in range(3):                      # quota=3，占满
        t = login(client, take_student())
        assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                           headers=t, json={}).status_code == 200
    t = login(client, take_student())
    assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=t, json={}).status_code == 200
    rows = _roster(client, admin, g)["waitlist"]
    assert len(rows) == 1
    assert rows[0]["position_id"] == g.placeholder().id, "候补也排在占位岗位上"


# ======================= 截止后：改模式、建岗位、一键分配 =======================

def test_直接建岗位再一键分配_排不下的留在待分配(client, admin, db, published,
                                         take_student):
    aid, g = published
    for _ in range(3):
        t = login(client, take_student())
        assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                           headers=t, json={}).status_code == 200
    holder_id = g.placeholder().id
    with editing(client, admin, aid):
        pid = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                          json={"name": "分拣", "quota": 2, **POSITION_TEXT}).json()["id"]

    sh = _shift_view(client, admin, aid)
    assert sh["mode"] == "assign", "建岗位不用切模式"
    assert sh["capacity"] == 3, "招募人数仍是占位岗位的名额，真实岗位不重复算"

    d = client.post(f"/api/staff/shifts/{g.id}/assign/preview", headers=admin, json={}).json()
    assert [s_["position_id"] for s_ in d["position_stats"]] == [pid], "占位不是分配目标"
    assert [x["position_id"] for x in d["plan"]] == [pid, pid]
    assert len(d["unassigned"]) == 1 and d["unassigned"][0]["reason"] == "可去的岗位名额已满"
    from backend.models import Enrollment
    db.get(Enrollment, d["plan"][0]["enrollment_id"]).allow_reassign = False
    db.commit()
    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": d["plan"]})
    assert r.status_code == 200, r.text
    assert r.json()["unwilling_moved"] == 0, "从待分配分出去不算调剂"
    rows = _roster(client, admin, g)["enrolled"]
    assert sorted(r_["position_id"] for r_ in rows) == sorted([pid, pid, holder_id])
    assert _shift_view(client, admin, aid)["unassigned_count"] == 1


def test_一键分配不动已在岗位上的人(client, admin, db, published, take_student):
    """已经分好的人是管理者定下的，试算只排剩下的空位。"""
    aid, g = published
    enr_ids = []
    for _ in range(3):
        t = login(client, take_student())
        enr_ids.append(client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                                   headers=t, json={}).json()["enrollment_id"])
    with editing(client, admin, aid):
        a = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "甲", "quota": 2, **POSITION_TEXT}).json()["id"]
        b = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "乙", "quota": 2, **POSITION_TEXT}).json()["id"]
    # 最早报名的那位先手动放进乙
    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"source": "manual", "plan": [
                           {"enrollment_id": enr_ids[0], "position_id": b}]}).status_code == 200
    d = client.post(f"/api/staff/shifts/{g.id}/assign/preview", headers=admin, json={}).json()
    assert enr_ids[0] not in [x["enrollment_id"] for x in d["plan"]]
    stats = {s_["position_id"]: s_ for s_ in d["position_stats"]}
    assert stats[b]["existing"] == 1 and stats[a]["existing"] == 0
    assert len(d["plan"]) == 2 and not d["unassigned"]


def test_不需要排班时直接说(client, admin, db, published, open_group, take_student):
    aid, g = published
    # 截止后分配的场次还没建岗位
    r = client.post(f"/api/staff/shifts/{g.id}/assign/preview", headers=admin, json={})
    assert r.status_code == 400 and "建好岗位" in r.json()["detail"]
    with editing(client, admin, aid):
        client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                    json={"name": "甲", "quota": 2, **POSITION_TEXT})
    r = client.post(f"/api/staff/shifts/{g.id}/assign/preview", headers=admin, json={})
    assert r.status_code == 400 and "没有待分配的同学" in r.json()["detail"]
    # 报名即选岗位的场次
    _act, pick = open_group(3)
    r = client.post(f"/api/staff/shifts/{pick.id}/assign/preview", headers=admin, json={})
    assert r.status_code == 400 and "不需要排班" in r.json()["detail"]


def test_待分配不能删(client, admin, db, published):
    aid, g = published
    with editing(client, admin, aid):
        r = client.delete(f"/api/staff/positions/{g.placeholder().id}", headers=admin)
    assert r.status_code == 409 and "报名的入口" in r.json()["detail"]


def test_分到岗位的人仍占本场名额(client, admin, db, published, take_student):
    """人从「待分配」挪到真实岗位，还在这一场里：不能因此再招进人来。"""
    aid, g = published                       # 人数上限 3
    ids = []
    for _ in range(3):
        t = login(client, take_student())
        ids.append(client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                               headers=t, json={}).json()["enrollment_id"])
    with editing(client, admin, aid):
        a = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "甲", "quota": 5, **POSITION_TEXT}).json()["id"]
    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"plan": [{"enrollment_id": i, "position_id": a}
                                      for i in ids]}).status_code == 200
    r = client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()), json={})
    assert r.status_code == 200 and r.json()["status"] == "waitlisted"


def test_截止后分配的场次退选后候补转正(client, admin, db, published, take_student):
    from backend.models import Enrollment
    aid, g = published                       # 人数上限 3，候补 2
    toks = [login(client, take_student()) for _ in range(4)]
    ids = [client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=t, json={}).json()["enrollment_id"] for t in toks]
    assert db.get(Enrollment, ids[3]).status == "waitlisted"
    r = client.delete(f"/api/activities/enrollments/{ids[0]}", headers=toks[0])
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Enrollment, ids[3]).status == "pending_confirm"


def test_待分配人数把占位里的人算进去(client, admin, db, published, take_student):
    """管理者要一眼看出「还有几个人没安排」。"""
    aid, g = published
    for _ in range(2):
        t = login(client, take_student())
        assert client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                           headers=t, json={}).status_code == 200
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    sh = d["shifts"][0]
    assert sh["unassigned_count"] == 2
    assert sh["position_quota_sum"] == 0, "占位不算真实岗位名额"
