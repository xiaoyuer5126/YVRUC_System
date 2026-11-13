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
from conftest import POSITION_TEXT, login, student_row, upload_qr

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
    r = _enroll(client, act, g, h)
    assert r.status_code == 403 and "招募范围" in r.json()["detail"]
    assert STAGE_B in r.json()["detail"]


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


def test_范围外的人广场上看不到详情也进不去(client, admin, db, scoped):
    """看得见报不了只会多一次被拦的失望；转发来的链接则要说清楚原因。"""
    act, _g, _u, h = scoped
    db.get(Activity, act.id).recruit_campuses = [CAMPUS_B]
    db.commit()
    assert act.id not in {a["id"] for a in
                          client.get("/api/activities", headers=h).json()["items"]}
    r = client.get(f"/api/activities/{act.id}", headers=h)
    assert r.status_code == 403 and CAMPUS_B in r.json()["detail"]
    # 管理者不受范围限制：要从广场进去核对志愿者看到的样子
    assert act.id in {a["id"] for a in
                      client.get("/api/activities", headers=admin).json()["items"]}
    assert client.get(f"/api/activities/{act.id}", headers=admin).status_code == 200


def test_缩小范围时已报名的范围外志愿者要先过管理者的眼(client, admin, db, scoped):
    """不自动退任何人：拦下来列名单，管理者明确说保留才改。"""
    act, g, u, h = scoped
    assert _enroll(client, act, g, h).status_code == 200
    r = client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                     json={"recruit_campuses": [CAMPUS_B]})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "OUTSIDERS_ENROLLED"
    assert [o["student_id"] for o in detail["outsiders"]] == [u.student_id]
    db.expire_all()
    assert db.get(Activity, act.id).recruit_campuses == []

    r = client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                     json={"recruit_campuses": [CAMPUS_B], "keep_outsiders": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).recruit_campuses == [CAMPUS_B]
    # 保留下来的人自己仍然能看到这场活动
    assert client.get(f"/api/activities/{act.id}", headers=h).status_code == 200


# ======================= 活动级：招募学院 =======================
# 学院是统一身份认证给的原文，照原样用；选项也从档案里来，不并写死的清单

REAL_COLLEGE = "统计与大数据学院"


def _square_ids(client, h, act):
    r = client.get("/api/activities", headers=h, params={"q": act.title})
    return {a["id"] for a in r.json()["items"]}


def _set_colleges(client, admin, act, colleges, **extra):
    return client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                        json={"recruit_colleges": colleges, **extra})


def test_不在招募学院的人广场看不到详情进不去也报不了(client, admin, db, scoped):
    act, g, u, h = scoped
    assert _set_colleges(client, admin, act, [REAL_COLLEGE]).status_code == 200
    assert act.id not in _square_ids(client, h, act)
    r = client.get(f"/api/activities/{act.id}", headers=h)
    assert r.status_code == 403
    assert REAL_COLLEGE in r.json()["detail"] and u.college in r.json()["detail"]
    r = _enroll(client, act, g, h)
    assert r.status_code == 403 and "招募范围" in r.json()["detail"]


def test_在招募学院内照常报名(client, admin, db, scoped):
    act, g, u, h = scoped
    u.college = REAL_COLLEGE
    db.commit()
    assert _set_colleges(client, admin, act,
                         [REAL_COLLEGE, "新闻学院"]).status_code == 200
    assert act.id in _square_ids(client, h, act)
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["recruit_colleges"] == [REAL_COLLEGE, "新闻学院"]
    assert d["recruit_scope_text"] == f"学院：{REAL_COLLEGE}/新闻学院"
    assert _enroll(client, act, g, h).status_code == 200


def test_学院留空即不限(client, admin, db, scoped):
    act, g, _u, h = scoped
    assert _set_colleges(client, admin, act, []).status_code == 200
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["recruit_colleges"] == [] and d["recruit_scope_text"] == "面向全校"
    assert _enroll(client, act, g, h).status_code == 200


def test_三个维度一起写进范围文案(client, admin, db, scoped):
    act, _g, _u, h = scoped
    r = client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                     json={"recruit_campuses": [CAMPUS_A],
                           "recruit_stages": [STAGE_A],
                           "recruit_colleges": ["明理书院"]})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["recruit_scope_text"] == \
        f"校区：{CAMPUS_A}；学段：{STAGE_A}；学院：明理书院"


def test_按学院缩小范围时已报名的人要先过管理者的眼且保留后仍看得见(
        client, admin, db, scoped):
    act, g, u, h = scoped
    assert _enroll(client, act, g, h).status_code == 200
    r = _set_colleges(client, admin, act, [REAL_COLLEGE])
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "OUTSIDERS_ENROLLED"
    assert [o["student_id"] for o in detail["outsiders"]] == [u.student_id]
    assert "学院" in detail["outsiders"][0]["reason"]
    db.expire_all()
    assert db.get(Activity, act.id).recruit_colleges == []

    r = _set_colleges(client, admin, act, [REAL_COLLEGE], keep_outsiders=True)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/activities/{act.id}", headers=h).status_code == 200
    assert act.id in _square_ids(client, h, act)


def test_学院取值去空白去重且限长(client, admin, db, scoped):
    act, _g, _u, _h = scoped
    r = _set_colleges(client, admin, act,
                      [f" {REAL_COLLEGE} ", REAL_COLLEGE, "", "  "])
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).recruit_colleges == [REAL_COLLEGE]
    r = _set_colleges(client, admin, act, ["学" * 65])
    assert r.status_code == 400 and "过长" in r.json()["detail"]


def test_创建活动时就能带上学院(client, admin, db):
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": upload_qr(client, admin), "title": f"学院活动 {uid('t')}",
                          "organizer": "青协", "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "recruit_colleges": [REAL_COLLEGE, REAL_COLLEGE],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    assert db.get(Activity, r.json()["id"]).recruit_colleges == [REAL_COLLEGE]


def test_复制活动带上招募学院(client, admin, db, scoped):
    act, _g, _u, _h = scoped
    assert _set_colleges(client, admin, act, [REAL_COLLEGE]).status_code == 200
    r = client.post(f"/api/staff/activities/{act.id}/copy", headers=admin,
                    json={"shift_days": 7, "title": f"学院副本 {uid('t')}",
                          "contact_group_qr": upload_qr(client, admin)})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{r.json()['id']}", headers=admin).json()
    assert d["recruit_colleges"] == [REAL_COLLEGE]


def test_学院选项汇总已有用户的学院并上兜底清单(client, admin, db, scoped):
    _act, _g, u, h = scoped
    u.college = REAL_COLLEGE
    db.commit()
    r = client.get("/api/staff/colleges", headers=admin)
    assert r.status_code == 200
    got = r.json()["colleges"]
    assert REAL_COLLEGE in got          # 档案里出现的学院就是选项，不管它在不在演示清单里
    assert "" not in got and len(got) == len(set(got))
    assert got == sorted(got)
    # 学生用不着这份选项
    assert client.get("/api/staff/colleges", headers=h).status_code == 403


def test_岗位条件的学院也不卡演示清单(client, admin, db, scoped):
    act, g, _u, _h = scoped
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                    json={"name": "新岗", "quota": 2, **POSITION_TEXT,
                          "requirement_tags": {"学院": [REAL_COLLEGE]}})
    assert r.status_code == 200, r.text


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
                          "requirement_tags": {"性别": ["不限男女"]}})
    assert r.status_code == 400 and "不在可选范围内" in r.json()["detail"]


def test_场次岗位各设各的条件(client, admin, db, scoped):
    """和两个表单一致：校区、学段设在场次上，性别、学院设在岗位上。
    放错层的话页面上改不了也看不见，只在报名时莫名其妙地拦人。"""
    act, g, _u, _h = scoped
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                    json={"name": "新岗", "quota": 2, **POSITION_TEXT,
                          "requirement_tags": {"校区": [CAMPUS_A]}})
    assert r.status_code == 400 and "请设在场次上" in r.json()["detail"]
    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                     json={"requirement_tags": {"性别": ["女"]}})
    assert r.status_code == 400 and "请设在岗位上" in r.json()["detail"]
