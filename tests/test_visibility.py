"""学生端可见性：培训内容与组长只对报名者开放。"""
from __future__ import annotations

from conftest import login


def test_未报名看不到培训内容与组长(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, enrolled_user = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = True
    db.commit()

    outsider = login(client, take_student())
    d = client.get(f"/api/activities/{act.id}", headers=outsider).json()
    p = d["shifts"][0]["positions"][0]
    assert p["training_visible"] is False
    assert p["training_note"] == ""
    assert p["attachments"] == []
    assert p["leader_name"] == ""
    # 招募信息该看的还得看得到
    assert p["requirement_note"] and p["quota"] and p["location"]


def test_报名后能看到培训内容(client, db, enrolled_activity):
    act, shift, u = enrolled_activity
    me = login(client, u.student_id)
    p = client.get(f"/api/activities/{act.id}",
                   headers=me).json()["shifts"][0]["positions"][0]
    assert p["training_visible"] is True
    assert p["training_note"]


def test_管理者不必先报名就能核对(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    p = client.get(f"/api/activities/{act.id}",
                   headers=admin).json()["shifts"][0]["positions"][0]
    assert p["training_visible"] is True


def test_材料数量始终公开(client, admin, db, enrolled_activity, take_student):
    """「报名后有几份材料」属于招募信息，内容才需要报名后才能看。"""
    import io
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    r = client.post("/api/staff/materials", headers=admin,
                    data={"owner_type": "position", "owner_id": pos.id,
                          "title": "培训手册"},
                    files={"file": ("t.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 200

    outsider = login(client, take_student())
    p = client.get(f"/api/activities/{act.id}",
                   headers=outsider).json()["shifts"][0]["positions"][0]
    assert p["material_count"] == 1 and p["attachments"] == []
