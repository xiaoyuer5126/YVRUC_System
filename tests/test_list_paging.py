"""志愿者档案名册、角色管理、消息列表的分页。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Notification, now, uid
from conftest import login, student_row, upload_qr


def _batch(db, take_student, n: int, **fields) -> list[str]:
    """现建 n 个学生，统一改上给定字段，返回学号（升序）。"""
    sids = [take_student() for _ in range(n)]
    for sid in sids:
        u = student_row(db, sid)
        for k, v in fields.items():
            setattr(u, k, v)
    db.commit()
    return sorted(sids)


def _pages(client, headers, url: str, size: int) -> tuple[int, list[str]]:
    """一页页翻到底，返回 (total, 各页拼起来的学号)。"""
    got, page = [], 1
    while True:
        d = client.get(f"{url}&page={page}&size={size}", headers=headers).json()
        if not d["items"]:
            return d["total"], got
        got += [r["student_id"] for r in d["items"]]
        page += 1


def test_档案名册分页且学院筛选在分页前生效(client, admin, db, take_student):
    college = f"分页学院{uid('c')}"
    sids = _batch(db, take_student, 25, college=college)

    url = f"/api/staff/users?college={college}"
    d = client.get(f"{url}&page=2&size=10", headers=admin).json()
    assert d["total"] == 25
    assert [r["student_id"] for r in d["items"]] == sids[10:20]
    # 翻到底不重不漏，按学号排
    total, got = _pages(client, admin, url, 10)
    assert total == 25 and got == sids

    # 搜索 + 学院 + 分页叠在一起
    name = f"翻页{uid('n')[-6:]}"
    for sid in sids[:12]:
        student_row(db, sid).name = name
    db.commit()
    d = client.get(f"{url}&q={name}&page=2&size=5", headers=admin).json()
    assert d["total"] == 12
    assert [r["student_id"] for r in d["items"]] == sids[5:10]


def test_档案名册每页上限100(client, admin):
    assert client.get("/api/staff/users?size=101", headers=admin).status_code == 422
    assert client.get("/api/staff/users?page=0", headers=admin).status_code == 422


def test_角色列表分页且搜索与身份筛选都在分页前生效(client, admin, db, take_student):
    name = f"角色翻页{uid('n')[-6:]}"
    sids = _batch(db, take_student, 23, name=name,
                  roles=["student", "initiator"])
    total, got = _pages(client, admin, f"/api/staff/roles?q={name}", 10)
    assert total == 23 and got == sids
    d = client.get(f"/api/staff/roles?q={name}&page=3&size=10", headers=admin).json()
    assert [r["student_id"] for r in d["items"]] == sids[20:]

    # 不搜索、按身份筛：只出发起方，总数算的是全部而不是这一页
    d = client.get("/api/staff/roles?role=initiator&page=1&size=5",
                   headers=admin).json()
    assert d["total"] >= 23 and len(d["items"]) == 5
    assert all("initiator" in r["roles"] for r in d["items"])
    total, got = _pages(client, admin, "/api/staff/roles?role=initiator", 50)
    assert set(sids) <= set(got) and total == len(got) == len(set(got))


def test_消息列表分页且未读数照算全部(client, db, take_student):
    sid = take_student()
    u = student_row(db, sid)
    base = now() - timedelta(hours=1)
    made = []
    for i in range(25):
        n = Notification(id=uid("ntf"), user_id=u.id, title=f"第{i}条",
                         content="x", created_at=base + timedelta(minutes=i),
                         need_ack=(i == 0))
        db.add(n)
        made.append(n.id)
    db.commit()
    newest_first = list(reversed(made))
    h = login(client, sid)

    d = client.get("/api/me/notifications?page=1&size=10", headers=h).json()
    assert d["total"] == 25 and d["has_more"] is True
    assert d["unread"] == 25 and d["pending_ack"] == 1
    assert [n["id"] for n in d["items"]] == newest_first[:10]

    d3 = client.get("/api/me/notifications?page=3&size=10", headers=h).json()
    assert d3["has_more"] is False
    assert [n["id"] for n in d3["items"]] == newest_first[20:]
    # 最早那条要签收的在最后一页，照样能签
    assert client.post(f"/api/me/notifications/{made[0]}/ack",
                       headers=h).status_code == 200


def test_消息同一时刻发出的几条翻页时不重不漏(client, db, take_student):
    sid = take_student()
    u = student_row(db, sid)
    at = now()
    ids = []
    for i in range(7):
        n = Notification(id=uid("ntf"), user_id=u.id, title="同一秒",
                         content="x", created_at=at)
        db.add(n)
        ids.append(n.id)
    db.commit()
    h = login(client, sid)
    got = []
    for page in (1, 2, 3):
        got += [n["id"] for n in client.get(
            f"/api/me/notifications?page={page}&size=3", headers=h).json()["items"]]
    assert sorted(got) == sorted(ids)


def test_管理端活动列表分页_同一时刻建的也不重不漏(client, admin, db):
    """不筛状态时在 SQL 里分页：总数照算全部，按建立时间倒序；同一时刻建的几条
    按 id 定序，翻到底不重不漏，和筛「草稿」时翻出来的次序一致。"""
    from backend.models import Activity
    tag = f"列表翻页{uid('t')[-6:]}"
    ids = []
    for i in range(7):
        r = client.post("/api/staff/activities", headers=admin,
                        json={"title": f"{tag}-{i}", "organizer": "文学院青协",
                              "location": "世纪馆", "intro": "x", "contact_group_qr": upload_qr(client, admin),
                              "contacts": [{"name": "王", "phone": "13900000000"}],
                              "reg_start": now().isoformat(),
                              "reg_end": (now() + timedelta(days=5)).isoformat()})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    base = now() - timedelta(hours=1)
    for i, aid in enumerate(ids):
        # 前四条同一时刻，后三条依次更晚
        db.get(Activity, aid).created_at = base + timedelta(minutes=max(0, i - 3))
    db.commit()
    expected = [a.id for a in sorted((db.get(Activity, x) for x in ids),
                                     key=lambda a: (a.created_at, a.id), reverse=True)]

    def pages(url):
        got, page = [], 1
        while True:
            d = client.get(f"{url}&page={page}&size=3", headers=admin).json()
            if not d["items"]:
                return d["total"], got
            assert all(x["display_status"] == "draft" for x in d["items"])
            got += [x["id"] for x in d["items"]]
            page += 1

    assert pages(f"/api/staff/activities?q={tag}") == (7, expected)
    assert pages(f"/api/staff/activities?q={tag}&status=draft") == (7, expected)
    d = client.get(f"/api/staff/activities?q={tag}&status=pending", headers=admin).json()
    assert d["total"] == 0 and d["items"] == []
