"""抢报那一刻的公平：毫秒级对时。

详情页的「开没开」要是用截断成整秒的 opens_in：离 reg_start 还差 0.3 秒
时它已经是 0，按钮亮了；报名接口却按精确时间判，手最快的那批人点下去全是
「报名尚未开始」。所以全站只有一处比较（serializers.reg_started）。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import serializers
from backend.models import now
from backend.routers import activities as activities_router
from conftest import mk_shift
from conftest import login


@pytest.fixture
def clock(monkeypatch):
    """把「此刻」钉住：详情页与报名接口看到的是同一个钟。"""
    box = {"t": now()}
    monkeypatch.setattr(serializers, "now", lambda: box["t"])
    monkeypatch.setattr(activities_router, "now", lambda: box["t"])
    return box


def test_差300毫秒就是没开始_到点就开(client, db, open_group, take_student, clock):
    act, g = open_group(3)
    reg_start = now() + timedelta(hours=1)
    act.reg_start = reg_start
    db.commit()
    t = login(client, take_student())
    detail = f"/api/activities/{act.id}"
    enroll = f"/api/activities/{act.id}/shifts/{g.id}/enroll"
    body = {"position_id": g.test_position_id}

    clock["t"] = reg_start - timedelta(milliseconds=300)
    d = client.get(detail, headers=t).json()
    assert d["display_status"] == "upcoming"
    assert d["shifts"][0]["enroll_open"] is False
    assert 0 < d["opens_in_ms"] <= 1000 and d["opens_in_ms"] == 300
    # 整秒数向上取整：没开始就不会是 0
    assert d["opens_in"] == 1
    item = client.get("/api/activities", headers=t,
                      params={"q": act.title, "status": ""}).json()["items"][0]
    assert item["opens_in_ms"] == 300 and item["display_status"] == "upcoming"
    r = client.post(enroll, headers=t, json=body)
    assert r.status_code == 409 and "报名尚未开始" in r.json()["detail"]

    # 到点。上面那次已经把公共部分缓存上了——时间字段不能跟着缓存
    clock["t"] = reg_start
    d = client.get(detail, headers=t).json()
    assert d["display_status"] == "published"
    assert d["shifts"][0]["enroll_open"] is True
    assert d["opens_in_ms"] == 0 and d["opens_in"] == 0
    # 离本场截止还有多久：前端本地开闸时拿它判断哪几场还收人
    deadline = min(act.reg_end, g.start_time)
    assert d["shifts"][0]["closes_in_ms"] == int(
        (deadline - reg_start).total_seconds() * 1000)
    r = client.post(enroll, headers=t, json=body)
    assert r.status_code == 200, r.text


def test_两场之间还收人就是报名中(client, db, open_group, take_student, clock):
    """第一场开过了、后面的场次还收人：显示「报名中」，广场按报名中筛得到。

    标成「进行中」的话广场默认看不见它，后面那几场就招不到人。最后一个
    服务场次开场后才算进行中；排在它后面的培训/彩排不收报名，不算。
    """
    from backend.models import Position, Shift, uid
    from conftest import POSITION_TEXT

    act, g1 = open_group(3)
    g2 = mk_shift(id=uid("g"), activity_id=act.id, name="第二场",
               start_time=g1.start_time + timedelta(days=1),
               end_time=g1.end_time + timedelta(days=1), mode="pick", sort=1)
    db.add(g2)
    db.flush()
    p2 = Position(id=uid("p"), shift_id=g2.id, name="第二场岗位", quota=3,
                  waitlist_quota=3, sort=0, **POSITION_TEXT)
    db.add(p2)
    db.add(mk_shift(id=uid("g"), activity_id=act.id, name="复盘培训",
                 kind="training", start_time=g2.end_time + timedelta(hours=1),
                 end_time=g2.end_time + timedelta(hours=2), sort=2))
    db.commit()
    t = login(client, take_student())
    detail = f"/api/activities/{act.id}"

    def listed(status):
        items = client.get("/api/activities", headers=t,
                           params={"q": act.title, "status": status}).json()["items"]
        return [i["display_status"] for i in items]

    # 第一场正在进行，第二场还没开
    clock["t"] = g1.start_time + timedelta(hours=1)
    d = client.get(detail, headers=t).json()
    assert (d["display_status"], d["display_label"]) == ("published", "报名中")
    assert [s["enroll_open"] for s in d["shifts"] if s["id"] in (g1.id, g2.id)] \
        == [False, True]
    assert listed("published") == ["published"]
    assert listed("ongoing") == []
    r = client.post(f"/api/activities/{act.id}/shifts/{g2.id}/enroll", headers=t,
                    json={"position_id": p2.id})
    assert r.status_code == 200, r.text

    # 最后一个服务场次也开场了：进行中（培训还没到也不算还能报）
    clock["t"] = g2.start_time + timedelta(minutes=1)
    assert client.get(detail, headers=t).json()["display_status"] == "ongoing"
    assert listed("ongoing") == ["ongoing"]
    assert listed("published") == []

    # 两场之间但活动报名已截止：进行中
    clock["t"] = g1.start_time + timedelta(hours=1)
    act.reg_end = g1.start_time
    db.commit()
    assert client.get(detail, headers=t).json()["display_status"] == "ongoing"
    assert listed("ongoing") == ["ongoing"]
    assert listed("published") == []
