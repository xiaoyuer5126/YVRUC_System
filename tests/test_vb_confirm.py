"""志愿北京并入时长审核：管理者提交时长审核时一并填志愿北京活动名称（可多个）→
时长审核通过、锁定 → 名称推给对外联络部（值班人，没人值班是部长），不论活动归口
哪个部门 → 外联确认即「已完成」，或退回（必填理由）→ 管理者改名称重提（时长
保持锁定）→ 外联再确认。

志愿北京是时长锁定之后的附加步骤，确认和退回都不回头动时长：解锁不作废外联确认，
重新锁定、名称没变的沿用上一次结果；活动已完成之后，管理者还可以改名称再推一次，
活动保持「已完成」。

以及：审批意见（通过选填、驳回/退回必填）、提醒（外联超时催一次、退回后管理者
迟迟不重提每天提醒）、时长审批的通知口径、旧的志愿北京登记与截图接口已移除。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend import config, scheduled
from backend.models import (Activity, ActivityManager, AuditLog, Department,
                            HoursRequest, Notification, VbConfirm, now, uid)
from conftest import (ADMIN, INITIATOR, LIAISON_CORE, LIAISON_HEAD, inbox,
                      is_approval_todo, login, make_draft, student_row,
                      upload_proposal, upload_stamped)

COUNCIL = "2025003"         # 张，会长团（不是管理员）
NAMES = ["校运会志愿服务（上午）", "校运会志愿服务（下午）"]


def _approved_act(db, dept_id, *, names=None) -> Activity:
    """已结束、时长已核准（没有时长记录）、等着报批的活动，管理员是管理者。"""
    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"外联确认 {uid('t')}", organizer="文学院青协",
                   status="finished", approval_dept_id=dept_id,
                   hours_approved_at=now() - timedelta(hours=2),
                   appeal_deadline=now() + timedelta(hours=5),
                   vb_names=list(names or []), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.commit()
    return act


def _office(db) -> Department:
    """办公室：不是对外联络部、也没有部长（时长审批落到会长团/管理员）。"""
    return db.query(Department).filter(Department.name == "办公室").one()


def _status(db, act):
    db.expire_all()
    return db.get(Activity, act.id).status


def _submit(client, headers, act, names=None):
    body = {} if names is None else {"vb_names": names}
    return client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                       headers=headers, json=body)


def _decide_hours(client, headers, act, approve=True, reply=""):
    req = next(x for x in client.get("/api/staff/hours-requests", headers=headers).json()
               if x["activity_id"] == act.id)
    return client.post(f"/api/staff/hours-requests/{req['id']}/decide",
                       headers=headers, json={"approve": approve, "reply": reply})


def _lock(client, admin, act, names=NAMES):
    assert _submit(client, admin, act, names).status_code == 200
    r = _decide_hours(client, admin, act)
    assert r.status_code == 200, r.text


def _pending_confirm(client, headers, act):
    rows = [x for x in client.get("/api/staff/vb-confirms", headers=headers).json()
            if x["activity_id"] == act.id]
    return rows[0] if rows else None


def _history(client, headers, act):
    r = client.get(f"/api/staff/activities/{act.id}/vb-confirms", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _set_duty(client, db, liaison):
    head = login(client, LIAISON_HEAD)
    r = client.post(f"/api/staff/duty/{liaison.id}", headers=head,
                    json={"user_id": student_row(db, LIAISON_CORE).id})
    assert r.status_code == 200, r.text


# ======================= 提交时长审核带志愿北京名称 =======================

def test_提交时长审核必须带至少一个名称(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id)
    for bad in (None, [], ["  "], ["短"]):
        r = _submit(client, admin, act, bad)
        assert r.status_code == 400 and "志愿北京" in r.json()["detail"], (bad, r.text)
    db.expire_all()
    assert not db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).count()

    # 去空白、去重，多个照收
    r = _submit(client, admin, act, [NAMES[0], " ", f" {NAMES[0]} ", NAMES[1]])
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).vb_names == NAMES
    # 审的人在待办里看得到名称，通知里也有
    req = next(x for x in client.get("/api/staff/hours-requests", headers=admin).json()
               if x["activity_id"] == act.id)
    assert req["vb_names"] == NAMES
    note = inbox(db, ADMIN, f"时长锁定审批｜{act.title}")
    assert note and NAMES[1] in note[0].content
    # 审核期间名称跟时长一起冻结
    r = client.put(f"/api/staff/activities/{act.id}/vb-names", headers=admin,
                   json={"names": ["改一个名字"]})
    assert r.status_code == 409


def test_名称可以先存_提交时沿用(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id)
    url = f"/api/staff/activities/{act.id}/vb-names"
    assert client.put(url, headers=login(client, INITIATOR),
                      json={"names": NAMES}).status_code == 403
    assert client.put(url, headers=admin, json={"names": []}).status_code == 400
    assert client.put(url, headers=admin, json={"names": NAMES}).status_code == 200
    h = _history(client, admin, act)
    assert h["names"] == NAMES and h["can_edit"] and not h["can_resubmit"]
    assert client.get(f"/api/staff/activities/{act.id}/hours",
                      headers=admin).json()["vb_names"] == NAMES
    assert _submit(client, admin, act).status_code == 200


# ======================= 时长通过 → 推外联 → 确认 =======================

def test_时长通过后推给外联值班人_不论归口部门_确认即完成(client, admin, db, liaison):
    _set_duty(client, db, liaison)
    act = _approved_act(db, _office(db).id)      # 归口办公室，不是对外联络部
    assert _submit(client, admin, act, NAMES).status_code == 200
    r = _decide_hours(client, admin, act, reply="时长核对无误")
    assert r.status_code == 200
    assert _status(db, act) == "vb_pending"
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert d["status_label"] == "待外联确认"
    db.expire_all()
    assert db.get(Activity, act.id).hours_locked_at is not None
    # 通过意见随通知发给申请人
    got = inbox(db, ADMIN, f"时长锁定已通过｜{act.title}")
    assert got and "时长核对无误" in got[0].content and "对外联络部" in got[0].content

    # 外联值班人收到（带名称），部长不逐条收
    title = f"志愿北京待确认｜{act.title}"
    note = inbox(db, LIAISON_CORE, title)
    assert len(note) == 1 and NAMES[0] in note[0].content and NAMES[1] in note[0].content
    assert is_approval_todo(note)
    assert not inbox(db, LIAISON_HEAD, title)

    core = login(client, LIAISON_CORE)
    row = _pending_confirm(client, core, act)
    assert row and row["names"] == NAMES and row["kind"] == "lock"
    # 外联值班人/部长、会长团、管理员之外的人拍不了，待确认列表也是空的
    assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide",
                       headers=login(client, INITIATOR),
                       json={"approve": True}).status_code == 403
    assert _pending_confirm(client, login(client, INITIATOR), act) is None

    r = client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=core,
                    json={"approve": True, "reply": "已在志愿北京核对"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed", r.text
    assert _status(db, act) == "completed"
    got = inbox(db, ADMIN, f"志愿北京已确认｜{act.title}")
    assert got and "已在志愿北京核对" in got[0].content
    # 处理过的不能再拍
    assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=core,
                       json={"approve": True}).status_code == 409
    h = _history(client, admin, act)
    assert [(x["status"], x["handled_by"], x["reply"]) for x in h["items"]] == \
        [("confirmed", "林骁", "已在志愿北京核对")]


def test_没人值班推给外联部长(client, admin, db, liaison):
    act = _approved_act(db, liaison.id)
    assert upload_stamped(client, admin, act.id).status_code == 200
    _lock(client, admin, act)
    assert inbox(db, LIAISON_HEAD, f"志愿北京待确认｜{act.title}")
    assert not inbox(db, LIAISON_CORE, f"志愿北京待确认｜{act.title}")
    # 不在班的骨干拍不了；部长能拍
    core = login(client, LIAISON_CORE)
    assert _pending_confirm(client, core, act) is None
    row = _pending_confirm(client, login(client, LIAISON_HEAD), act)
    assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=core,
                       json={"approve": True}).status_code == 403


@pytest.mark.parametrize("who", [ADMIN, COUNCIL])
def test_会长团管理员也能确认(client, admin, db, liaison, who):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    h = login(client, who)
    row = _pending_confirm(client, h, act)
    r = client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=h,
                    json={"approve": True})
    assert r.status_code == 200 and _status(db, act) == "completed"


# ======================= 退回 → 重提 =======================

def test_退回必须写理由_管理者改名称重提_外联再次收到(client, admin, db, liaison):
    _set_duty(client, db, liaison)
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    db.expire_all()
    locked_at = db.get(Activity, act.id).hours_locked_at
    core = login(client, LIAISON_CORE)
    row = _pending_confirm(client, core, act)
    url = f"/api/staff/vb-confirms/{row['id']}/decide"
    for reply in ("", " 短 "):
        r = client.post(url, headers=core, json={"approve": False, "reply": reply})
        assert r.status_code == 400 and "理由" in r.json()["detail"]
    r = client.post(url, headers=core,
                    json={"approve": False, "reply": "志愿北京上查不到「下午」这一场"})
    assert r.status_code == 200 and r.json()["status"] == "returned"
    assert _status(db, act) == "vb_pending"
    got = inbox(db, ADMIN, f"志愿北京被退回｜{act.title}")
    assert got and "查不到" in got[0].content

    # 重提：只有管理者、只在被退回时；名称照样校验
    rs = f"/api/staff/activities/{act.id}/vb-confirms/resubmit"
    assert client.post(rs, headers=login(client, INITIATOR),
                       json={"names": ["改好的名称"]}).status_code == 403
    assert client.post(rs, headers=admin, json={"names": [" "]}).status_code == 400
    assert _history(client, admin, act)["can_resubmit"] is True
    # 锁定之后不能走「先存」那条路改名称
    assert client.put(f"/api/staff/activities/{act.id}/vb-names", headers=admin,
                      json={"names": ["改好的名称"]}).status_code == 409
    new_names = [NAMES[0], "校运会志愿服务（午后场）"]
    r = client.post(rs, headers=admin, json={"names": new_names})
    assert r.status_code == 200 and r.json()["kind"] == "resubmit", r.text
    # 重提不能连着来
    assert client.post(rs, headers=admin, json={"names": new_names}).status_code == 409

    # 外联再次收到；时长保持锁定，没有新的时长审核
    notes = inbox(db, LIAISON_CORE, f"志愿北京待确认｜{act.title}")
    assert len(notes) == 2 and "午后场" in notes[-1].content
    db.expire_all()
    a = db.get(Activity, act.id)
    assert a.hours_locked_at == locked_at and a.vb_names == new_names
    assert db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).count() == 1

    # 历史留痕：新的在前
    h = _history(client, admin, act)
    assert [(x["kind"], x["status"]) for x in h["items"]] == \
        [("resubmit", "pending"), ("lock", "returned")]
    assert h["items"][1]["reply"] == "志愿北京上查不到「下午」这一场"
    assert h["can_resubmit"] is False
    pending = _pending_confirm(client, core, act)
    assert pending["names"] == new_names and "查不到" in pending["last_return"]
    assert (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id, AuditLog.action == "vb_confirm")
            .count() >= 2)

    r = client.post(f"/api/staff/vb-confirms/{pending['id']}/decide", headers=core,
                    json={"approve": True})
    assert r.status_code == 200 and _status(db, act) == "completed"


# ======================= 解锁 =======================

@pytest.mark.parametrize("confirm_first", [False, True])
def test_解锁不作废外联确认_名称没变重新锁定沿用(client, admin, db, liaison,
                                              confirm_first):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    row = _pending_confirm(client, admin, act)
    if confirm_first:
        assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=admin,
                           json={"approve": True}).status_code == 200
        assert _status(db, act) == "completed"

    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏算了两位同学的时长"})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                    headers=admin, json={"approve": True, "reply": "同意，改完重新提交"})
    assert r.status_code == 200
    db.expire_all()
    a = db.get(Activity, act.id)
    assert a.status == "finished" and a.hours_locked_at is None
    # 解锁不动外联确认：已确认的仍然有效，待确认的继续等外联
    kept = db.get(VbConfirm, row["id"])
    assert kept.status == ("confirmed" if confirm_first else "pending")
    if not confirm_first:
        assert _pending_confirm(client, admin, act)["id"] == row["id"]
    got = inbox(db, ADMIN, f"时长解锁已通过｜{act.title}")
    assert got and "同意，改完重新提交" in got[0].content

    # 名称没变，重新锁定沿用上一次的结果，不再让外联重复核对
    _lock(client, admin, act, names=None)
    assert _status(db, act) == ("completed" if confirm_first else "vb_pending")
    db.expire_all()
    assert db.query(VbConfirm).filter(VbConfirm.activity_id == act.id).count() == 1
    assert len(inbox(db, LIAISON_HEAD, f"志愿北京待确认｜{act.title}")) == 1


def test_解锁后改名重新锁定_旧的待确认作废推新的(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    row = _pending_confirm(client, admin, act)
    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "名称也写错了"})
    assert client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                       headers=admin, json={"approve": True}).status_code == 200
    # 解锁之后名称又能改了
    assert client.put(f"/api/staff/activities/{act.id}/vb-names", headers=admin,
                      json={"names": ["改过的名称"]}).status_code == 200

    _lock(client, admin, act, names=None)
    assert _status(db, act) == "vb_pending"
    db.expire_all()
    assert db.get(VbConfirm, row["id"]).status == "void"
    rows = (db.query(VbConfirm).filter(VbConfirm.activity_id == act.id)
            .order_by(VbConfirm.submitted_at).all())
    assert [x.status for x in rows] == ["void", "pending"]
    assert rows[-1].names == ["改过的名称"]
    assert len(inbox(db, LIAISON_HEAD, f"志愿北京待确认｜{act.title}")) == 2


def test_已完成可以再次推送_活动保持已完成(client, admin, db, liaison):
    _set_duty(client, db, liaison)
    act = _approved_act(db, _office(db).id)
    url = f"/api/staff/activities/{act.id}/vb-confirms/repush"
    _lock(client, admin, act)
    row = _pending_confirm(client, admin, act)
    # 还在等外联确认时，这条路不开放
    assert client.post(url, headers=admin,
                       json={"names": ["重新建的活动"], "reason": "漏录"}).status_code == 409
    assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=admin,
                       json={"approve": True}).status_code == 200
    assert _status(db, act) == "completed"

    # 非管理者不能推；名称照样校验
    assert client.post(url, headers=login(client, INITIATOR),
                       json={"names": ["重新建的活动"], "reason": "漏录"}).status_code == 403
    assert client.post(url, headers=admin,
                       json={"names": [" "], "reason": "漏录"}).status_code == 400
    # 原因必填：已完成的活动又推过去，外联得知道为什么
    r = client.post(url, headers=admin, json={"names": ["重新建的活动"], "reason": " "})
    assert r.status_code == 400 and "原因" in r.json()["detail"]
    assert _history(client, admin, act)["can_repush"] is True

    new = ["校运会志愿服务（补录场）"]
    r = client.post(url, headers=admin,
                    json={"names": new, "reason": "志愿北京上漏录了补录场"})
    assert r.status_code == 200 and r.json()["kind"] == "repush", r.text
    assert r.json()["reason"] == "志愿北京上漏录了补录场"
    assert _status(db, act) == "completed"
    db.expire_all()
    assert db.get(Activity, act.id).vb_names == new

    # 外联收到新的一条
    core = login(client, LIAISON_CORE)
    pending = _pending_confirm(client, core, act)
    assert pending and pending["names"] == new and pending["kind"] == "repush"
    assert pending["reason"] == "志愿北京上漏录了补录场"
    assert any("推送原因：志愿北京上漏录了补录场" in n.content
               for who in (LIAISON_CORE, LIAISON_HEAD)
               for n in inbox(db, who, f"志愿北京待确认｜{act.title}"))

    # 再推一次：没处理的旧待确认作废
    r = client.post(url, headers=admin, json={"names": ["第三个名称"], "reason": "名称建错了"})
    assert r.status_code == 200
    db.expire_all()
    assert db.get(VbConfirm, pending["id"]).status == "void"
    latest = _pending_confirm(client, core, act)
    assert latest["names"] == ["第三个名称"]

    # 退回也只动记录，活动仍是已完成
    assert client.post(f"/api/staff/vb-confirms/{latest['id']}/decide", headers=core,
                       json={"approve": False,
                             "reply": "这个名称在志愿北京上也没有"}).status_code == 200
    assert _status(db, act) == "completed"


# ======================= 审批意见 =======================

def test_时长审批_通过意见选填_驳回无意见400(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id, names=NAMES)
    assert _submit(client, admin, act).status_code == 200
    r = _decide_hours(client, admin, act, approve=False, reply="")
    assert r.status_code == 400
    r = _decide_hours(client, admin, act, approve=False, reply=" x ")
    assert r.status_code == 400
    r = _decide_hours(client, admin, act, approve=False, reply="两人时长对不上")
    assert r.status_code == 200
    got = inbox(db, ADMIN, f"时长锁定未通过｜{act.title}")
    assert got and "两人时长对不上" in got[0].content
    # 通过不写意见也行
    assert _submit(client, admin, act).status_code == 200
    assert _decide_hours(client, admin, act, approve=True).status_code == 200
    items = client.get(f"/api/staff/activities/{act.id}/hours-requests",
                       headers=admin).json()["items"]
    # 提交与审批都是同一位管理员：两条都标「本人审批」
    assert [(x["status"], x["reply"]) for x in items] == \
        [("approved", "【本人审批】"), ("rejected", "【本人审批】两人时长对不上")]


def test_活动立项_通过意见随通知发出_驳回无意见400(client, admin, db, liaison):
    aid = make_draft(client, admin)
    title = db.get(Activity, aid).title
    upload_proposal(client, admin, aid)
    assert client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                       json={"department_id": liaison.id}).status_code == 200
    url = f"/api/staff/activities/{aid}/decide"
    assert client.post(url, headers=admin,
                       json={"approve": False, "comment": ""}).status_code == 400
    r = client.post(url, headers=admin, json={"approve": True, "comment": "注意安全"})
    assert r.status_code == 200, r.text
    got = inbox(db, ADMIN, f"审批通过｜{title}")
    assert got and "注意安全" in got[0].content
    events = client.get(f"/api/staff/activities/{aid}/approval", headers=admin).json()
    assert any("注意安全" in (e.get("comment") or "") for e in events["events"])


# ======================= 旧接口已移除 =======================

def test_旧的志愿北京登记与截图接口已移除(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    base = f"/api/staff/activities/{act.id}"
    assert client.get(f"{base}/vb-registrations", headers=admin).status_code == 404
    assert client.post(f"{base}/vb-registrations", headers=admin,
                       json={"vb_name": "x"}).status_code == 404
    assert client.get("/api/staff/vb-registrations", headers=admin).status_code == 404
    assert client.post(f"{base}/vb-hours/confirm", headers=admin).status_code == 404
    r = client.post(f"{base}/project-files/vb_hours", headers=admin,
                    files={"file": ("录入.png", b"\x89PNG", "image/png")})
    assert r.status_code == 404
    f = client.get(f"{base}/project-files", headers=admin).json()
    assert "vb_hours" not in f and "can_confirm_vb_hours" not in f


# ======================= 自动提交缺名称 =======================

def test_自动提交缺名称_不提交_每轮提醒一次(client, admin, db, liaison):
    act = _approved_act(db, _office(db).id)
    db.get(Activity, act.id).appeal_deadline = now() - timedelta(minutes=5)
    db.commit()
    title = f"请填写志愿北京活动名称｜{act.title}"

    def requests():
        db.expire_all()
        return db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).count()

    scheduled.submit_lock_when_due(db)
    assert requests() == 0 and len(inbox(db, ADMIN, title)) == 1
    scheduled.submit_lock_when_due(db)
    assert requests() == 0 and len(inbox(db, ADMIN, title)) == 1, "同一轮只提醒一次"

    assert client.put(f"/api/staff/activities/{act.id}/vb-names", headers=admin,
                      json={"names": NAMES}).status_code == 200
    db.expire_all()                  # 接口在另一个会话里改的
    scheduled.submit_lock_when_due(db)
    assert requests() == 1 and len(inbox(db, ADMIN, title)) == 1


# ======================= 提醒 =======================

def test_外联超过24小时未确认催一次(client, admin, db, liaison):
    _set_duty(client, db, liaison)
    stale_act = _approved_act(db, _office(db).id, names=NAMES)
    fresh_act = _approved_act(db, _office(db).id, names=NAMES)
    admin_id = student_row(db, ADMIN).id
    stale = VbConfirm(id=uid("vbc"), activity_id=stale_act.id, kind="lock",
                      names=NAMES, submitted_by=admin_id,
                      submitted_at=now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1))
    fresh = VbConfirm(id=uid("vbc"), activity_id=fresh_act.id, kind="lock",
                      names=NAMES, submitted_by=admin_id,
                      submitted_at=now() - timedelta(hours=config.APPROVAL_REMIND_HOURS - 1))
    db.add_all([stale, fresh])
    db.commit()
    db.expire_all()                  # 值班是接口在另一个会话里安排的

    def of(a):
        db.expire_all()
        return (db.query(Notification)
                .filter(Notification.title.like("志愿北京待确认已等待%"),
                        Notification.title.endswith(a.title)).all())

    scheduled.remind_stale_vb_confirms(db)
    assert [n.user_id for n in of(stale_act)] == [student_row(db, LIAISON_CORE).id]
    assert of(fresh_act) == [], "没满 24 小时不催"
    scheduled.remind_stale_vb_confirms(db)
    assert len(of(stale_act)) == 1, "每条只催一次"


def _at(day_offset: int, hour: int) -> datetime:
    base = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return base + timedelta(days=day_offset)


def test_退回后三天未重提每天提醒管理者(client, admin, db, liaison, monkeypatch):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    row = _pending_confirm(client, admin, act)
    assert client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=admin,
                       json={"approve": False, "reply": "名称对不上"}).status_code == 200
    title = f"请重新提交志愿北京活动名称｜{act.title}"
    t0 = _at(0, 10)

    def run(at):
        monkeypatch.setattr(scheduled, "now", lambda: at)
        scheduled.remind_vb_resubmit(db)
        return len(inbox(db, ADMIN, title))

    def returned_before(delta):
        db.expire_all()
        db.get(VbConfirm, row["id"]).handled_at = t0 - delta
        db.commit()

    returned_before(timedelta(days=2, hours=23))
    assert run(t0) == 0, "三天内不提醒"
    returned_before(timedelta(days=3, minutes=1))
    assert run(_at(0, 8)) == 0, "提醒点之前不发"
    assert run(t0) == 1
    assert "名称对不上" in inbox(db, ADMIN, title)[0].content
    assert run(t0 + timedelta(hours=5)) == 1, "同一天不重复"
    assert run(_at(1, 9)) == 2, "第二天再提醒一次"

    # 重提之后不再提醒
    monkeypatch.undo()
    assert client.post(f"/api/staff/activities/{act.id}/vb-confirms/resubmit",
                       headers=admin, json={"names": ["改好的名称"]}).status_code == 200
    assert run(_at(3, 10)) == 2


# ======================= 时长审批通知口径（原 test_vb_hours 保留） =======================

def _who_got(db, prefix):
    return {sid for sid in (ADMIN, COUNCIL, LIAISON_HEAD) if inbox(db, sid, prefix)}


def test_时长审批只通知对接人_不抄送会长团与管理员(client, admin, db, liaison):
    act = _approved_act(db, liaison.id, names=NAMES)
    assert upload_stamped(client, admin, act.id).status_code == 200
    assert _submit(client, admin, act).status_code == 200
    assert _who_got(db, f"时长锁定审批｜{act.title}") == {LIAISON_HEAD}
    head = login(client, LIAISON_HEAD)
    assert _decide_hours(client, admin, act).status_code == 200

    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏记了两个人的时长"})
    assert r.status_code == 200
    assert _who_got(db, f"时长解锁申请｜{act.title}") == {LIAISON_HEAD}
    assert client.post(f"/api/staff/hours-requests/{r.json()['id']}/decide",
                       headers=head, json={"approve": False, "reply": "先别动"}
                       ).status_code == 200


def test_找不到对接人就兜底发会长团(client, admin, db):
    """办公室没有部长；没归口部门的活动也一样。"""
    for dept_id in (_office(db).id, None):
        act = _approved_act(db, dept_id, names=NAMES)
        assert _submit(client, admin, act).status_code == 200
        # 管理员 2025001 同时在会长团，所以两人都收到
        assert _who_got(db, f"时长锁定审批｜{act.title}") == {ADMIN, COUNCIL}


def test_时长审批超时催对接人抄送会长团_只催一次(client, admin, db, liaison):
    act = _approved_act(db, liaison.id)
    admin_id = student_row(db, ADMIN).id
    stale = HoursRequest(id=uid("hrq"), activity_id=act.id, kind="lock",
                         reason="系统自动提交", requested_by=admin_id,
                         created_at=now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1))
    fresh_act = _approved_act(db, liaison.id)
    fresh = HoursRequest(id=uid("hrq"), activity_id=fresh_act.id, kind="lock",
                         reason="刚提交", requested_by=admin_id,
                         created_at=now() - timedelta(hours=config.APPROVAL_REMIND_HOURS - 1))
    db.add_all([stale, fresh])
    db.commit()

    def reminded(a) -> list[str]:
        db.expire_all()
        return sorted(n.user_id for n in db.query(Notification)
                      .filter(Notification.title.like("时长锁定审批已等待%"),
                              Notification.title.endswith(a.title)).all())

    from backend.routers.staff_ops import council_members
    expected = sorted({student_row(db, LIAISON_HEAD).id}
                      | {u.id for u in council_members(db)})
    scheduled.remind_stale_hours_requests(db)
    assert reminded(act) == expected
    assert reminded(fresh_act) == [], "没满 24 小时不催"
    scheduled.remind_stale_hours_requests(db)
    assert reminded(act) == expected, "每条只催一次"


def test_新的定时任务都接进了每轮(db):
    out = scheduled.run_once(db)
    assert {"hours_approvals", "vb_confirms", "vb_resubmit"} <= set(out)
    assert "vb_hours" not in out


def test_待外联确认与已完成都算活动已结束(client, admin, db, liaison):
    """按状态列表判断的地方要认这个状态：结构只读、展示为待外联确认。"""
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert d["status"] == "vb_pending" and d["status_label"] == "待外联确认"
    r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                    json={"name": "补一场", "mode": "pick",
                          "periods": [{"start_time": (now() + timedelta(days=1)).isoformat(),
                          "end_time": (now() + timedelta(days=1, hours=2)).isoformat()}]})
    assert r.status_code == 409
