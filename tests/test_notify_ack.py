"""通知签收：发送时要求签收、志愿者签收、管理端查看与催办。"""
from __future__ import annotations

from conftest import editing, login


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
    assert batch and body["count"] == 1         # 活动里只有这一位志愿者

    # 志愿者侧：收件箱里带待签收标记与计数
    student = login(client, u.student_id)
    inbox = client.get("/api/me/notifications", headers=student).json()
    assert inbox["pending_ack"] == 1
    item = next(n for n in inbox["items"] if n["need_ack"])
    assert item["acked_at"] is None
    # 站内签收按钮就在卡片上，点卡片照样进活动页（企业微信里才落在消息中心）
    assert item["page"] == f"/activity/{act.id}"

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


def _first_ack_item(client, headers) -> dict:
    return next(n for n in client.get("/api/me/notifications",
                                      headers=headers).json()["items"]
                if n["need_ack"])


def test_不能签收别人的通知(client, admin, db, enrolled_activity, take_student):
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True)
    student = login(client, u.student_id)
    mine = _first_ack_item(client, student)
    other = login(client, take_student())
    r = client.post(f"/api/me/notifications/{mine['id']}/ack", headers=other)
    assert r.status_code == 404
    # 本人那条仍是待签收
    assert client.get("/api/me/notifications",
                      headers=student).json()["pending_ack"] == 1


def test_催办只发给未签收的人(client, admin, db, enrolled_activity, take_student):
    act, shift, u = enrolled_activity
    # 再报一个人：两人一签一不签，才看得出催办是挑着发的
    other = login(client, take_student())
    assert client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                       headers=other, json={"position_id": shift.positions[0].id}
                       ).status_code == 200
    batch = _send(client, admin, act.id, need_ack=True)["batch_id"]

    student = login(client, u.student_id)
    assert client.post(f"/api/me/notifications/{_first_ack_item(client, student)['id']}/ack",
                       headers=student).status_code == 200
    r = client.post(f"/api/staff/notify-batches/{batch}/remind", headers=admin)
    assert r.status_code == 200 and r.json()["count"] == 1

    # 都签收了再催，应该没人可催
    assert client.post(f"/api/me/notifications/{_first_ack_item(client, other)['id']}/ack",
                       headers=other).status_code == 200
    r = client.post(f"/api/staff/notify-batches/{batch}/remind", headers=admin)
    assert r.status_code == 400


def test_催办不产生新的待签收(client, admin, db, enrolled_activity):
    """催一次就多一条待办的话，5 个人的批次催一轮会变成 10 条。"""
    act, _shift, u = enrolled_activity
    batch = _send(client, admin, act.id, need_ack=True)["batch_id"]
    student = login(client, u.student_id)

    for _ in range(2):
        assert client.post(f"/api/staff/notify-batches/{batch}/remind",
                           headers=admin).status_code == 200

    inbox = client.get("/api/me/notifications", headers=student).json()
    assert inbox["pending_ack"] == 1          # 催了两轮，要签的还是那一条
    assert sum(1 for n in inbox["items"]
               if n["template_key"] == "ack_reminder") == 2

    b = next(x for x in client.get(
        f"/api/staff/activities/{act.id}/notify-batches",
        headers=admin).json() if x["batch_id"] == batch)
    assert (b["total"], b["acked"], b["reminded"]) == (1, 0, 2)
    assert b["last_remind_at"]

    # 签掉那一条，批次就齐了——催办不影响口径
    item = next(n for n in inbox["items"] if n["need_ack"])
    client.post(f"/api/me/notifications/{item['id']}/ack", headers=student)
    b = next(x for x in client.get(
        f"/api/staff/activities/{act.id}/notify-batches",
        headers=admin).json() if x["batch_id"] == batch)
    assert (b["total"], b["acked"], b["pending"]) == (1, 1, [])


def test_非本活动管理者不能查看签收情况(client, initiator, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = client.get(f"/api/staff/activities/{act.id}/notify-batches",
                   headers=initiator)
    assert r.status_code == 403


def _remind(client, admin, shift_id, **body):
    r = client.post(f"/api/staff/shifts/{shift_id}/checkin-reminder",
                    headers=admin, json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_签到提醒默认不要求签收(client, admin, enrolled_activity):
    act, shift, _u = enrolled_activity
    assert _remind(client, admin, shift.id)["batch_id"] is None
    assert client.get(f"/api/staff/activities/{act.id}/notify-batches",
                      headers=admin).json() == []


def test_签到提醒可要求签收并进批次(client, admin, enrolled_activity):
    """现场催场最需要「他到底看没看到」，所以提醒也能要签收。"""
    act, shift, u = enrolled_activity
    batch = _remind(client, admin, shift.id, need_ack=True)["batch_id"]
    assert batch

    b = next(x for x in client.get(
        f"/api/staff/activities/{act.id}/notify-batches",
        headers=admin).json() if x["batch_id"] == batch)
    assert b["acked"] == 0
    assert u.student_id in [p["student_id"] for p in b["pending"]]

    # 催办要能定位到活动（靠 data.activity_id 判权限），签收后计数归位
    assert client.post(f"/api/staff/notify-batches/{batch}/remind",
                       headers=admin).json()["count"] == 1
    student = login(client, u.student_id)
    for n in client.get("/api/me/notifications",
                        headers=student).json()["items"]:
        if n["need_ack"] and not n["acked_at"]:
            client.post(f"/api/me/notifications/{n['id']}/ack", headers=student)
    b = next(x for x in client.get(
        f"/api/staff/activities/{act.id}/notify-batches",
        headers=admin).json() if x["batch_id"] == batch)
    assert (b["total"], b["acked"], b["pending"], b["reminded"]) == (1, 1, [], 1)


def test_签收批次不串活动(client, admin, db, enrolled_activity, take_student):
    """同一个人报了两个活动，A 的管理者不该看到 B 的未签收名单。"""
    from backend.models import Enrollment, now, uid
    from conftest import make_enrolled_activity

    act_a, _shift_a, u = enrolled_activity
    act_b, shift_b, _other = make_enrolled_activity(db, take_student)
    db.add(Enrollment(id=uid("enr"), activity_id=act_b.id, shift_id=shift_b.id,
                      user_id=u.id, position_id=shift_b.positions[0].id,
                      status="enrolled", joined_at=now()))
    db.commit()

    batch_b = _send(client, admin, act_b.id, need_ack=True,
                    title="B 活动通知")["batch_id"]
    ids = [x["batch_id"] for x in client.get(
        f"/api/staff/activities/{act_a.id}/notify-batches",
        headers=admin).json()]
    assert batch_b not in ids
    # B 自己的批次照常可见
    assert batch_b in [x["batch_id"] for x in client.get(
        f"/api/staff/activities/{act_b.id}/notify-batches",
        headers=admin).json()]


def test_点开一条就算已读_签收仍要本人点(client, admin, db, enrolled_activity,
                                        take_student):
    """已读是浏览，签收是确认：点开消息只消掉红点，签收还得自己按。"""
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True)
    student = login(client, u.student_id)
    mine = _first_ack_item(client, student)
    # 别人的点不动（先验这个，免得本人已读之后看不出差别）
    other = login(client, take_student())
    assert client.post(f"/api/me/notifications/{mine['id']}/read",
                       headers=other).status_code == 404
    assert client.get("/api/me/notifications",
                      headers=student).json()["unread"] == 1
    assert client.post(f"/api/me/notifications/{mine['id']}/read",
                       headers=student).status_code == 200
    inbox = client.get("/api/me/notifications", headers=student).json()
    assert inbox["unread"] == 0 and inbox["pending_ack"] == 1


def test_管理者收到的通知也点得进活动(client, admin, db, enrolled_activity):
    """人员变动、排岗提醒这些原来没带活动 id，点了没反应。

    用「打开组长开关 → 提醒管理者指定组长」这条真实链路触发一条给管理者的通知。
    """
    act, shift, _u = enrolled_activity
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{shift.positions[0].id}",
                         headers=admin,
                         json={"needs_leader": True,
                               "leader_task": "带队进场、清点人数"})
        assert r.status_code == 200, r.text
    rows = [n for n in client.get("/api/me/notifications", headers=admin).json()["items"]
            if n["title"] == f"[组长空缺] {act.title}"]
    assert len(rows) == 1 and rows[0]["data"].get("activity_id") == act.id


def test_未读数有瘦接口(client, admin, db, enrolled_activity):
    """顶上那个 30 秒轮询只要一个数字，不该把最近 50 条通知的正文一起拉回去。

    完整接口的 items 里每条都带 content（集合地点、注意事项那种长文本），
    而轮询只用到 unread——开着的每个页面每 30 秒拉一次，纯浪费。
    """
    act, _shift, u = enrolled_activity
    _send(client, admin, act.id, need_ack=True, title="集合地点变更")

    stu = login(client, u.student_id)
    thin = client.get("/api/me/notifications/unread", headers=stu)
    assert thin.status_code == 200
    assert set(thin.json()) == {"unread", "pending_ack"}      # 没有 items

    # 两个数字要和完整接口对得上，否则顶上那个红点会骗人
    full = client.get("/api/me/notifications", headers=stu).json()
    assert thin.json()["unread"] == full["unread"]
    assert thin.json()["pending_ack"] == full["pending_ack"] == 1

    # 签收之后待签收数归零
    item = next(n for n in full["items"] if n["need_ack"])
    client.post(f"/api/me/notifications/{item['id']}/ack", headers=stu)
    assert client.get("/api/me/notifications/unread",
                      headers=stu).json()["pending_ack"] == 0
