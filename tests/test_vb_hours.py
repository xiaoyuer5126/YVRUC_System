"""时长锁定之后的收尾：活动先落「待录入志愿北京」，管理者上传录入截图并确认
才算已完成；解锁一律回到已结束；停太久每天提醒一次。

以及时长审批的通知口径：逐条只发对接人，超时才催并抄送会长团。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend import scheduled
from backend.models import (Activity, ActivityManager, Attachment, AuditLog,
                            Department, HoursRequest, Notification, now, uid)
from conftest import (ADMIN, INITIATOR, LIAISON_HEAD, dl_url, inbox, login,
                      student_row)

COUNCIL = "2025003"         # 张，会长团（不是管理员）


@pytest.fixture
def finished(db):
    """一场已结束、一条时长都没有的活动（核准即锁定），管理员是管理者。"""
    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"收尾测试 {uid('t')}", organizer="x",
                   intro="x", location="x", status="finished",
                   reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.commit()
    return act


def _lock(client, headers, act):
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=headers, json={})
    assert r.status_code == 200 and r.json()["locked"] is True, r.text


def _status(db, act):
    db.expire_all()
    return db.get(Activity, act.id).status


def _upload(client, headers, aid, name="录入.png", data=b"\x89PNG shot"):
    return client.post(f"/api/staff/activities/{aid}/project-files/vb_hours",
                       headers=headers, files={"file": (name, data, "image/png")})


def _confirm(client, headers, aid):
    return client.post(f"/api/staff/activities/{aid}/vb-hours/confirm", headers=headers)


def _files(client, headers, aid):
    r = client.get(f"/api/staff/activities/{aid}/project-files", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ======================= 待录入志愿北京 → 已完成 =======================

def test_锁定后待录入_传了截图确认才完成(client, admin, db, finished):
    act = finished
    # 锁定之前传不了，也确认不了
    assert _upload(client, admin, act.id).status_code == 409
    assert _confirm(client, admin, act.id).status_code == 409

    _lock(client, admin, act)
    assert _status(db, act) == "vb_pending"
    f = _files(client, admin, act.id)
    assert f["status"] == "vb_pending" and f["vb_hours"] is None
    assert f["can_upload_vb_hours"] and not f["can_confirm_vb_hours"]

    # 不传截图不能确认
    r = _confirm(client, admin, act.id)
    assert r.status_code == 400 and "截图" in r.json()["detail"]
    assert _status(db, act) == "vb_pending"

    # 只收图片/PDF；非管理者传不了、也确认不了
    assert _upload(client, admin, act.id, "录入.docx", b"doc").status_code == 400
    assert _upload(client, login(client, INITIATOR), act.id).status_code == 403
    assert _upload(client, admin, act.id, "v1.png").status_code == 200
    # 待录入期间可以换，只留一份
    assert _upload(client, admin, act.id, "v2.pdf", b"%PDF shot").status_code == 200
    db.expire_all()
    assert [a.title for a in db.query(Attachment)
            .filter(Attachment.owner_type == "vb_hours",
                    Attachment.owner_id == act.id).all()] == ["v2.pdf"]
    assert _confirm(client, login(client, INITIATOR), act.id).status_code == 403
    assert _files(client, admin, act.id)["can_confirm_vb_hours"]

    r = _confirm(client, admin, act.id)
    assert r.status_code == 200 and r.json()["status"] == "completed", r.text
    assert _status(db, act) == "completed"
    assert (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id,
                    AuditLog.summary.like("%已录入志愿北京%")).count() == 1)

    # 完成之后截图定下来：不能再换，也不必再确认
    r = _upload(client, admin, act.id, "v3.png")
    assert r.status_code == 409 and "解锁" in r.json()["detail"]
    assert _confirm(client, admin, act.id).status_code == 409
    f = _files(client, admin, act.id)
    assert not f["can_upload_vb_hours"] and not f["can_confirm_vb_hours"]


def test_截图只给管理者与审批线看(client, admin, db, finished, take_student):
    act = finished
    _lock(client, admin, act)
    shot = _upload(client, admin, act.id).json()
    path = f"/api/materials/{shot['id']}"
    assert client.get(dl_url(client, admin, path)).status_code == 200
    stu = login(client, take_student())
    assert client.get(dl_url(client, stu, path)).status_code == 404
    # 截图不走学习材料的删除口子
    assert client.delete(f"/api/staff/materials/{shot['id']}",
                         headers=admin).status_code == 404


def test_待录入与已完成都算活动已结束(client, admin, db, finished):
    """按状态列表判断的地方要认新状态：结构只读、广场照常可见、展示为已结束。"""
    act = finished
    _lock(client, admin, act)
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert d["status"] == "vb_pending" and d["status_label"] == "待录入志愿北京"
    r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                    json={"name": "补一场", "mode": "pick",
                          "start_time": (now() + timedelta(days=1)).isoformat(),
                          "end_time": (now() + timedelta(days=1, hours=2)).isoformat()})
    assert r.status_code == 409


def _unlock(client, headers, act):
    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=headers, json={"reason": "漏算了两位同学的时长"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                    headers=headers, json={"approve": True})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("complete_first", [False, True])
def test_解锁一律回到已结束_重新锁定再录一遍(client, admin, db, finished,
                                              complete_first):
    act = finished
    _lock(client, admin, act)
    assert _upload(client, admin, act.id).status_code == 200
    if complete_first:
        assert _confirm(client, admin, act.id).status_code == 200
        assert _status(db, act) == "completed"

    _unlock(client, admin, act)
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.status == "finished" and row.hours_locked_at is None
    # 截的是改之前的时长，随解锁作废
    assert _files(client, admin, act.id)["vb_hours"] is None
    assert _upload(client, admin, act.id).status_code == 409

    _lock(client, admin, act)
    assert _status(db, act) == "vb_pending"
    assert _confirm(client, admin, act.id).status_code == 400


# ======================= 待录入提醒 =======================

def _at(day_offset: int, hour: int) -> datetime:
    base = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return base + timedelta(days=day_offset)


def test_待录入满三天每天提醒一次(client, admin, db, finished, monkeypatch):
    act = finished
    _lock(client, admin, act)
    title = f"请录入志愿北京｜{act.title}"
    t0 = _at(0, 10)

    def run(at):
        monkeypatch.setattr(scheduled, "now", lambda: at)
        scheduled.remind_vb_hours(db)
        return len(inbox(db, ADMIN, title))

    def locked_before(delta):
        db.expire_all()
        db.get(Activity, act.id).hours_locked_at = t0 - delta
        db.commit()

    locked_before(timedelta(days=2, hours=23))
    assert run(t0) == 0, "三天内不提醒"
    locked_before(timedelta(days=3, minutes=1))
    assert run(_at(0, 8)) == 0, "提醒点之前不发"
    assert run(t0) == 1
    assert run(t0 + timedelta(hours=5)) == 1, "同一天不重复"
    assert run(_at(1, 9)) == 2, "第二天再提醒一次"
    assert run(_at(1, 20)) == 2

    # 确认完成之后不再提醒
    assert _upload(client, admin, act.id).status_code == 200
    assert _confirm(client, admin, act.id).status_code == 200
    assert run(_at(2, 10)) == 2


# ======================= 时长审批通知口径 =======================

def _approved_act(db, dept_id) -> Activity:
    """已结束、时长已核准、等着报批的活动。"""
    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"报批通知 {uid('t')}", organizer="x",
                   status="finished", approval_dept_id=dept_id,
                   hours_approved_at=now() - timedelta(hours=2),
                   appeal_deadline=now() + timedelta(hours=5),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.commit()
    return act


def _who_got(db, prefix):
    return {sid for sid in (ADMIN, COUNCIL, LIAISON_HEAD) if inbox(db, sid, prefix)}


def test_时长审批只通知对接人_不再抄送会长团与管理员(client, admin, db, liaison):
    act = _approved_act(db, liaison.id)
    assert client.post(f"/api/staff/activities/{act.id}/project-files/stamped",
                       headers=admin,
                       files={"file": ("盖章.pdf", b"%PDF", "application/pdf")}
                       ).status_code == 200
    assert client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                       headers=admin, json={}).status_code == 200
    assert _who_got(db, f"时长锁定审批｜{act.title}") == {LIAISON_HEAD}
    # 会长团、管理员照样能处理
    head = login(client, LIAISON_HEAD)
    req = next(x for x in client.get("/api/staff/hours-requests", headers=admin).json()
               if x["activity_id"] == act.id)
    assert client.post(f"/api/staff/hours-requests/{req['id']}/decide",
                       headers=admin, json={"approve": True}).status_code == 200

    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏记了两个人的时长"})
    assert r.status_code == 200
    assert _who_got(db, f"时长解锁申请｜{act.title}") == {LIAISON_HEAD}
    assert client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                       headers=head, json={"approve": False, "reply": "先别动"}
                       ).status_code == 200


def test_找不到对接人就兜底发会长团(client, admin, db):
    """办公室没有部长；没归口部门的活动也一样。"""
    office = db.query(Department).filter(Department.name == "办公室").one()
    for dept_id in (office.id, None):
        act = _approved_act(db, dept_id)
        assert client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                           headers=admin, json={}).status_code == 200
        # 管理员 2025001 同时在会长团，所以两人都收到
        assert _who_got(db, f"时长锁定审批｜{act.title}") == {ADMIN, COUNCIL}


def test_时长审批超时催对接人抄送会长团_只催一次(client, admin, db, liaison):
    act = _approved_act(db, liaison.id)
    admin_id = student_row(db, ADMIN).id
    stale = HoursRequest(id=uid("hrq"), activity_id=act.id, kind="lock",
                         reason="系统自动提交", requested_by=admin_id,
                         created_at=now() - timedelta(hours=25))
    fresh_act = _approved_act(db, liaison.id)
    fresh = HoursRequest(id=uid("hrq"), activity_id=fresh_act.id, kind="lock",
                         reason="刚提交", requested_by=admin_id,
                         created_at=now() - timedelta(hours=23))
    db.add_all([stale, fresh])
    db.commit()

    def reminded(a) -> list[str]:
        """收到催办的人（学号），一条一个，重复收到就会重复出现。"""
        db.expire_all()
        ids = {student_row(db, sid).id: sid for sid in (ADMIN, COUNCIL, LIAISON_HEAD)}
        rows = (db.query(Notification)
                .filter(Notification.title.like("时长锁定审批已等待%"),
                        Notification.title.endswith(a.title)).all())
        return sorted(ids.get(n.user_id, n.user_id) for n in rows)

    scheduled.remind_stale_hours_requests(db)
    assert reminded(act) == sorted([LIAISON_HEAD, ADMIN, COUNCIL])
    assert reminded(fresh_act) == [], "没满 24 小时不催"
    db.expire_all()
    assert db.get(HoursRequest, stale.id).reminded_at is not None

    scheduled.remind_stale_hours_requests(db)
    assert reminded(act) == sorted([LIAISON_HEAD, ADMIN, COUNCIL]), "每条只催一次"


def test_新的定时任务都接进了每轮(db):
    out = scheduled.run_once(db)
    assert "hours_approvals" in out and "vb_hours" in out
