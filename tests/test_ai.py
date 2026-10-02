"""AI 起草（预览版）：没开不让用、原文长短与限频、模型输出按白名单收拾。

不连真模型：把 httpx.post 换成假的，验的是平台这一侧怎么问、怎么收。
"""
from __future__ import annotations

import json

import httpx
import pytest

from backend import ai, config, locks
from backend.models import Activity
from conftest import login, make_draft


class _Resp:
    def __init__(self, status: int, content):
        self.status_code = status
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


@pytest.fixture
def model(monkeypatch):
    """开着 AI，并记下发给模型的请求；replies 里放模型这次要回的内容。"""
    monkeypatch.setattr(config, "AI_API_KEY", "sk-test")
    calls, replies = [], []

    def fake_post(url, headers, json, timeout):
        calls.append({"url": url, "headers": headers, "body": json})
        status, content = replies.pop(0)
        return _Resp(status, content)

    monkeypatch.setattr(ai.httpx, "post", fake_post)
    # 限频计数在 Redis 里跨用例、跨轮次累积，一小时内多跑几遍就会撞上限：这里不计
    monkeypatch.setattr(locks, "rate_count", lambda key: 0)
    monkeypatch.setattr(locks, "note_rate", lambda key, window: None)
    return calls, replies


TEXT = "校运动会开幕式志愿者招募，10 月 18 日上午在世纪馆，需要检录引导 6 人。"


def test_没配key就不能用_常量里也说没开(client, admin, monkeypatch):
    monkeypatch.setattr(config, "AI_API_KEY", "")
    assert client.get("/api/auth/constants", headers=admin).json()["ai_enabled"] is False
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 409


def test_学生不能用(client, take_student, model):
    r = client.post("/api/staff/ai/activity-draft", headers=login(client, take_student()),
                    json={"text": TEXT})
    assert r.status_code == 403


def test_活动草稿按白名单收拾_空值不回(client, admin, model):
    calls, replies = model
    replies.append((200, json.dumps({
        "title": "校运动会开幕式志愿服务", "organizer": "校团委", "location": "世纪馆",
        "intro": "开幕式现场引导", "volunteer_requirement": "",
        "volunteer_benefit": "",
        "contacts": [{"name": "王老师", "phone": "13900000000"}, {"name": "", "phone": ""}],
        "recruit_mode": "assign",                      # 不认识的类型丢掉
        "recruit_campuses": ["中关村校区", "海淀校区"],   # 不在可选集合里的丢掉
        "recruit_stages": ["本科生"],
        "reg_start": "", "reg_end": "2026-10-15 23:59",
        "notes": ["原文没写报名开始时间"],
        "password": "不该透传的东西",
    }, ensure_ascii=False)))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["draft"] == {
        "title": "校运动会开幕式志愿服务", "organizer": "校团委", "location": "世纪馆",
        "intro": "开幕式现场引导",
        "contacts": [{"name": "王老师", "phone": "13900000000"}],
        "recruit_campuses": ["中关村校区"], "recruit_stages": ["本科生"],
        "reg_end": "2026-10-15T23:59",
    }
    assert d["notes"] == ["原文没写报名开始时间"]
    # 发出去的请求：key 在头里，要求 json 输出，原文原样作为用户消息
    sent = calls[0]
    assert sent["url"] == f"{config.AI_BASE_URL}/chat/completions"
    assert sent["headers"]["Authorization"] == "Bearer sk-test"
    assert sent["body"]["response_format"] == {"type": "json_object"}
    assert sent["body"]["messages"][1]["content"] == TEXT
    assert "json" in sent["body"]["messages"][0]["content"]


def test_场次草稿_岗位字段收拾好(client, admin, db, model):
    calls, replies = model
    aid = make_draft(client, admin)
    replies.append((200, json.dumps({"shifts": [{
        "name": "开幕式当天", "start_time": "2026-10-18 07:30", "end_time": "不知道",
        "location": "",
        "positions": [
            {"name": "检录引导", "quota": "6", "service_task": "引导运动员检录",
             "attire": "issued", "attire_note": "",            # 发服装却没说怎么领：按不需要放
             "needs_leader": "yes",                             # 不是 true 就当 false
             "requirement_tags": {"性别": "女", "学院": ["文学院"], "校区": ["中关村校区"]}},
            {"name": "", "quota": 0, "service_task": ""},
        ]}], "notes": []}, ensure_ascii=False)))
    r = client.post(f"/api/staff/ai/activities/{aid}/structure-draft", headers=admin,
                    json={"text": TEXT})
    assert r.status_code == 200, r.text
    g = r.json()["shifts"][0]
    assert g["name"] == "开幕式当天" and g["start_time"] == "2026-10-18T07:30"
    assert g["end_time"] == ""                                  # 认不出的留空，让人填
    p1, p2 = g["positions"]
    assert p1["quota"] == 6 and p1["attire"] == "none" and p1["needs_leader"] is False
    assert p1["requirement_tags"] == {"性别": ["女"], "校区": ["中关村校区"]}
    assert p2["name"] == "志愿者" and p2["quota"] == 1 and p2["service_task"] == ""
    # 活动信息与已有场次带给了模型
    title = db.get(Activity, aid).title
    assert title in calls[0]["body"]["messages"][0]["content"]
    assert "上午场" in calls[0]["body"]["messages"][0]["content"]


def test_原文长短与限频(client, admin, model, monkeypatch):
    calls, replies = model
    assert client.post("/api/staff/ai/activity-draft", headers=admin,
                       json={"text": "太短"}).status_code == 400
    r = client.post("/api/staff/ai/activity-draft", headers=admin,
                    json={"text": "长" * (config.AI_INPUT_MAX + 1)})
    assert r.status_code == 400 and "太长" in r.json()["detail"]
    monkeypatch.setattr(locks, "rate_count", lambda key: config.AI_HOURLY_LIMIT)
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 429
    assert not calls                     # 都在打到模型之前拦下


@pytest.mark.parametrize("status, content, word", [
    (401, "", "key"), (402, "", "余额"), (500, "", "暂时不可用"),
    (200, "不是 json", "不完整"), (200, "[1, 2]", "不完整"),
])
def test_模型出错给人看得懂的话(client, admin, model, status, content, word):
    _calls, replies = model
    replies.append((status, content))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 502 and word in r.json()["detail"]


def test_超时与连不上(client, admin, model, monkeypatch):
    def boom(exc):
        def _post(*a, **k):
            raise exc
        return _post
    monkeypatch.setattr(ai.httpx, "post", boom(httpx.ReadTimeout("slow")))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 502 and "太久" in r.json()["detail"]
    monkeypatch.setattr(ai.httpx, "post", boom(httpx.ConnectError("down")))
    r = client.post("/api/staff/ai/activity-draft", headers=admin, json={"text": TEXT})
    assert r.status_code == 502 and "连不上" in r.json()["detail"]
