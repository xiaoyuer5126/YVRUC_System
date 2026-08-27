"""通知签收：发送时要求签收、志愿者签收、管理端查看与催办。"""
from __future__ import annotations

from backend.models import Notification, User
from conftest import QINGXIE, login


def _send(client, admin, act_id, *, need_ack, title="集合地点变更"):
    r = client.post(f"/api/staff/activities/{act_id}/notify", headers=admin,
                    json={"scope": "all", "title": title,
                          "content": "集合点改到世纪馆北门，请提前 15 分钟到场",
                          "need_ack": need_ack})
    assert r.status_code == 200
    return r.json()


def test_普通通知不产生签收批次(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    body = _send(client, admin, act.id, need_ack=False)
    assert body["batch_id"] is None
    assert client.get(f"/api/staff/activities/{act.id}/notify-batches",
                      headers=admin).json() == []


def test_签收全流程(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    body = _send(client, admin, act.id, need_ack=True)
    batch = body["batch_id"]
    assert batch and body["count"] >= 1

    # 志愿者侧：收件箱里带待签收标记与计数
    student = login(client, u.student_id)
    inbox = client.get("/api/me/notifications", headers=student).json()
    assert inbox["pending_ack"] == 1
    item = next(n for n in inbox["items"] if n["need_ack"])
    assert item["acked_at"] is None

    # 管理端：未签收名单里有这个人
    batches = client.get(f"/api/staff/activities/{act.id}/notify-batches",
                         headers=admin).json()
    b = next(x for x in batches if x["batch_id"] == batch)
    assert b["acked"] == 0
    assert u.student_id in [p["student_id"] for p in b["pending"]]

    # 签收
    r = client.post(f"/api/me/notifications/{item['id']}/ack", headers=student)
    assert r.status_code == 200 and r.json()["acked_at"]

    batches = client.get(f"/api/staff/activities/{act.id}/notify-batches",
                         headers=admin).json()
    b = next(x for x in batches if x["batch_id"] == batch)
    assert b["acked"] == 1 and b["pending"] == []
    assert client.get("/api/me/notifications",
                      headers=student).json()["pending_ack"] == 0


def test_重复签收不报错也不改时间(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True)
    student = login(client, u.student_id)
    item = next(n for n in client.get("/api/me/notifications",
                                      headers=student).json()["items"]
                if n["need_ack"])
    first = client.post(f"/api/me/notifications/{item['id']}/ack",
                        headers=student).json()["acked_at"]
    again = client.post(f"/api/me/notifications/{item['id']}/ack",
                        headers=student).json()["acked_at"]
    assert first == again


def test_一键已读不代替签收(client, admin, db, enrolled_activity):
    """「全部已读」是浏览动作，不能顺手把签收也做了。"""
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True)
    student = login(client, u.student_id)
    client.post("/api/me/notifications/read-all", headers=student)
    inbox = client.get("/api/me/notifications", headers=student).json()
    assert inbox["unread"] == 0
    assert inbox["pending_ack"] == 1


def test_不能签收别人的通知(client, admin, db, enrolled_activity, take_student):
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True)
    mine = (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.need_ack.is_(True)).first())
    other = login(client, take_student())
    r = client.post(f"/api/me/notifications/{mine.id}/ack", headers=other)
    assert r.status_code == 404


def test_催办只发给未签收的人(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    batch = _send(client, admin, act.id, need_ack=True)["batch_id"]

    r = client.post(f"/api/staff/notify-batches/{batch}/remind", headers=admin)
    assert r.status_code == 200 and r.json()["count"] >= 1

    # 签收后再催，应该没人可催
    student = login(client, u.student_id)
    for n in client.get("/api/me/notifications",
                        headers=student).json()["items"]:
        if n["need_ack"] and not n["acked_at"]:
            client.post(f"/api/me/notifications/{n['id']}/ack", headers=student)
    r = client.post(f"/api/staff/notify-batches/{batch}/remind", headers=admin)
    assert r.status_code == 400


def test_非本活动管理者不能查看签收情况(client, teacher, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = client.get(f"/api/staff/activities/{act.id}/notify-batches",
                   headers=teacher)
    assert r.status_code == 403
