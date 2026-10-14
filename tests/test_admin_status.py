"""管理端与数据总览的状态口径：与广场同一套按时间算的展示状态。

已发布但还没到报名时间的活动，管理页上写「未开始报名」，不写「报名中」；状态
筛选也照这套走。数据总览的「报名中活动」只数此刻真能报的。发布确认与发布成功
那句「学生什么时候能报」由服务端按报名开始时间给（见 serializers）。
"""
from __future__ import annotations

from datetime import timedelta

from backend.models import now
from conftest import login

COUNCIL = "2025003"    # 张远航，会长团：进得了数据总览


def _one(client, headers, act) -> dict:
    """管理端列表里这一场的那一条。"""
    r = client.get("/api/staff/activities", headers=headers,
                   params={"q": act.title})
    assert r.status_code == 200, r.text
    items = [a for a in r.json()["items"] if a["id"] == act.id]
    assert items, f"「{act.title}」不在管理端列表里"
    return items[0]


def _listed(client, headers, act, status: str) -> dict:
    r = client.get("/api/staff/activities", headers=headers,
                   params={"q": act.title, "status": status})
    assert r.status_code == 200, r.text
    return r.json()


def _card(client, headers) -> int:
    r = client.get("/api/staff/overview", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["cards"]["activities_published"]


def test_管理端列表按报名时间标状态_筛选也对得上(client, db, admin, open_group):
    """只看 status 的话，报名明天才开始的活动在管理页上写着「报名中」，跟学生
    在广场上看到的那条对不上。状态列与筛选都按当前时刻算。"""
    act, _g = open_group(3)
    act.reg_start = now() + timedelta(days=2)
    db.commit()
    one = _one(client, admin, act)
    assert one["status"] == "published"          # 库里的状态没变
    assert (one["display_status"], one["display_label"]) == ("upcoming", "未开始报名")

    up = _listed(client, admin, act, "upcoming")
    assert act.id in [a["id"] for a in up["items"]] and up["total"] == 1
    pub = _listed(client, admin, act, "published")
    assert act.id not in [a["id"] for a in pub["items"]] and pub["total"] == 0

    # 到点之后两边一起翻过来
    act.reg_start = now() - timedelta(hours=1)
    db.commit()
    assert _one(client, admin, act)["display_label"] == "报名中"
    assert _listed(client, admin, act, "published")["total"] == 1
    assert _listed(client, admin, act, "upcoming")["total"] == 0


def test_发布提示按报名开始时间说(client, db, admin, open_group):
    """发布成功的那句话由服务端给：报名还没到点就说清哪一刻开始，一律写
    「现在可以报名」的话，学生点开却报不了。"""
    act, _g = open_group(3)
    act.status = "approved"
    act.reg_start, act.reg_end = now() + timedelta(days=2), now() + timedelta(days=3)
    db.commit()
    r = client.post(f"/api/staff/activities/{act.id}/publish", headers=admin)
    assert r.status_code == 200, r.text
    hint = r.json()["hint"]
    assert hint == f"报名将于 {act.reg_start:%m-%d %H:%M} 开始"
    # 活动管理页的发布确认读同一个字段（列表里的 publish_hint）
    assert _one(client, admin, act)["publish_hint"] == hint

    # 报名时间已经过了的活动，才是「现在可以报名」
    later, _g2 = open_group(3)
    later.status = "approved"
    later.reg_start, later.reg_end = now() - timedelta(hours=1), now() + timedelta(days=1)
    db.commit()
    r = client.post(f"/api/staff/activities/{later.id}/publish", headers=admin)
    assert r.json()["hint"] == "现在可以报名"
    assert _one(client, admin, later)["publish_hint"] == "现在可以报名"


def test_报名中活动只数此刻真能报的(client, db, open_group):
    """数据总览那张卡与广场上按「报名中」筛出来的一致：已发布但还没到报名时间
    的不算。"""
    council = login(client, COUNCIL)
    base = _card(client, council)
    act, _g = open_group(3)
    act.reg_start = now() + timedelta(days=1)
    db.commit()
    assert _card(client, council) == base        # 还没到报名时间，不算

    act.reg_start = now() - timedelta(hours=1)
    db.commit()
    assert _card(client, council) == base + 1
