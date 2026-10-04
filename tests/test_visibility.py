"""学生端可见性：培训内容与组长只对报名者开放。"""
from __future__ import annotations

from conftest import login


def test_未报名看不到培训内容与组长(client, admin, db, enrolled_activity,
                                  take_student):
    """组长和材料都得真有，否则「看不到」什么也没验到。"""
    import io

    from backend.models import Enrollment

    act, shift, enrolled_user = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = True
    db.commit()
    enr = db.query(Enrollment).filter(Enrollment.shift_id == shift.id,
                                      Enrollment.user_id == enrolled_user.id).one()
    r = client.post(f"/api/staff/enrollments/{enr.id}/leader", headers=admin,
                    json={"is_leader": True})
    assert r.status_code == 200, r.text
    r = client.post("/api/staff/materials", headers=admin,
                    data={"owner_type": "position", "owner_id": pos.id,
                          "title": "培训手册"},
                    files={"file": ("t.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 200, r.text

    # 对照：报了名的人看得到组长和材料
    me = login(client, enrolled_user.student_id)
    mine = client.get(f"/api/activities/{act.id}",
                      headers=me).json()["shifts"][0]["positions"][0]
    assert mine["leader_name"] == enrolled_user.name
    assert len(mine["attachments"]) == 1

    outsider = login(client, take_student())
    d = client.get(f"/api/activities/{act.id}", headers=outsider).json()
    p = d["shifts"][0]["positions"][0]
    assert p["training_visible"] is False
    assert p["training_note"] == ""
    assert p["attachments"] == []
    assert p["leader_name"] == ""
    # 招募信息该看的还得看得到——「报名后有几份材料」也算招募信息
    assert p["service_task"] and p["quota"] and p["location"]
    assert p["material_count"] == 1


def test_报名后能看到培训内容(client, db, enrolled_activity):
    act, shift, u = enrolled_activity
    me = login(client, u.student_id)
    p = client.get(f"/api/activities/{act.id}",
                   headers=me).json()["shifts"][0]["positions"][0]
    assert p["training_visible"] is True
    assert p["training_note"] == shift.positions[0].training_note


def test_管理者不必先报名就能核对(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    p = client.get(f"/api/activities/{act.id}",
                   headers=admin).json()["shifts"][0]["positions"][0]
    assert p["training_visible"] is True


def test_急招置顶排最前且只有会长团能点(client, admin, initiator,
                                        enrolled_activity, take_student):
    """急招是全校层面的调度：会长团/管理员置顶，广场上排最前挂「急招」。"""
    act, _g, _u = enrolled_activity
    url = f"/api/staff/activities/{act.id}/pin"
    assert client.post(url, headers=initiator, json={"pinned": True}).status_code == 403
    r = client.post(url, headers=admin, json={"pinned": True})
    assert r.status_code == 200 and r.json()["pinned"]

    h = login(client, take_student())
    items = client.get("/api/activities", headers=h).json()["items"]
    assert items[0]["id"] == act.id and items[0]["urgent"]

    assert client.post(url, headers=admin, json={"pinned": False}).json()["pinned"] is False


def test_广场按条件筛选并分页(client, db, open_group, take_student):
    """全量返回迟早会炸：筛选与排序交给后端，一页十几条。"""
    from backend.models import Activity, uid

    tag = uid("org")            # 独一无二的组织单位，免得和别的用例互相干扰
    acts = []
    for i in range(3):
        act, _g = open_group(2)
        row = db.get(Activity, act.id)
        row.organizer, row.title = tag, f"{tag} 活动{i}"
        acts.append(row)
    db.commit()
    h = login(client, take_student())

    d = client.get(f"/api/activities?organizer={tag}&size=2", headers=h).json()
    assert d["total"] == 3 and len(d["items"]) == 2
    d2 = client.get(f"/api/activities?organizer={tag}&size=2&page=2",
                    headers=h).json()
    assert len(d2["items"]) == 1
    assert {a["id"] for a in d["items"] + d2["items"]} == {a.id for a in acts}
    assert tag in d["organizers"]

    d = client.get(f"/api/activities?organizer={tag}&q=活动1", headers=h).json()
    assert [a["title"] for a in d["items"]] == [f"{tag} 活动1"]

    db.get(Activity, acts[0].id).status = "closed"
    db.commit()
    open_ids = {a["id"] for a in client.get(
        f"/api/activities?organizer={tag}&status=published", headers=h).json()["items"]}
    assert acts[0].id not in open_ids and len(open_ids) == 2
    closed_ids = {a["id"] for a in client.get(
        f"/api/activities?organizer={tag}&status=closed", headers=h).json()["items"]}
    assert closed_ids == {acts[0].id}
