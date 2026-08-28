"""违规登记与限制准入建议，逐条对照《志愿者服务规范·限制准入标准》。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import violations
from backend.models import (Activity, ActivityManager, User,
                            ViolationRecord, now, uid)
from conftest import ADMIN, QINGXIE, login


@pytest.fixture
def act_of_type(db):
    """按类型造活动，违规登记要挂在具体活动上。"""
    def _make(type_code: str):
        admin = db.query(User).filter(User.student_id == ADMIN).one()
        act = Activity(id=uid("act"), title=f"{type_code}类活动 {uid('t')}",
                       type=type_code, intro="x", location="x",
                       contacts=[{"name": "测试", "phone": "13900000000"}],
                       qa_group_qr="x", status="finished",
                       reg_start=now() - timedelta(days=9),
                       reg_end=now() - timedelta(days=8), created_by=admin.id)
        db.add(act)
        db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                               user_id=admin.id, added_by=admin.id))
        db.commit()
        return act
    return _make


@pytest.fixture
def student(db, take_student):
    return db.query(User).filter(User.student_id == take_student()).one()


def record(db, user, type_code, kind, times=1):
    """直接写历史违规，用于把人推到阶梯的某一级。"""
    for _ in range(times):
        db.add(ViolationRecord(id=uid("vio"), user_id=user.id,
                               activity_id=None, activity_title="历史",
                               activity_type=type_code, kind=kind))
    db.commit()


def test_迟到早退_AB类三级阶梯(db, student):
    """1-2次禁CD；3-4次加禁B；5次及以上加禁A。"""
    s = violations.suggest(db, student, "A", "late")
    assert s["types"] == ["C", "D"]

    record(db, student, "A", "late", times=2)      # 本次是第 3 次
    s = violations.suggest(db, student, "B", "late")
    assert s["types"] == ["B", "C", "D"]

    record(db, student, "B", "late", times=2)      # 本次是第 5 次
    s = violations.suggest(db, student, "A", "late")
    assert s["types"] == ["A", "B", "C", "D"]


def test_CD类迟到早退首次即禁CD并提示可追加(db, student):
    s = violations.suggest(db, student, "C", "late")
    assert s["types"] == ["C", "D"]
    assert any("追加" in n for n in s["notes"])


def test_CD类无故请假区分有无证明(db, student):
    """无证明的病假/事假首次即禁 ABCD；有证明但影响活动的只禁 D。"""
    assert violations.suggest(db, student, "D", "leave",
                              has_proof=False)["types"] == ["A", "B", "C", "D"]
    assert violations.suggest(db, student, "D", "leave",
                              has_proof=True)["types"] == ["D"]


def test_失联与消极志愿共用同一条阶梯(db, student):
    """规范里两者属同一条款，累计次数要合并数。"""
    record(db, student, "A", "contact")            # 已有 1 次失联
    s = violations.suggest(db, student, "A", "passive")   # 本次消极志愿 = 第 2 次
    assert s["history_count"] == 2
    assert s["types"] == ["B", "C", "D"]


def test_CD类失联首次即禁全部类型(db, student):
    s = violations.suggest(db, student, "C", "contact")
    assert s["types"] == ["A", "B", "C", "D"]
    assert any("特殊情况" in n for n in s["notes"])


def test_其他类型不给自动阶梯(db, student):
    s = violations.suggest(db, student, "A", "other")
    assert s["types"] == []
    assert any("人工" in n or "自行" in n for n in s["notes"])


def test_既有限制会被并入建议(db, student):
    student.restricted_types = ["D"]
    student.restricted_until = now() + timedelta(days=200)
    db.commit()
    s = violations.suggest(db, student, "A", "late")
    assert s["types"] == ["C", "D"]                 # D 并入，不会丢
    assert s["until"] > now() + timedelta(days=190)  # 更晚的截止日胜出


def test_登记接口落库并生效(client, db, act_of_type, student):
    """确认后的口径写进用户，且拦得住报名。"""
    qingxie = login(client, QINGXIE)
    act = act_of_type("C")

    r = client.post("/api/staff/violations/preview", headers=qingxie,
                    json={"user_id": student.id, "activity_id": act.id,
                          "kind": "contact"})
    assert r.status_code == 200
    body = r.json()
    assert body["suggest_types"] == ["A", "B", "C", "D"]
    assert "C/D类" in body["basis"]

    r = client.post("/api/staff/violations", headers=qingxie,
                    json={"user_id": student.id, "activity_id": act.id,
                          "kind": "contact", "detail": "活动当天失联，电话不通",
                          "types": body["suggest_types"],
                          "until": body["suggest_until"]})
    assert r.status_code == 200

    db.refresh(student)
    assert student.restricted_types == ["A", "B", "C", "D"]

    history = client.get(f"/api/staff/users/{student.id}/violations",
                         headers=qingxie).json()
    assert len(history) == 1 and history[0]["kind_label"] == "无法取得联系"


def test_登记必须填情况简述(client, act_of_type, student):
    qingxie = login(client, QINGXIE)
    act = act_of_type("A")
    r = client.post("/api/staff/violations", headers=qingxie,
                    json={"user_id": student.id, "activity_id": act.id,
                          "kind": "late", "detail": "   ",
                          "types": ["C", "D"],
                          "until": (now() + timedelta(days=90)).isoformat()})
    assert r.status_code == 400


def test_老师不能登记违规(client, initiator, act_of_type, student):
    """限制准入是全平台口径，只有管理员/青协能落。"""
    act = act_of_type("A")
    r = client.post("/api/staff/violations", headers=initiator,
                    json={"user_id": student.id, "activity_id": act.id,
                          "kind": "late", "detail": "迟到 20 分钟",
                          "types": ["C", "D"],
                          "until": (now() + timedelta(days=90)).isoformat()})
    assert r.status_code == 403
