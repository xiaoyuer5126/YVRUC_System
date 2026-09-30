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
