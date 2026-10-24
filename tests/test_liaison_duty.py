"""部门能力开关（启用值班、对外活动、志愿北京对接）与值班：谁能值班、指定与交班、
审批通知发给值班人、值班人能拍板、部长的每晚日报。志愿北京外联确认见 test_vb_confirm.py。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend import scheduled
from backend.models import (Activity, ActivityManager, AuditLog, Department,
                            DutyHandover, HoursRequest, Notification, User, now, uid)
from conftest import (ADMIN, INITIATOR, LIAISON_CORE, LIAISON_HEAD, LIAISON_MEMBER,
                      approve, inbox, is_approval_todo, liaison_dept, login,
                      make_draft, student_row, upload_stamped)


def _set_duty(client, headers, dept_id, user_id, note=""):
    return client.post(f"/api/staff/duty/{dept_id}", headers=headers,
                       json={"user_id": user_id, "note": note})


def _submit(client, headers, aid, dept_id):
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=headers,
                    json={"department_id": dept_id})
    assert r.status_code == 200, r.text
    return r.json()


# ======================= 值班资格与交接 =======================

def test_只有本部门骨干或部长能值班(client, db, liaison, take_student):
    head = login(client, LIAISON_HEAD)
    member = student_row(db, LIAISON_MEMBER)
    outsider = student_row(db, take_student())
    for who in (member, outsider):
        r = _set_duty(client, head, liaison.id, who.id)
        assert r.status_code == 400 and "骨干或部长" in r.json()["detail"]

    core = student_row(db, LIAISON_CORE)
    r = _set_duty(client, head, liaison.id, core.id, note="本周")
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["on_duty"]["name"] == "林骁"
    assert d["history"][0]["to"] == "林骁" and d["history"][0]["note"] == "本周"
    # 部长自己也能值班
    assert _set_duty(client, head, liaison.id,
                     student_row(db, LIAISON_HEAD).id).status_code == 200

    db.expire_all()
    assert db.query(AuditLog).filter(AuditLog.action == "duty",
                                     AuditLog.target_id == liaison.id).count() >= 2


def test_骨干不能自己上岗_在班的人才能交班(client, db, liaison, extra_core):
    core = login(client, LIAISON_CORE)
    lin = student_row(db, LIAISON_CORE)
    # 不在班的骨干不能指定值班（包括自己）
    assert _set_duty(client, core, liaison.id, lin.id).status_code == 403

    head = login(client, LIAISON_HEAD)
    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200
    # 在班的人不能撂挑子，要交给具体的人
    r = _set_duty(client, core, liaison.id, "")
    assert r.status_code == 400 and "交班给谁" in r.json()["detail"]
    r = _set_duty(client, core, liaison.id, extra_core.id, note="期中考试")
    assert r.status_code == 200, r.text
    assert r.json()["on_duty"]["id"] == extra_core.id
    # 交出去之后就不再能安排
    assert _set_duty(client, core, liaison.id, lin.id).status_code == 403

    db.expire_all()
    last = (db.query(DutyHandover).filter(DutyHandover.dept_id == liaison.id)
            .order_by(DutyHandover.created_at.desc()).first())
    assert (last.from_user_id, last.to_user_id, last.actor_id) == \
        (lin.id, extra_core.id, lin.id)
    # 接班的人收到通知，部长也知道换了人
    assert is_approval_todo(inbox(db, extra_core.student_id, "您开始值班"))
    assert inbox(db, LIAISON_HEAD, "值班交接")

    # 在班的人也可以把班交回给部长
    r = _set_duty(client, login(client, extra_core.student_id), liaison.id,
                  student_row(db, LIAISON_HEAD).id)
    assert r.status_code == 200, r.text
    assert r.json()["on_duty"]["id"] == student_row(db, LIAISON_HEAD).id

    # 部长可以撤下值班（回落给自己）
    r = _set_duty(client, head, liaison.id, "")
    assert r.status_code == 200 and r.json()["on_duty"] is None


def test_值班卡片只给相关的人(client, db, liaison, take_student):
    core = login(client, LIAISON_CORE)
    rows = client.get("/api/staff/duty", headers=core).json()
    assert [d["dept_name"] for d in rows] == ["对外联络部"]
    assert rows[0]["can_assign"] is False
    assert LIAISON_CORE in [p["student_id"] for p in rows[0]["eligible"]]
    assert LIAISON_MEMBER not in [p["student_id"] for p in rows[0]["eligible"]]
    initiator = login(client, INITIATOR)
    assert client.get("/api/staff/duty", headers=initiator).json() == []


def test_值班人被调离部门就自动下岗(client, db, liaison, extra_core):
    head = login(client, LIAISON_HEAD)
    assert _set_duty(client, head, liaison.id, extra_core.id).status_code == 200
    u = db.get(User, extra_core.id)
    u.qingxie_post = "member"
    db.commit()
    rows = client.get("/api/staff/duty", headers=head).json()
    assert rows[0]["on_duty"] is None


# ======================= 活动审批 =======================

def test_审批通知只发值班人_没人值班回落部长(client, admin, db, liaison):
    lin = student_row(db, LIAISON_CORE)
    head = login(client, LIAISON_HEAD)
    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200

    aid = make_draft(client, admin)
    assert _submit(client, admin, aid, liaison.id)["assignee"] == "林骁"
    title = db.get(Activity, aid).title
    assert inbox(db, LIAISON_CORE, f"志愿活动待审批｜{title}")
    assert not inbox(db, LIAISON_HEAD, f"志愿活动待审批｜{title}")

    # 撤下值班：下一条回到部长
    assert _set_duty(client, head, liaison.id, "").status_code == 200
    aid2 = make_draft(client, admin)
    assert _submit(client, admin, aid2, liaison.id)["assignee"] == "周砚"
    title2 = db.get(Activity, aid2).title
    assert inbox(db, LIAISON_HEAD, f"志愿活动待审批｜{title2}")
    assert not inbox(db, LIAISON_CORE, f"志愿活动待审批｜{title2}")


def test_转交给部门也通知值班人(client, admin, db, liaison):
    lin = student_row(db, LIAISON_CORE)
    head = login(client, LIAISON_HEAD)
    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200
    other = db.query(Department).filter(Department.name == "办公室").one()
    aid = make_draft(client, admin)
    # 先放到别的部门（直接改库：办公室没有部长，走不了提交），再由会长团转进来
    act = db.get(Activity, aid)
    act.status, act.approval_dept_id = "pending", other.id
    act.approval_assignee_id = student_row(db, ADMIN).id
    act.submitted_at = now()
    db.commit()
    r = client.post(f"/api/staff/activities/{aid}/transfer-approval", headers=admin,
                    json={"department_id": liaison.id, "reason": "二级团体的活动"})
    assert r.status_code == 200 and r.json()["assignee"] == "林骁"
    assert is_approval_todo(inbox(db, LIAISON_CORE, f"转来一条待审批｜{act.title}"))
    assert not inbox(db, LIAISON_HEAD, f"转来一条待审批｜{act.title}")


def test_值班人能看能拍_部长照样能拍(client, admin, db, liaison):
    lin = student_row(db, LIAISON_CORE)
    head = login(client, LIAISON_HEAD)
    core = login(client, LIAISON_CORE)

    # 不在班：看不到、拍不了
    aid = make_draft(client, admin)
    _submit(client, admin, aid, liaison.id)
    assert aid not in [a["id"] for a in
                       client.get("/api/staff/approvals", headers=core).json()]
    assert client.post(f"/api/staff/activities/{aid}/decide", headers=core,
                       json={"approve": True}).status_code == 403

    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200
    rows = client.get("/api/staff/approvals", headers=core).json()
    row = next(a for a in rows if a["id"] == aid)
    assert row["mine"] and row["on_duty"] == "林骁" and row["proposal"]
    # 值班人能看未公开活动的完整安排
    assert client.get(f"/api/activities/{aid}", headers=core).status_code == 200
    r = client.post(f"/api/staff/activities/{aid}/decide", headers=core,
                    json={"approve": True, "comment": "值班通过"})
    assert r.status_code == 200 and r.json()["status"] == "published"

    # 有人值班时部长照样看得到、能直接拍
    aid2 = make_draft(client, admin)
    _submit(client, admin, aid2, liaison.id)
    assert aid2 in [a["id"] for a in
                    client.get("/api/staff/approvals", headers=head).json()]
    r = client.post(f"/api/staff/activities/{aid2}/decide", headers=head,
                    json={"approve": False, "comment": "部长驳回"})
    assert r.status_code == 200

    # 值班人也能转交
    aid3 = make_draft(client, admin)
    _submit(client, admin, aid3, liaison.id)
    r = client.post(f"/api/staff/activities/{aid3}/transfer-approval", headers=core,
                    json={"student_id": "2025003", "reason": "请会长定"})
    assert r.status_code == 200
    # 转给会长团之后，部门（值班人、部长）不再插手
    assert client.post(f"/api/staff/activities/{aid3}/decide", headers=core,
                       json={"approve": True}).status_code == 403
    assert client.post(f"/api/staff/activities/{aid3}/decide", headers=head,
                       json={"approve": True}).status_code == 403


def test_其他部门不受值班影响(client, admin, db, liaison):
    """值班只在对外联络部启用：别的部门的骨干即便被写进 duty_user_id 也不算数。"""
    other = db.query(Department).filter(Department.name == "办公室").one()
    lin = student_row(db, LIAISON_CORE)
    other.duty_user_id = lin.id
    db.commit()
    try:
        from backend import liaison as lz
        assert lz.on_duty(db, other) is None
        assert lz.is_dept_handler(db, lin, other.id) is False
    finally:
        other.duty_user_id = None
        db.commit()


# ======================= 时长锁定/解锁 =======================

def _finished_external(db, liaison) -> Activity:
    """一场对外联络部归口、已结束、时长已核准的活动，管理员是管理者。"""
    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"对外时长 {uid('t')}", organizer="x",
                   status="finished", approval_dept_id=liaison.id,
                   hours_approved_at=now() - timedelta(hours=2),
                   appeal_deadline=now() - timedelta(minutes=5),
                   vb_names=["对外时长志愿服务"], created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.commit()
    return act


def _upload_stamped(client, headers, aid):
    return upload_stamped(client, headers, aid)


def test_时长审批通知值班人_值班人能拍(client, admin, db, liaison):
    lin = student_row(db, LIAISON_CORE)
    head = login(client, LIAISON_HEAD)
    core = login(client, LIAISON_CORE)
    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200
    act = _finished_external(db, liaison)
    assert _upload_stamped(client, admin, act.id).status_code == 200
    r = client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    assert is_approval_todo(inbox(db, LIAISON_CORE, f"时长锁定审批｜{act.title}"))
    assert not inbox(db, LIAISON_HEAD, f"时长锁定审批｜{act.title}")

    # 部长照样看得到
    assert act.id in [x["activity_id"] for x in
                      client.get("/api/staff/hours-requests", headers=head).json()]
    rows = client.get("/api/staff/hours-requests", headers=core).json()
    req = next(x for x in rows if x["activity_id"] == act.id)
    assert req["external"] is True          # 前端据此给「结项通过 / 不通过」常用语
    r = client.post(f"/api/staff/hours-requests/{req['id']}/decide", headers=core,
                    json={"approve": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).hours_locked_at is not None

    # 解锁申请同样只通知值班人；这次让部长拍
    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏记了两个人的时长"})
    assert r.status_code == 200
    assert is_approval_todo(inbox(db, LIAISON_CORE, f"时长解锁申请｜{act.title}"))
    assert not inbox(db, LIAISON_HEAD, f"时长解锁申请｜{act.title}")
    r = client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                    headers=head, json={"approve": False, "reply": "先别动"})
    assert r.status_code == 200


def test_不在班的骨干拍不了时长(client, admin, db, liaison):
    act = _finished_external(db, liaison)
    req = HoursRequest(id=uid("hrq"), activity_id=act.id, kind="lock",
                       reason="x", requested_by=student_row(db, ADMIN).id)
    db.add(req)
    db.commit()
    core = login(client, LIAISON_CORE)
    assert client.post(f"/api/staff/hours-requests/{req.id}/decide", headers=core,
                       json={"approve": True}).status_code == 403


# ======================= 每晚日报 =======================

def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def _digests(db, dept_name="对外联络部"):
    head = student_row(db, LIAISON_HEAD)
    db.expire_all()
    return (db.query(Notification)
            .filter(Notification.user_id == head.id,
                    Notification.title.startswith(f"值班日报｜{dept_name}")).all())


def test_日报_九点前不发_九点后发一次(client, admin, db, liaison, monkeypatch):
    lin = student_row(db, LIAISON_CORE)
    head = login(client, LIAISON_HEAD)
    assert _set_duty(client, head, liaison.id, lin.id).status_code == 200
    # 今天：一条还压着，一条被值班人通过
    waiting = make_draft(client, admin)
    _submit(client, admin, waiting, liaison.id)
    handled = make_draft(client, admin)
    _submit(client, admin, handled, liaison.id)
    core = login(client, LIAISON_CORE)
    assert client.post(f"/api/staff/activities/{handled}/decide", headers=core,
                       json={"approve": True}).status_code == 200

    dept = db.get(Department, liaison.id)
    dept.duty_digest_on = None
    db.commit()
    before = len(_digests(db))

    monkeypatch.setattr(scheduled, "now", lambda: _at(20, 59))
    assert scheduled.send_duty_digests(db) == 0
    monkeypatch.setattr(scheduled, "now", lambda: _at(21, 0))
    assert scheduled.send_duty_digests(db) == 1
    rows = _digests(db)
    assert len(rows) == before + 1
    text = max(rows, key=lambda n: n.created_at).content
    w, h = db.get(Activity, waiting).title, db.get(Activity, handled).title
    assert "今日值班：林骁" in text
    new_part, rest = text.split("【今日处理】")
    done_part, pending_part = rest.split("【尚未处理】")
    assert w in new_part and h in new_part
    assert h in done_part and "林骁" in done_part
    assert w in pending_part and h not in pending_part

    # 同一天再扫（包括进程重启后）不发第二遍
    monkeypatch.setattr(scheduled, "now", lambda: _at(23, 30))
    assert scheduled.send_duty_digests(db) == 0
    assert len(_digests(db)) == before + 1
    # 值班人不收日报
    assert not inbox(db, LIAISON_CORE, "值班日报")


def test_日报_什么都没有就不发(client, db, monkeypatch, take_student):
    """换一个空部门来验：对外联络部在共享库里总有别的用例留下的待办。"""
    dept = Department(id=uid("dep"), name=f"值班测试部{uid('x')[-4:]}", sort=99,
                      duty_enabled=True)
    db.add(dept)
    boss = student_row(db, take_student())
    boss.roles = ["qingxie", "student"]
    boss.department_id, boss.qingxie_post = dept.id, "head"
    db.commit()
    monkeypatch.setattr(scheduled, "now", lambda: _at(21, 30))
    try:
        assert scheduled.send_duty_digests(db) == 0
        db.expire_all()
        assert db.get(Department, dept.id).duty_digest_on == _at(21).date()
        assert not (db.query(Notification)
                    .filter(Notification.user_id == boss.id).count())
    finally:
        db.rollback()
        boss = db.get(User, boss.id)
        boss.department_id, boss.qingxie_post = None, "member"
        db.commit()
        db.delete(db.get(Department, dept.id))
        db.commit()


# ======================= 部门能力开关 =======================

def test_迁移后对外联络部三项开关都开着(client, admin, db):
    d = db.query(Department).filter(Department.name == "对外联络部").one()
    assert d.duty_enabled and d.external and d.vb_liaison
    row = next(x for x in client.get("/api/staff/departments",
                                     headers=admin).json() if x["id"] == d.id)
    assert (row["duty_enabled"], row["external"], row["vb_liaison"]) == \
        (True, True, True)


def test_部门改名不影响三项开关(client, admin, db, liaison):
    """三项能力挂在部门 id 上，改名后值班、对外活动、志愿北京对接照样认它。"""
    new_name = f"测试改名部门 {uid('t')}"
    try:
        r = client.patch(f"/api/staff/departments/{liaison.id}", headers=admin,
                         json={"name": new_name})
        assert r.status_code == 200, r.text
        row = next(x for x in client.get("/api/staff/departments",
                                         headers=admin).json()
                   if x["id"] == liaison.id)
        assert row["name"] == new_name
        assert (row["duty_enabled"], row["external"], row["vb_liaison"]) == \
            (True, True, True)
        # 对接部门名是现查的，文案跟着新名字走
        me = client.get("/api/auth/me", headers=admin).json()["user"]
        assert me["vb_dept_name"] == new_name
        # 值班按部门认，不按名字认
        core = student_row(db, LIAISON_CORE)
        assert _set_duty(client, admin, liaison.id, core.id).status_code == 200
        duty = client.get("/api/staff/duty", headers=admin).json()
        assert next(x for x in duty
                    if x["dept_id"] == liaison.id)["dept_name"] == new_name
        # 归口要不要立项书也只看部门开关，不看名字
        aid = make_draft(client, admin, proposal=False)
        r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                        json={"department_id": liaison.id})
        assert r.status_code == 400 and new_name in r.json()["detail"]
    finally:
        r = client.patch(f"/api/staff/departments/{liaison.id}", headers=admin,
                         json={"name": "对外联络部"})
        assert r.status_code == 200, r.text


def test_志愿北京对接部门只能换不能清空也不能删(client, admin, db):
    ori_id = liaison_dept(db).id
    tmp = client.post("/api/staff/departments", headers=admin,
                      json={"name": f"测试对接部门 {uid('t')}"}).json()["id"]
    try:
        r = client.patch(f"/api/staff/departments/{tmp}", headers=admin,
                         json={"vb_liaison": True})
        assert r.status_code == 200, r.text
        db.expire_all()
        rows = client.get("/api/staff/departments", headers=admin).json()
        assert [x["id"] for x in rows if x["vb_liaison"]] == [tmp]
        assert db.get(Department, ori_id).vb_liaison is False

        r = client.patch(f"/api/staff/departments/{tmp}", headers=admin,
                         json={"vb_liaison": False})
        assert r.status_code == 400 and "不能清空" in r.json()["detail"]

        r = client.delete(f"/api/staff/departments/{tmp}", headers=admin)
        assert r.status_code == 409 and "志愿北京对接部门" in r.json()["detail"]
    finally:
        assert client.patch(f"/api/staff/departments/{ori_id}", headers=admin,
                            json={"vb_liaison": True}).status_code == 200
        assert client.delete(f"/api/staff/departments/{tmp}",
                             headers=admin).status_code == 200


def test_关闭值班撤下当前值班人(client, admin, db, liaison):
    core = student_row(db, LIAISON_CORE)
    assert _set_duty(client, admin, liaison.id, core.id).status_code == 200
    try:
        r = client.patch(f"/api/staff/departments/{liaison.id}", headers=admin,
                         json={"duty_enabled": False})
        assert r.status_code == 200, r.text
        db.expire_all()
        d = db.get(Department, liaison.id)
        assert d.duty_enabled is False and d.duty_user_id is None
        # 部门不再出现在值班卡片里
        ids = [x["dept_id"] for x in
               client.get("/api/staff/duty", headers=admin).json()]
        assert liaison.id not in ids
        # 交接记录留下「关闭值班」，不是无名无姓地撤掉
        log = (db.query(DutyHandover)
               .filter(DutyHandover.dept_id == liaison.id,
                       DutyHandover.note == "关闭值班")
               .order_by(DutyHandover.created_at.desc()).first())
        assert log is not None and log.from_user_id == core.id \
            and log.to_user_id is None
    finally:
        assert client.patch(f"/api/staff/departments/{liaison.id}", headers=admin,
                            json={"duty_enabled": True}).status_code == 200


def test_普通活动管理者不能开关部门能力(client, initiator, liaison):
    """这三项改的是全平台的审批去向，只有会长团、管理员能动。"""
    for body in ({"duty_enabled": False}, {"external": True},
                 {"vb_liaison": True}):
        r = client.patch(f"/api/staff/departments/{liaison.id}",
                         headers=initiator, json=body)
        assert r.status_code == 403


def test_归口对外活动的部门没立项书就不收提交(client, admin, db, take_student):
    name = f"测试对外部门 {uid('t')}"
    dept_id = client.post("/api/staff/departments", headers=admin,
                          json={"name": name}).json()["id"]
    head = student_row(db, take_student())
    try:
        r = client.put(f"/api/staff/roles/{head.id}", headers=admin,
                       json={"roles": ["student", "qingxie"],
                             "qingxie_post": "head", "department_id": dept_id})
        assert r.status_code == 200, r.text
        row = next(x for x in client.get("/api/staff/departments",
                                         headers=admin).json()
                   if x["id"] == dept_id)
        # 有部长、开关没开：先确认缺立项书之前不会被别的条件拦下
        assert row["has_head"] is True and row["external"] is False

        assert client.patch(f"/api/staff/departments/{dept_id}", headers=admin,
                            json={"external": True}).status_code == 200
        aid = make_draft(client, admin, proposal=False)
        r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                        json={"department_id": dept_id})
        assert r.status_code == 400 and "须先上传立项书" in r.json()["detail"]
    finally:
        assert client.patch(f"/api/staff/departments/{dept_id}", headers=admin,
                            json={"external": False}).status_code == 200
        assert client.put(f"/api/staff/roles/{head.id}", headers=admin,
                          json={"roles": ["student"]}).status_code == 200
        assert client.delete(f"/api/staff/departments/{dept_id}",
                             headers=admin).status_code == 200
