"""招募范围与条件要求：校区、学段。

三层筛人，粒度从粗到细：
  · 活动级「招募范围」——整场一刀切；
  · 场次级「条件要求」——assign 模式下报名时还没岗位，只能靠这一层；
  · 岗位级「条件要求」——性别/学院那套，同一个匹配规则。
不满足的人在报名接口就被拦下，不是等分配时才发现。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import config
from backend.models import Activity, Shift, User, now, uid
from conftest import POSITION_TEXT, login, student_row

CAMPUS_A, CAMPUS_B = config.CAMPUSES[0], config.CAMPUSES[1]
STAGE_A, STAGE_B = config.STAGES[0], config.STAGES[1]


@pytest.fixture
def scoped(client, db, open_group, take_student):
    """一个报名中的活动 + 一名校区/学段已知的学生。"""
    act, g = open_group(3, day_offset=0, start_in_hours=5)
    sid = take_student()
    u = student_row(db, sid)
    u.campus, u.stage = CAMPUS_A, STAGE_A
    db.commit()
    return act, g, u, login(client, sid)


def _enroll(client, act, g, headers):
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=headers, json={"position_id": g.test_position_id})


# ======================= 活动级：招募范围 =======================

def test_不在招募校区的人报不了(client, db, scoped):
    act, g, _u, h = scoped
    db.get(Activity, act.id).recruit_campuses = [CAMPUS_B]
    db.commit()
    r = _enroll(client, act, g, h)
    assert r.status_code == 403 and "招募范围" in r.json()["detail"]
    assert CAMPUS_B in r.json()["detail"]


def test_不在招募学段的人报不了(client, db, scoped):
    act, g, _u, h = scoped
    db.get(Activity, act.id).recruit_stages = [STAGE_B]
    db.commit()
    assert _enroll(client, act, g, h).status_code == 403


def test_在范围内照常报名(client, db, scoped):
    act, g, _u, h = scoped
    row = db.get(Activity, act.id)
    row.recruit_campuses = [CAMPUS_A, CAMPUS_B]
    row.recruit_stages = [STAGE_A]
    db.commit()
    assert _enroll(client, act, g, h).status_code == 200


def test_留空即不限(client, db, scoped):
    act, g, _u, h = scoped
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["recruit_scope_text"] == "面向全校"
    assert _enroll(client, act, g, h).status_code == 200


def test_招募范围写在详情里(client, db, scoped):
    """不在范围内的人不该报到一半才被拦下，页面上就要看得见。"""
    act, _g, _u, h = scoped
    row = db.get(Activity, act.id)
    row.recruit_campuses = [CAMPUS_A]
    row.recruit_stages = [STAGE_A, STAGE_B]
    db.commit()
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["recruit_scope_text"] == \
        f"校区：{CAMPUS_A}；学段：{STAGE_A}/{STAGE_B}"


# ======================= 场次级：条件要求 =======================

def test_场次条件拦人(client, db, scoped):
    act, g, _u, h = scoped
    db.get(Shift, g.id).requirement_tags = {"学段": [STAGE_B]}
    db.commit()
    r = _enroll(client, act, g, h)
    assert r.status_code == 403 and "场次" in r.json()["detail"]


def test_场次条件满足就放行(client, db, scoped):
    act, g, _u, h = scoped
    db.get(Shift, g.id).requirement_tags = {"校区": [CAMPUS_A],
                                            "学段": [STAGE_A]}
    db.commit()
    assert _enroll(client, act, g, h).status_code == 200


def test_场次条件在详情里给出(client, db, scoped):
    act, g, _u, h = scoped
    db.get(Shift, g.id).requirement_tags = {"校区": [CAMPUS_A]}
    db.commit()
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["shifts"][0]["requirement_desc"] == f"校区：{CAMPUS_A}"


# ======================= 取值必须在清单内 =======================

def test_招募范围只认清单里的值(client, admin, db, scoped):
    """条件是拿去跟档案逐字比对的，放错别字进来就是谁都报不了且看不出为什么。"""
    act, _g, _u, _h = scoped
    r = client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                     json={"recruit_campuses": ["火星校区"]})
    assert r.status_code == 400 and "不在可选范围内" in r.json()["detail"]


def test_场次条件只认已知标签(client, admin, db, scoped):
    act, g, _u, _h = scoped
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                     json={"requirement_tags": {"星座": ["天秤"]}})
    assert r.status_code == 400 and "未知的条件标签" in r.json()["detail"]


def test_岗位条件也走同一套校验(client, admin, db, scoped):
    act, g, _u, _h = scoped
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                    json={"name": "新岗", "quota": 2, **POSITION_TEXT,
                          "requirement_tags": {"学段": ["幼儿园"]}})
    assert r.status_code == 400 and "不在可选范围内" in r.json()["detail"]
