"""接口层。需要一个真的 Postgres——JSONB、乐观并发、按 owner 过滤这三件事
用 sqlite 或者假仓库测都测不出真东西。

    SCHEDULE_TEST_DATABASE_URL=postgresql://tool_schedule:...@127.0.0.1:5433/rucommunity

没设就跳过。
"""
from __future__ import annotations

import io
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

DB_URL = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="没有配 SCHEDULE_TEST_DATABASE_URL")

ORIGIN = "http://localhost:9365"

ALICE = None
BOB = None


def _identity(nickname: str):
    from app.auth import Identity

    return Identity(uuid.uuid4(), nickname, f"{nickname}@ruc.edu.cn", "user")


@pytest.fixture
def client(monkeypatch):
    from app.auth import require_login
    from app.main import API_PREFIX, app

    global ALICE, BOB
    ALICE, BOB = _identity("小明"), _identity("小红")
    current = {"identity": ALICE}

    async def _fake_login():
        return current["identity"]

    app.dependency_overrides[require_login] = _fake_login
    try:
        with TestClient(app) as test_client:
            test_client.identity = current       # type: ignore[attr-defined]
            test_client.prefix = API_PREFIX      # type: ignore[attr-defined]
            yield test_client
    finally:
        app.dependency_overrides.clear()


def url(client, path: str) -> str:
    return f"{client.prefix}{path}"


def to_xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


POSITIONS = [
    ["岗位ID", "岗位名称", "时间说明", "名额", "要求"],
    ["P01", "检录A组", "8月1日上午", 2, ""],
    ["P02", "颁奖礼仪", "8月1日下午", 1, "性别=女"],
]

SIGNUPS = [
    ["提交答卷时间", "姓名", "身份证号", "联系方式", "微信", "性别", "P01", "P02"],
    ["2026-07-20 09:01", "张三", "110101200501011234", "13800000001", "zs", "男", 1, ""],
    ["2026-07-20 09:03", "李四", "110101200502022345", "13800000002", "", "女", 1, 1],
    ["2026-07-20 09:05", "王五", "110101200503033456", "13800000003", "", "男", 1, ""],
]


def new_project(client, name="测试项目") -> dict:
    resp = client.post(url(client, "/projects"), json={"name": name}, headers={"Origin": ORIGIN})
    assert resp.status_code == 201, resp.text
    return resp.json()


def with_data(client) -> dict:
    project = new_project(client)
    pid = project["id"]
    r1 = client.post(
        url(client, f"/projects/{pid}/import/positions"),
        files={"file": ("岗位.xlsx", to_xlsx(POSITIONS), "application/vnd.ms-excel")},
        headers={"Origin": ORIGIN},
    )
    assert r1.status_code == 200, r1.text
    r2 = client.post(
        url(client, f"/projects/{pid}/import/signups"),
        files={"file": ("报名.xlsx", to_xlsx(SIGNUPS), "application/vnd.ms-excel")},
        headers={"Origin": ORIGIN},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["added"] == 4
    return r2.json()["project"]


# --------------------------------------------------------------------- 隔离

def test_看不到别人的项目_而且不告诉你它存不存在(client):
    """两种情况都回 404。区分开的话，拿 UUID 逐个试就能问出别人有哪些项目。"""
    mine = new_project(client, "小明的活动")

    client.identity["identity"] = BOB
    assert client.get(url(client, "/projects")).json()["items"] == []
    assert client.get(url(client, f"/projects/{mine['id']}")).status_code == 404
    assert client.delete(
        url(client, f"/projects/{mine['id']}"), headers={"Origin": ORIGIN}
    ).status_code == 404
    # 编一个不存在的，回的东西要一模一样
    assert client.get(url(client, f"/projects/{uuid.uuid4()}")).status_code == 404


def test_列表页不带整份数据(client):
    """列表里有别人的联系方式没必要，也没用——列表只显示数量。"""
    with_data(client)
    items = client.get(url(client, "/projects")).json()["items"]
    assert len(items) == 1
    assert "data" not in items[0]
    assert items[0]["positions"] == 2
    assert items[0]["volunteers"] == 3


# --------------------------------------------------------------------- 存取

def test_版本对不上就不让盖(client):
    """两个标签页开着同一个项目，后写的不该把前一个的改动无声盖掉。"""
    project = with_data(client)
    pid, version = project["id"], project["version"]

    first = client.put(
        url(client, f"/projects/{pid}"),
        json={"data": project["data"], "version": version},
        headers={"Origin": ORIGIN},
    )
    assert first.status_code == 200
    assert first.json()["version"] == version + 1

    stale = client.put(
        url(client, f"/projects/{pid}"),
        json={"data": project["data"], "version": version},
        headers={"Origin": ORIGIN},
    )
    assert stale.status_code == 409
    assert "改过" in stale.json()["detail"]


def test_坏数据存不进去(client):
    project = new_project(client)
    resp = client.put(
        url(client, f"/projects/{project['id']}"),
        json={"data": {"positions": [{"没有pid": 1}]}, "version": project["version"]},
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 400
    assert "格式不对" in resp.json()["detail"]


# --------------------------------------------------------------------- 排班

def test_预览不落库_应用才落库(client):
    project = with_data(client)
    pid, version = project["id"], project["version"]
    assert project["data"]["assignments"] == []

    preview = client.post(
        url(client, f"/projects/{pid}/solve"),
        json={"apply": False},
        headers={"Origin": ORIGIN},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    # 预览**只回变更**，不回整份状态——一份六千条报名的项目那是 3.5MB，
    # 而预览界面只用得上 diff 那几个字段。
    assert body["project"] is None, "预览不该带整份状态"
    assert body["diff"], "应该有变更行"
    assert body["degraded"] is False
    # 库里还是空的
    assert client.get(url(client, f"/projects/{pid}")).json()["data"]["assignments"] == []

    applied = client.post(
        url(client, f"/projects/{pid}/solve"),
        json={"apply": True, "version": version},
        headers={"Origin": ORIGIN},
    )
    assert applied.status_code == 200, applied.text
    saved = client.get(url(client, f"/projects/{pid}")).json()
    assert saved["data"]["assignments"], "应用之后库里要有"
    # 预览和应用算出来的是同一份——求解器是确定性的。预览不回整份状态了，
    # 所以拿 diff 来比：同一份结果必然给出同一份变更。
    assert applied.json()["diff"] == body["diff"]
    # 应用的响应顺手带回了新状态，省掉前端再 GET 一次
    assert applied.json()["project"]["data"]["assignments"] == saved["data"]["assignments"]


def test_应用时版本对不上就拒(client):
    project = with_data(client)
    pid = project["id"]
    resp = client.post(
        url(client, f"/projects/{pid}/solve"),
        json={"apply": True, "version": project["version"] - 1},
        headers={"Origin": ORIGIN},
    )
    assert resp.status_code == 409


def test_排班遵守名额和岗位要求(client):
    """P02 只有 1 个名额且要求性别=女，报了的只有李四（女）。"""
    project = with_data(client)
    body = client.post(
        url(client, f"/projects/{project['id']}/solve"),
        json={"apply": True, "version": project["version"]},
        headers={"Origin": ORIGIN},
    ).json()
    per_post = {}
    for a in body["project"]["data"]["assignments"]:
        per_post.setdefault(a["pid"], []).append(a["vol_key"])
    assert len(per_post.get("P01", [])) <= 2
    assert len(per_post.get("P02", [])) <= 1
    for vk in per_post.get("P02", []):
        assert "李四" in vk


# --------------------------------------------------------------------- 导出

def test_同一个人不能同时算两次(client):
    """前端会把按钮禁掉，但那只挡得住同一个页面上的连点——开两个标签页、
    刷新后再点、或者直接拿 curl 打，都能绕过去。而一次排班最坏要占一个核
    一分半，几个并发就能把容器的 CPU 配额吃干。
    """
    from app import solve_guard

    project = with_data(client)
    pid = project["id"]
    try:
        # 假装已经有一次在算
        import time as _time

        solve_guard._inflight[ALICE.user_id] = _time.monotonic()
        resp = client.post(
            url(client, f"/projects/{pid}/solve"), json={"apply": False}, headers={"Origin": ORIGIN}
        )
        assert resp.status_code == 409
        assert "已经有一次排班在算" in resp.json()["detail"]

        # 别人不受影响：两个不同的人同时排班是正常使用
        client.identity["identity"] = BOB
        other = client.post(
            url(client, "/projects"), json={"name": "小红的"}, headers={"Origin": ORIGIN}
        ).json()
        assert client.post(
            url(client, f"/projects/{other['id']}/solve"),
            json={"apply": False},
            headers={"Origin": ORIGIN},
        ).status_code == 200
    finally:
        solve_guard._inflight.clear()


def test_算完之后闸门会放开(client):
    project = with_data(client)
    from app import solve_guard

    for _ in range(2):
        resp = client.post(
            url(client, f"/projects/{project['id']}/solve"),
            json={"apply": False},
            headers={"Origin": ORIGIN},
        )
        assert resp.status_code == 200
    assert not solve_guard.busy(ALICE.user_id)


def test_漏掉的标记会自己过期(client):
    """协程在线程池真正开始之前就被取消的话，那个 finally 永远不会跑。
    宁可多放一次，也不能让人再也点不了重排。
    """
    import time as _time

    from app import solve_guard
    from app.config import get_settings

    project = with_data(client)
    stale = _time.monotonic() - (get_settings().solve_max_seconds + solve_guard.STALE_SLACK_SECONDS + 1)
    solve_guard._inflight[ALICE.user_id] = stale
    try:
        resp = client.post(
            url(client, f"/projects/{project['id']}/solve"),
            json={"apply": False},
            headers={"Origin": ORIGIN},
        )
        assert resp.status_code == 200
    finally:
        solve_guard._inflight.clear()


def test_派生视图的字段和前端对得上(client):
    """字段名写错的话，页面上就是几列空白——不报错、不崩，只是没东西，
    最难查的那一种。这里把契约钉死，改名字会当场红。

    对应 apps/web/src/api/tools/schedule.ts 里的那几个 interface。
    """
    project = with_data(client)
    client.post(
        url(client, f"/projects/{project['id']}/solve"),
        json={"apply": True, "version": project["version"]},
        headers={"Origin": ORIGIN},
    )
    views = client.get(url(client, f"/projects/{project['id']}")).json()["views"]

    assert set(views) == {
        "stats", "schedule", "queue", "signups", "stranded", "ineligible",
        "caps", "tag_values", "checks"}
    assert set(views["stats"][0]) == {
        "pid", "name", "time_note", "quota", "min_quota", "filled", "gap", "state",
        "leader_quota", "leader_filled", "requirements", "preferences",
        "leader_conditions", "req_note"}
    group = views["schedule"][0]
    assert set(group) == {
        "pid", "name", "time_note", "quota", "min_quota", "filled", "requirements",
        "preferences", "leader_quota", "leader_filled", "leader_conditions",
        "req_note", "rows"}
    assert set(group["rows"][0]) == {
        "seq", "vol_key", "status", "forced", "role", "time_raw",
        "key", "name", "contact", "wechat", "tags"}
    assert set(views["signups"][0]) == {
        "key", "name", "contact", "wechat", "tags", "wants", "assigned",
        "time_raw", "time_text", "time_ok"}
    # 候补和零岗位名单是**截断过的**：{items, total, ...}，不是裸数组。
    assert set(views["queue"]) == {"items", "total", "per_position"}
    assert set(views["stranded"]) == {"items", "total", "limit"}
    if views["queue"]["items"]:
        assert set(views["queue"]["items"][0]) == {
            "pid", "pos_name", "rank", "vol_key", "name", "contact",
            "wechat", "tags", "time_raw", "count", "reason"}

    # 屏幕上的表格一律不带身份证号——导出的 xlsx 里才有。
    assert "110101200501011234" not in str(views)
    assert "id_card" not in str(views)


def test_导出的xlsx有五张表(client):
    project = with_data(client)
    resp = client.get(url(client, f"/projects/{project['id']}/export"))
    assert resp.status_code == 200
    assert resp.content[:2] == b"PK"
    wb = load_workbook(io.BytesIO(resp.content))
    assert wb.sheetnames == ["排班表", "候补队列", "岗位统计", "个人班表", "变更表"]


def test_示例表格下得下来(client):
    resp = client.get(url(client, "/sample"))
    assert resp.status_code == 200
    assert load_workbook(io.BytesIO(resp.content)).sheetnames == ["岗位表", "报名表"]


# --------------------------------------------------------------------- 到期

def test_过期项目会被真删(client):
    """留一份「已删除但还在库里」的联系方式名单，等于没删。"""
    from datetime import UTC, datetime, timedelta

    from app.db import SessionLocal
    from app.models import ScheduleProject
    from app.store import purge_expired

    project = with_data(client)
    db = SessionLocal()
    try:
        row = db.get(ScheduleProject, uuid.UUID(project["id"]))
        row.expires_at = datetime.now(UTC) - timedelta(days=1)
        db.commit()
        assert purge_expired(db) >= 1
        assert db.get(ScheduleProject, uuid.UUID(project["id"])) is None
    finally:
        db.close()
    assert client.get(url(client, f"/projects/{project['id']}")).status_code == 404


def test_续期会把到期日推回去(client):
    project = new_project(client)
    before = client.get(url(client, "/projects")).json()["items"][0]["expires_at"]

    from datetime import UTC, datetime, timedelta

    from app.db import SessionLocal
    from app.models import ScheduleProject

    db = SessionLocal()
    try:
        row = db.get(ScheduleProject, uuid.UUID(project["id"]))
        row.expires_at = datetime.now(UTC) + timedelta(days=3)
        db.commit()
    finally:
        db.close()

    soon = client.get(url(client, "/projects")).json()["items"][0]
    assert soon["expiring_soon"] is True

    renewed = client.post(
        url(client, f"/projects/{project['id']}/renew"), headers={"Origin": ORIGIN}
    )
    assert renewed.status_code == 200
    assert renewed.json()["expiring_soon"] is False
    assert renewed.json()["expires_at"] >= before[:10]


# --------------------------------------------------------------------- 来源

def test_写操作要过来源白名单(client):
    """这个服务吃的是社区那张 cookie，CSRF 那道防线一条都不能少。"""
    resp = client.post(
        url(client, "/projects"),
        json={"name": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    assert "白名单" in resp.json()["detail"]


# --------------------------------------------------------------------- 协作

def test_协作者什么都能改_就是删不掉项目(client):
    """走真 HTTP 的那一遍。store 里那条用例证明了判定本身，这条证明**路由真的
    调了它**——漏调一个 require_owner 是最容易犯又最难发现的错。
    """
    project = with_data(client)
    pid = project["id"]

    invite = client.post(url(client, f"/projects/{pid}/invites"), json={},
                         headers={"Origin": ORIGIN})
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]

    client.identity["identity"] = BOB
    joined = client.post(url(client, f"/invites/{token}/accept"), json={},
                         headers={"Origin": ORIGIN})
    assert joined.status_code == 200, joined.text
    # 加进来之后看得见，而且身份是「协作者」不是「开发者」
    detail = client.get(url(client, f"/projects/{pid}")).json()
    assert detail["role"] == "member"
    assert detail["can_delete"] is False

    # 能改——但得先接管编辑权：小明刚建完数据，锁还在他手上。
    # 「同一时刻只有一个人能改」和「协作者能改」这两件事就是这么搭起来的。
    taken = client.post(url(client, f"/projects/{pid}/lock?force=1"), json={},
                        headers={"Origin": ORIGIN})
    assert taken.status_code == 200, taken.text
    assert taken.json()["editing"]["is_me"] is True

    saved = client.put(
        url(client, f"/projects/{pid}"),
        json={"data": detail["data"], "version": detail["version"], "action": "应用排班"},
        headers={"Origin": ORIGIN},
    )
    assert saved.status_code == 200, saved.text

    # 删不掉
    denied = client.delete(url(client, f"/projects/{pid}"), headers={"Origin": ORIGIN})
    assert denied.status_code == 403, denied.text
    assert "创建者" in denied.json()["detail"]

    # 创建者可以（先接管编辑权——小红刚才改过，锁在她手上）
    client.identity["identity"] = ALICE
    assert client.get(url(client, f"/projects/{pid}")).json()["can_delete"] is True
    gone = client.delete(url(client, f"/projects/{pid}"), headers={"Origin": ORIGIN})
    assert gone.status_code == 204


def test_没被邀请的人拿不到项目(client):
    project = with_data(client)
    client.identity["identity"] = BOB
    assert client.get(url(client, f"/projects/{project['id']}")).status_code == 404
