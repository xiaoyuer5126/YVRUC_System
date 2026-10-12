"""开放选岗：「截止后统一分配」的场次排完班后转成「报名即选岗位」。

条件是建了真实岗位、「待分配」里一个人都不挂（正式、候补、待确认、待筛选都算）；
转过去之后占位岗位删掉，新来的人按各岗位名额自己挑。不用进编辑态。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, AuditLog, Enrollment, Position, Shift, now, uid
from conftest import POSITION_TEXT, editing, login


@pytest.fixture
def published(client, admin, db):
    """报名中的活动 + 一个 assign 场次（人数上限 3、候补 2）。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"开放选岗 {uid('t')}",
                          "organizer": "校青协", "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "统一分配场", "mode": "assign", "capacity": 3,
                          "waitlist_quota": 2,
                          "start_time": (now() + timedelta(days=6)).isoformat(),
                          "end_time": (now() + timedelta(days=6, hours=4)).isoformat()})
    assert r.status_code == 200, r.text
    db.get(Activity, aid).status = "published"
    db.commit()
    return aid, db.get(Shift, r.json()["id"])


def _enroll(client, aid, g, tok, **body):
    return client.post(f"/api/activities/{aid}/shifts/{g.id}/enroll",
                       headers=tok, json=body)


def _position(client, admin, aid, g, name, quota) -> str:
    with editing(client, admin, aid):
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": name, "quota": quota, **POSITION_TEXT})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _open(client, admin, g):
    return client.post(f"/api/staff/shifts/{g.id}/open-pick", headers=admin)


def test_排完班开放选岗_之后按岗位名额自选(client, admin, db, published, take_student):
    aid, g = published
    holder_id = g.placeholder().id
    toks = [login(client, take_student()) for _ in range(3)]
    ids = [_enroll(client, aid, g, t).json()["enrollment_id"] for t in toks]
    # 第三个人报了又退：「待分配」上留下一条历史记录
    assert client.delete(f"/api/activities/enrollments/{ids[2]}",
                         headers=toks[2]).status_code == 200
    a = _position(client, admin, aid, g, "甲", 2)
    b = _position(client, admin, aid, g, "乙", 1)
    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"plan": [{"enrollment_id": i, "position_id": a}
                                      for i in ids[:2]]}).status_code == 200

    r = _open(client, admin, g)          # 不进编辑态
    assert r.status_code == 200, r.text

    sh = client.get(f"/api/activities/{aid}", headers=admin).json()["shifts"][0]
    assert sh["mode"] == "pick"
    assert sorted(p["id"] for p in sh["positions"]) == sorted([a, b]), "待分配删掉了"
    db.expire_all()
    assert db.get(Position, holder_id) is None
    assert db.get(Enrollment, ids[2]).status == "cancelled", "历史记录还在"
    assert db.get(Enrollment, ids[2]).position_id is None
    assert {db.get(Enrollment, i).position_id for i in ids[:2]} == {a}, "分好的人不动"
    log = (db.query(AuditLog).filter(AuditLog.target_id == g.id)
           .order_by(AuditLog.created_at.desc()).first())
    assert "开放选岗" in log.summary

    # 不选岗位不能报了
    t = login(client, take_student())
    r = _enroll(client, aid, g, t)
    assert r.status_code == 400 and "请选择具体岗位" in r.json()["detail"]
    # 乙空着一个位子；甲 2/2 满了，再报进候补
    r = _enroll(client, aid, g, t, position_id=b)
    assert r.status_code == 200 and r.json()["status"] == "enrolled", r.text
    r = _enroll(client, aid, g, login(client, take_student()), position_id=a)
    assert r.status_code == 200 and r.json()["status"] == "waitlisted", r.text


def test_判完之后才报进来的待筛选报名不被清掉(client, admin, db, published,
                                       take_student, monkeypatch):
    """问卷申请那条路不拿场次锁：判「待分配没人」到删占位之间可能正好有人报进来。
    锁里再核一遍状态，宁可让他重试，也不把一条待筛选的报名静默清掉。"""
    from backend.models import Enrollment, uid
    from backend.routers import staff_activities as sa
    from conftest import student_row

    aid, g = published
    holder = g.placeholder().id
    sid = take_student()
    u = student_row(db, sid)
    enr_id = uid("e")
    db.add(Enrollment(id=enr_id, activity_id=aid, shift_id=g.id, user_id=u.id,
                      position_id=holder, status="applied"))
    db.commit()
    _position(client, admin, aid, g, "甲", 2)
    # 把锁内那道检查伪装成「已经判过、没人」：检查与更新之间有人报了名就是这个样子
    monkeypatch.setattr(sa, "_holder_occupants", lambda _db, _holder: "")

    r = _open(client, admin, g)
    assert r.status_code == 409 and "刚有人报了名" in r.json()["detail"], r.text
    db.expire_all()
    assert db.get(Enrollment, enr_id).position_id == holder, "待筛选的报名被清掉了"


def test_待分配还有人_409(client, admin, db, published, take_student):
    aid, g = published
    _position(client, admin, aid, g, "甲", 3)
    assert _enroll(client, aid, g, login(client, take_student())).status_code == 200
    r = _open(client, admin, g)
    assert r.status_code == 409
    assert "1 人还没分到岗位" in r.json()["detail"]
    assert "一键分配" in r.json()["detail"]
    db.expire_all()
    assert db.get(Shift, g.id).mode == "assign" and g.placeholder() is not None


def test_待分配上有候补_409(client, admin, db, published, take_student):
    """候补排在占位岗位上，一键分配挪不走他们：要先处理。"""
    aid, g = published                       # 人数上限 3
    a = _position(client, admin, aid, g, "甲", 3)
    ids = [_enroll(client, aid, g, login(client, take_student())).json()["enrollment_id"]
           for _ in range(4)]
    assert db.get(Enrollment, ids[3]).status == "waitlisted"
    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"plan": [{"enrollment_id": i, "position_id": a}
                                      for i in ids[:3]]}).status_code == 200
    r = _open(client, admin, g)
    assert r.status_code == 409
    assert "1 人在候补" in r.json()["detail"]


def test_还没建岗位_409(client, admin, published):
    _aid, g = published
    r = _open(client, admin, g)
    assert r.status_code == 409 and "还没建岗位" in r.json()["detail"]


def test_不是统一分配的场次_409(client, admin, open_group):
    _act, g = open_group(3)
    r = _open(client, admin, g)
    assert r.status_code == 409 and "报名即选岗位" in r.json()["detail"]


def test_锁里认出岗位已被删掉(db, published, take_student):
    """锁外落在「待分配」上的报名，排到锁里时它可能已被开放选岗删掉。"""
    from backend import enroll_logic as logic
    from backend.models import User

    aid, g = published
    u = db.query(User).filter(User.student_id == take_student()).one()
    assert not logic.enroll_ledger(db, aid, g.id, g.placeholder().id, u.id, [g]).gone
    assert logic.enroll_ledger(db, aid, g.id, "p_missing", u.id, [g]).gone


def test_圈着待分配的题换成圈这一场的岗位(client, admin, db, published):
    """「待分配」退场后这一场由真实岗位接着收人：只问这一场的题照样问报这一场
    的人，不会因为占位岗位没了就对谁都不显示。"""
    from backend.models import FormQuestion

    aid, g = published
    holder_id = g.placeholder().id
    other = FormQuestion(id=uid("q"), activity_id=aid, kind="text", title="别处",
                         sort=1, applies_to_positions=["p_elsewhere"])
    mine = FormQuestion(id=uid("q"), activity_id=aid, kind="text", title="这一场",
                        sort=0, applies_to_positions=[holder_id])
    db.add_all([mine, other])
    db.commit()
    a = _position(client, admin, aid, g, "甲", 2)
    b = _position(client, admin, aid, g, "乙", 1)
    assert _open(client, admin, g).status_code == 200
    db.expire_all()
    assert sorted(db.get(FormQuestion, mine.id).applies_to_positions) == sorted([a, b])
    assert db.get(FormQuestion, other.id).applies_to_positions == ["p_elsewhere"]
