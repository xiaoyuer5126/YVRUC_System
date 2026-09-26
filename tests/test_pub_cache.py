"""学生端活动页的公共部分缓存（backend/pubcache.py）。

缓存的是「人人都一样」的那部分，最怕的是把因人而异的东西存进去、再发给
下一个人：外人看到培训内容和组长手机号，或者报了名的人看到的是别人的
「未报名」。这里逐条钉住：
  · 谁先把缓存填上都一样：外人看不到培训内容/组长，报了名的人照样看得到；
  · 我的报名状态每次现算，缓存旧了也不影响；
  · 管理者改完、提交之后，学生立刻看到新的；
  · 开缓存和关缓存，同一个人看到的 JSON 一模一样；
  · Redis 挂了照常出页面。
"""
from __future__ import annotations

import pytest
import redis

from backend import locks, pubcache, serializers
from backend.database import SessionLocal
from backend.models import Enrollment, Position, now, uid
from conftest import editing, login, student_row
from test_query_budget import build


def _detail(client, headers, aid):
    r = client.get(f"/api/activities/{aid}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _positions(d):
    return {p["id"]: p for g in d["shifts"] for p in g["positions"]}


SECRET = "集合口令：青松"


@pytest.fixture
def secret_case(db, take_student):
    """build 出来的活动，把培训内容换成一句一看就认得出的「秘密」。"""
    def _make():
        case = build(db, take_student, per_shift=2)
        for p in db.query(Position).join(Position.shift).filter_by(
                activity_id=case["id"]):
            p.training_note = SECRET
        db.commit()
        return case
    return _make


def _assert_outsider_view(d):
    for p in _positions(d).values():
        assert p["training_visible"] is False
        assert p["training_note"] == "" and p["attachments"] == []
        assert p["leader_name"] == "" and p["leader_contact"] is None
    assert all("my_enrollment" not in g for g in d["shifts"])


def _assert_member_view(d):
    # 报在第一个场次的第一个岗位上，同组有组长
    g = d["shifts"][0]
    assert g["my_enrollment"]["status"] == "enrolled"
    mine = g["positions"][0]
    assert mine["training_note"] == SECRET and mine["attachments"]
    assert mine["leader_name"] and mine["leader_contact"]["phone"]
    # 另一个场次没报：那边照样看不到
    other = d["shifts"][1]["positions"][0]
    assert other["training_note"] == "" and other["leader_contact"] is None


def test_外人先填缓存_报了名的人照样看得到(client, secret_case):
    case = secret_case()
    _assert_outsider_view(_detail(client, login(client, case["outsider"]), case["id"]))
    _assert_member_view(_detail(client, login(client, case["member"]), case["id"]))


def test_报了名的人先填缓存_外人也看不到(client, secret_case):
    case = secret_case()
    d = _detail(client, login(client, case["member"]), case["id"])
    _assert_member_view(d)
    _assert_outsider_view(_detail(client, login(client, case["outsider"]), case["id"]))
    # 缓存里压根没有这些：不是「存进去再挑着给」
    leader = d["shifts"][0]["positions"][0]["leader_contact"]
    raw = locks._client.get(pubcache._key("detail", case["id"]))
    assert raw and SECRET not in raw
    assert leader["phone"] not in raw and leader["wechat"] not in raw


def test_我的报名状态不吃缓存(client, db, open_group, take_student, monkeypatch):
    """把「提交后作废」关掉，缓存就一直是旧的——人数跟着旧，我的状态不能旧。"""
    act, g = open_group(3)
    me, other = login(client, take_student()), login(client, take_student())
    _detail(client, other, act.id)                     # 别人先把缓存填上
    monkeypatch.setattr(pubcache, "invalidate", lambda ids: None)

    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=me, json={"position_id": g.test_position_id})
    assert r.status_code == 200, r.text
    d = _detail(client, me, act.id)
    assert d["shifts"][0]["my_enrollment"]["status"] == "enrolled"
    # 这份人数确实来自缓存（说明上面那条不是碰巧现算出来的）
    assert d["shifts"][0]["enrolled"] == 0

    r = client.delete(f"/api/activities/enrollments/{r.json()['enrollment_id']}",
                      headers=me)
    assert r.status_code == 200, r.text
    assert "my_enrollment" not in _detail(client, me, act.id)["shifts"][0]


def test_报名与退选提交后人数立刻更新(client, open_group, take_student):
    act, g = open_group(3)
    me, other = login(client, take_student()), login(client, take_student())
    assert _detail(client, other, act.id)["shifts"][0]["enrolled"] == 0
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=me, json={"position_id": g.test_position_id})
    assert r.status_code == 200, r.text
    assert _detail(client, other, act.id)["shifts"][0]["enrolled"] == 1
    client.delete(f"/api/activities/enrollments/{r.json()['enrollment_id']}",
                  headers=me)
    assert _detail(client, other, act.id)["shifts"][0]["enrolled"] == 0


def test_管理者改完学生立刻看到(client, admin, open_group, take_student):
    act, g = open_group(3)
    t = login(client, take_student())
    assert _positions(_detail(client, t, act.id))[g.test_position_id]["name"] == "测试岗位"
    listed = client.get("/api/activities", headers=t,
                        params={"q": act.title, "status": ""}).json()["items"]
    assert [a["total_capacity"] for a in listed] == [3]

    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{g.test_position_id}",
                         headers=admin, json={"name": "检录岗", "quota": 5})
        assert r.status_code == 200, r.text
        # 编辑冻结本身也要立刻看到：报名按钮得跟着停
        assert _detail(client, t, act.id)["editing"] is True

    d = _detail(client, t, act.id)
    assert _positions(d)[g.test_position_id]["name"] == "检录岗"
    assert d["editing"] is False
    listed = client.get("/api/activities", headers=t,
                        params={"q": act.title, "status": ""}).json()["items"]
    assert [a["total_capacity"] for a in listed] == [5]


def test_直接改库提交后同样作废(client, db, open_group, take_student):
    """作废挂在会话提交上，不靠各个接口记得调用：定时任务、脚本改的也算。"""
    act, g = open_group(3)
    t = login(client, take_student())
    _detail(client, t, act.id)
    # 新开一个会话：只加载岗位、不加载场次，作废时得自己查出它属于哪个活动
    s = SessionLocal()
    try:
        s.get(Position, g.test_position_id).name = "改库改的"
        s.commit()
    finally:
        s.close()
    assert _positions(_detail(client, t, act.id))[g.test_position_id]["name"] == "改库改的"


@pytest.fixture
def frozen(monkeypatch):
    """时间字段（倒计时毫秒数）每次现算，比较前把「此刻」钉住。"""
    t = now()
    monkeypatch.setattr(serializers, "now", lambda: t)


def test_开关缓存结果一致(client, admin, db, secret_case, frozen, monkeypatch):
    case = secret_case()
    # 再造一个候补的人，my_enrollment 的候补名次也一并比
    queued = student_row(db, case["outsider"])
    pid = next(iter(_positions(_detail(client, admin, case["id"]))))
    p = db.get(Position, pid)
    db.add(Enrollment(id=uid("enr"), activity_id=case["id"],
                      shift_id=p.shift_id, user_id=queued.id,
                      position_id=pid, status="waitlisted", joined_at=now()))
    db.commit()

    viewers = {"外人/候补": login(client, case["outsider"]),
               "已报名": login(client, case["member"]),
               "管理者": admin}
    paths = [f"/api/activities/{case['id']}",
             "/api/activities?status=&size=50"]
    for name, h in viewers.items():
        for path in paths:
            cold = client.get(path, headers=h).json()      # 现算并填缓存
            warm = client.get(path, headers=h).json()      # 命中
            monkeypatch.setattr(pubcache, "ENABLED", False)
            off = client.get(path, headers=h).json()
            monkeypatch.setattr(pubcache, "ENABLED", True)
            assert cold == warm == off, (name, path)


def test_Redis_不可用照常出页面(client, open_group, take_student, monkeypatch):
    act, _g = open_group(3)
    t = login(client, take_student())
    dead = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2,
                       decode_responses=True)
    monkeypatch.setattr(pubcache, "_client", lambda: dead)
    assert _detail(client, t, act.id)["id"] == act.id
    r = client.get("/api/activities?status=", headers=t)
    assert r.status_code == 200 and r.json()["items"]


def test_作废后只有一个人去重算_其余先用旧的(db, open_group):
    """抢报时每落一条报名就作废一次。直接删键的话缓存几乎总是空的；
    现在作废后抢到「重算」占位的那一个去算，其余拿 3 秒内的那份旧的。"""
    act, _g = open_group(3)
    calls = []

    def build_(ids):
        calls.append(ids)
        return {i: {"n": len(calls)} for i in ids}

    assert pubcache.get_many("t", [act.id], build_) == {act.id: {"n": 1}}
    assert pubcache.get_many("t", [act.id], build_) == {act.id: {"n": 1}}
    assert len(calls) == 1

    pubcache.invalidate([act.id])
    fill = pubcache._key("t", act.id) + ":fill"
    locks._client.set(fill, 1, px=2000)                # 假装有人正在重算
    assert pubcache.get_many("t", [act.id], build_) == {act.id: {"n": 1}}
    assert len(calls) == 1
    locks._client.delete(fill)
    assert pubcache.get_many("t", [act.id], build_) == {act.id: {"n": 2}}
    assert pubcache.get_many("t", [act.id], build_) == {act.id: {"n": 2}}
    assert len(calls) == 2


def test_过了三秒一定是新的(db, open_group):
    """旧的那份最多用到它自己过期：TTL 就是业主认可的那 3 秒。"""
    act, _g = open_group(3)
    pubcache.get_many("t2", [act.id], lambda ids: {i: {"n": 1} for i in ids})
    ttl = locks._client.pttl(pubcache._key("t2", act.id))
    assert 0 < ttl <= pubcache.TTL_MS == 3000
