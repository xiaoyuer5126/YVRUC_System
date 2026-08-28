"""报名窗口：每个场次在自己开场时关闸，活动的报名截止不得晚于最后一场开始。

活动的报名窗可以跨很多天（支教连办八周），所以「活动还在报名期」不等于
「每一场都还收人」。两条规则合起来才说得通：
  · 场次级：min(活动报名截止, 本场开场) —— 开过场的那一场谁也报不进去；
  · 活动级：报名截止 ≤ 最后一场开始 —— 再往后是一段谁都报不进来的死窗口。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Shift, now, uid
from conftest import POSITION_TEXT, login


@pytest.fixture
def two_shifts(client, admin, db):
    """一个草稿 + 两个服务场次（第 3 天、第 9 天），报名截止在第 5 天。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": f"报名窗口 {uid('t')}", "organizer": "校青协",
                          "type": "A", "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    aid = r.json()["id"]
    for day in (3, 9):
        g = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                        json={"name": f"第 {day} 天", "mode": "pick",
                              "start_time": (now() + timedelta(days=day)).isoformat(),
                              "end_time": (now() + timedelta(days=day, hours=3)).isoformat()}
                        ).json()["id"]
        client.post(f"/api/staff/shifts/{g}/positions", headers=admin,
                    json={"name": "引导", "quota": 3, **POSITION_TEXT})
    return aid


# ======================= 活动级：不得晚于最后一场开始 =======================

def test_报名截止晚于最后一场开始就发不出去(client, admin, db, two_shifts):
    act = db.get(Activity, two_shifts)
    act.reg_end = now() + timedelta(days=20)      # 比第 9 天那场还晚
    db.commit()

    r = client.get(f"/api/staff/activities/{two_shifts}/submit-check", headers=admin)
    assert r.status_code == 400 and "晚于最后一个场次" in r.json()["detail"]


def test_截止落在两场之间是允许的(client, admin, db, two_shifts):
    """第一场开过了、后面还有场次，这时继续收人是正常需求，不该拦。"""
    r = client.patch(f"/api/staff/activities/{two_shifts}", headers=admin,
                     json={"reg_end": (now() + timedelta(days=7)).isoformat()})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/staff/activities/{two_shifts}/submit-check",
                      headers=admin).status_code == 200


def test_改基本信息时也拦(client, admin, two_shifts):
    r = client.patch(f"/api/staff/activities/{two_shifts}", headers=admin,
                     json={"reg_end": (now() + timedelta(days=30)).isoformat()})
    assert r.status_code == 400 and "晚于最后一个场次" in r.json()["detail"]


def test_恢复报名同样按最后一场算(client, admin, db, two_shifts):
    act = db.get(Activity, two_shifts)
    act.status = "closed"
    db.commit()

    r = client.post(f"/api/staff/activities/{two_shifts}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(days=30)).isoformat()})
    assert r.status_code == 400 and "最后一个场次" in r.json()["detail"]

    # 第一场之后、最后一场之前：还有场次能补人，允许恢复
    r = client.post(f"/api/staff/activities/{two_shifts}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(days=6)).isoformat()})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, two_shifts).status == "published"


def test_所有场次都开过了就不给恢复报名(client, admin, db, two_shifts):
    act = db.get(Activity, two_shifts)
    act.status = "closed"
    for g in act.shifts:
        g.start_time = now() - timedelta(hours=5)
        g.end_time = now() - timedelta(hours=2)
    db.commit()

    r = client.post(f"/api/staff/activities/{two_shifts}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(days=1)).isoformat()})
    assert r.status_code == 409 and "所有场次都已开始" in r.json()["detail"]


# ======================= 场次级：开场即关闸 =======================

def test_场次开过了就报不进去(client, db, open_group, take_student):
    """活动的报名截止还远在天边，但这一场昨天就结束了。

    报进去的后果是死结：签不了到，也退不了选（活动已开始一律不许退），
    最后挂在名单上算一个未签到。
    """
    act, g = open_group(3)
    g.start_time = now() - timedelta(days=1, hours=3)
    g.end_time = now() - timedelta(days=1)
    db.commit()
    assert act.reg_end > now()                    # 活动整体还在报名期

    t = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=t, json={"position_id": g.test_position_id})
    assert r.status_code == 409 and "开场" in r.json()["detail"]


def test_同一个活动里没开场的那一场照常报名(client, db, open_group, take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=5)
    past = Shift(id=uid("g"), activity_id=act.id, name="昨天那场",
                 start_time=now() - timedelta(days=1, hours=3),
                 end_time=now() - timedelta(days=1), mode="assign",
                 capacity=5, sort=1)
    db.add(past)
    db.commit()

    t = login(client, take_student())
    assert client.post(f"/api/activities/{act.id}/shifts/{past.id}/enroll",
                       headers=t, json={}).status_code == 409
    assert client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=t,
                       json={"position_id": g.test_position_id}).status_code == 200


def test_详情里给出每场自己的报名闸门(client, db, open_group, take_student):
    """前端据此灰按钮，不能让人点了才知道这一场早过了。"""
    act, g = open_group(3, day_offset=0, start_in_hours=5)
    past = Shift(id=uid("g"), activity_id=act.id, name="昨天那场",
                 start_time=now() - timedelta(days=1, hours=3),
                 end_time=now() - timedelta(days=1), mode="assign",
                 capacity=5, sort=1)
    db.add(past)
    db.commit()

    t = login(client, take_student())
    shifts = {s["name"]: s
              for s in client.get(f"/api/activities/{act.id}", headers=t).json()["shifts"]}
    assert shifts["昨天那场"]["enroll_open"] is False
    assert shifts["昨天那场"]["started"] is True
    assert shifts["测试场次"]["enroll_open"] is True
    # 活动的报名截止远在一年后，闸门要按本场开场时间算
    assert shifts["测试场次"]["enroll_deadline"][:10] == \
        f"{(now() + timedelta(hours=5)):%Y-%m-%d}"
