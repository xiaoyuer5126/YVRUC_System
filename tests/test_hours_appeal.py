"""时长申诉：志愿者提异议，管理者采纳或驳回，与确认/回传的关系。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, HoursRecord, Notification,
                            Position, Shift, User, new_checkin_token, now,
                            span_text, uid)
from conftest import mk_shift
from conftest import ADMIN, POSITION_TEXT, login, student_row

# 手动加/扣时长那一段的服务日期
MANUAL_DATE = "2026-10-05"


@pytest.fixture
def served(db, take_student):
    """一个已结束的活动 + 一名签到签退过的志愿者（4 小时）。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"申诉测试 {uid('t')}",
                   intro="x", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="finished", reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5), vb_names=["申诉测试志愿服务"],
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    start = now().replace(microsecond=0) - timedelta(days=2)
    g = mk_shift(id=uid("g"), activity_id=act.id, name="服务场",
              start_time=start, end_time=start + timedelta(hours=4),
              mode="pick", sort=0)
    db.add(g)
    db.flush()
    pos = Position(id=uid("p"), shift_id=g.id, name="引导", quota=5, sort=0,
                   **POSITION_TEXT)
    db.add(pos)
    db.flush()
    u = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, position_id=pos.id, status="enrolled",
                      joined_at=now()))
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       period_id=g.periods[0].id,
                       mode="shift", token=new_checkin_token(),
                       created_by=admin.id,
                       valid_from=g.start_time, valid_to=g.end_time)
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, period_id=g.periods[0].id,
                         position_id=pos.id, user_id=u.id,
                         method="manual", checkin_at=g.start_time,
                         checkout_at=g.end_time))
    db.commit()
    return act, g, u


def _enrolled(db, act, sid: str) -> User:
    """再报上一名志愿者（这场活动的第一个场次与岗位），不签到。

    手动加时长只给已报名的人：时长是这场活动的，没报名就没有服务依据。"""
    u = student_row(db, sid)
    pos = (db.query(Position).join(Shift, Position.shift_id == Shift.id)
           .filter(Shift.activity_id == act.id).order_by(Shift.sort).first())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=pos.shift_id,
                      user_id=u.id, position_id=pos.id, status="enrolled",
                      joined_at=now()))
    db.commit()
    return u


def _confirm(client, admin, act, *, approve=True):
    """算出时长；默认接着核准，志愿者这才看得到、也才能申诉。"""
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    if approve:
        r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                        headers=admin, json={})
        assert r.status_code == 200, r.text


def _record(client, h):
    return client.get("/api/me/hours", headers=h).json()["records"][0]


def _approve_lock(client, admin, act, *, approve=True, reply="核对无误"):
    """校青协那一步：找到挂着的锁定申请，拍板。"""
    rows = [x for x in client.get("/api/staff/hours-requests", headers=admin).json()
            if x["activity_id"] == act.id and x["kind"] == "lock"]
    assert len(rows) == 1, rows
    r = client.post(f"/api/staff/hours-requests/{rows[0]['id']}/decide",
                    headers=admin, json={"approve": approve, "reply": reply})
    assert r.status_code == 200, r.text
    return rows[0]


def _lock_requests(client, admin, act):
    """这场活动挂着的时长审批。只看本活动：定时任务扫的是全库，
    别的用例留下的活动也会被它顺手送审。"""
    return [x for x in client.get("/api/staff/hours-requests", headers=admin).json()
            if x["activity_id"] == act.id]


def _appeal_window_closes(db, act):
    """把确认/申诉截止时间拨到刚刚过去，再跑一轮定时任务。"""
    from backend import scheduled

    db.expire_all()
    db.get(Activity, act.id).appeal_deadline = now() - timedelta(minutes=1)
    db.commit()
    scheduled.submit_lock_when_due(db)


def test_申诉全流程_采纳(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["hours"] == 4.0 and rec["can_appeal"]

    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "忘记签退，实际 15:30 就走了"})
    assert r.status_code == 200, r.text

    rows = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                      headers=admin).json()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["old_hours"] == 4.0 and rows[0]["claimed_hours"] == 3.0

    r = client.post(f"/api/staff/hours-appeals/{rows[0]['id']}/resolve",
                    headers=admin, json={"accept": True, "reply": "已按实际核减"})
    assert r.status_code == 200 and r.json()["hours"] == 3.0

    after = _record(client, stu)
    assert after["hours"] == 3.0
    assert after["appeal"]["status"] == "accepted" and not after["can_appeal"]


def test_驳回后可以再提一次(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 8, "reason": "我觉得应该算八小时"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": False, "reply": "签到记录显示 4 小时"})

    after = _record(client, stu)
    assert after["hours"] == 4.0 and after["appeal"]["status"] == "rejected"
    assert after["can_appeal"]        # 驳回了还能再说一次
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json={"claimed_hours": 5,
                             "reason": "补充：结束后还留下收拾了一小时"}
                       ).status_code == 200


def test_同时只能有一条待处理(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    body = {"claimed_hours": 3, "reason": "忘记签退了，实际提前走的"}
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json=body).status_code == 200
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json=body).status_code == 409


def test_理由太短会被挡住(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "不对"})
    assert r.status_code == 400


def test_不能申诉别人的时长(client, admin, db, served, take_student):
    act, _g, _u = served
    _confirm(client, admin, act)
    other = login(client, take_student())
    rec = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).one()
    r = client.post(f"/api/me/hours/{rec.id}/appeal", headers=other,
                    json={"claimed_hours": 3, "reason": "这条不是我的记录"})
    assert r.status_code == 404


def test_采纳过的时长不会被一键确认推回去(client, admin, db, served):
    """改判是人判的，再点一次「确认」不该把它冲掉。"""
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": True})

    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert "保留不覆盖" in r.json()["message"]
    assert _record(client, stu)["hours"] == 3.0


def test_核准前看不到也申诉不了(client, admin, db, served):
    """确认只是把账算出来放在管理端，核准了才通知本人、才开申诉窗口。"""
    act, _g, u = served
    _confirm(client, admin, act, approve=False)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["approved"] is False and rec["can_appeal"] is False
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    assert r.status_code == 409 and "尚未核准" in r.json()["detail"]

    # 核准会通知每个人，并给出申诉截止时间
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["count"] == 1
    rec = _record(client, stu)
    assert rec["approved"] and rec["can_appeal"] and rec["appeal_deadline"]
    assert (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("请确认志愿时长%")).count() == 1)


def test_到点无人申诉自动提交审批_通过后才锁定(client, admin, db, served):
    from backend import scheduled

    act, _g, u = served
    _confirm(client, admin, act)
    _appeal_window_closes(db, act)

    assert [x["kind"] for x in _lock_requests(client, admin, act)] == ["lock"]
    db.expire_all()
    assert db.get(Activity, act.id).hours_locked_at is None, "等校青协点头才算数"
    # 审批期间冻结：账送上去了就不能再动
    assert client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin, json={}).status_code == 409
    # 挂着一条就不重复提交
    scheduled.submit_lock_when_due(db)
    assert len(_lock_requests(client, admin, act)) == 1

    _approve_lock(client, admin, act)
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.hours_locked_at is not None and row.status == "vb_pending"
    assert (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("志愿时长已锁定%")).count() == 1)

    # 锁定之后：志愿者申诉不了，管理者也改不动
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["locked"] and not rec["can_appeal"]
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json={"claimed_hours": 3, "reason": "忘记签退了，实际提前走"}
                       ).status_code == 409
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 409 and "已于" in r.json()["detail"]


def test_有人申诉就不自动提交审批(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                       json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"}
                       ).status_code == 200
    _appeal_window_closes(db, act)

    assert _lock_requests(client, admin, act) == []

    # 处理完申诉，管理者可以自己提交审批，不必再等一轮
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": True})
    r = client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                    headers=admin, json={"note": "已按申诉改判"})
    assert r.status_code == 200, r.text
    _approve_lock(client, admin, act)
    db.expire_all()
    assert db.get(Activity, act.id).status == "vb_pending"


def test_锁定审批被驳回后解冻重改(client, admin, db, served):
    """驳回 = 退回管理者：时长又能改了，改完重新核准再提交一次。"""
    act, _g, _u = served
    _confirm(client, admin, act)
    client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                headers=admin, json={"note": "先送一次"})
    assert client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin, json={}).status_code == 409

    _approve_lock(client, admin, act, approve=False, reply="有两人的时长对不上，请核对")
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.hours_locked_at is None and row.status == "finished"
    # 解冻了：改得动，也能再提交一次
    assert client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin, json={}).status_code == 200
    assert client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                       headers=admin, json={}).status_code == 200
    _approve_lock(client, admin, act)
    db.expire_all()
    assert db.get(Activity, act.id).status == "vb_pending"


def test_锁定后改时长要报审批(client, admin, db, served):
    act, _g, _u = served
    _confirm(client, admin, act)
    client.post(f"/api/staff/activities/{act.id}/hours/lock-request",
                headers=admin, json={})
    _approve_lock(client, admin, act)
    # 理由太短拦下
    assert client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                       headers=admin, json={"reason": "改"}).status_code == 400
    r = client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                    headers=admin, json={"reason": "漏算了两位同学的加班时长"})
    assert r.status_code == 200, r.text
    req_id = r.json()["id"]
    # 一次只能挂一条
    assert client.post(f"/api/staff/activities/{act.id}/hours/unlock-request",
                       headers=admin,
                       json={"reason": "再来一条应当被拦下"}).status_code == 409

    rows = [x for x in client.get("/api/staff/hours-requests", headers=admin).json()
            if x["activity_id"] == act.id]
    assert [(x["id"], x["kind"]) for x in rows] == [(req_id, "unlock")]
    r = client.post(f"/api/staff/hours-requests/{req_id}/decide", headers=admin,
                    json={"approve": True, "reply": "同意，改完重新核准"})
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Activity, act.id)
    assert row.hours_locked_at is None and row.status == "finished"
    # 解锁之后又能改了
    assert client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin, json={}).status_code == 200


def test_没有时长记录的活动核准即锁定(client, admin, db, served):
    """一条时长都没有的活动没什么可申诉的，直接结账；志愿北京也没什么可录的，
    不推外联，直接完成。"""
    act, _g, _u = served
    db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).delete()
    db.commit()
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["locked"] is True
    db.expire_all()
    assert db.get(Activity, act.id).status == "completed"


def test_非本活动管理者不能处理申诉(client, admin, initiator, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    assert client.post(f"/api/staff/hours-appeals/{a['id']}/resolve",
                       headers=initiator, json={"accept": True}).status_code == 403


# ======================= 本人确认与报酬方式 =======================

def _served_pay_position(db, act):
    pos = (db.query(Position).join(Shift, Position.shift_id == Shift.id)
           .filter(Shift.activity_id == act.id).first())
    pos.pay_optional, pos.pay_note = True, "按校内标准，活动后一周内发放"
    db.commit()
    return pos


def _choose(client, h, rec, reward):
    """给这笔时长里每一段签到算出来的段都选同一种报酬方式。"""
    return client.post(f"/api/me/hours/{rec['id']}/confirm", headers=h, json={
        "parts": [{"shift_id": p["shift_id"], "position_id": p["position_id"],
                   "reward": reward} for p in rec["parts"] if p["kind"] == "shift"]})


def test_确认无误并选劳务就不记时长(client, admin, db, served):
    """岗位开了劳务，本人核准后可以选劳务；账上这次就不算志愿时长。"""
    act, _g, u = served
    _served_pay_position(db, act)
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["can_confirm"] and rec["pay_allowed"]
    assert rec["parts"][0]["pay_ok"] and "校内标准" in rec["parts"][0]["pay_note"]
    r = _choose(client, stu, rec, "pay")
    assert r.status_code == 200 and r.json()["pay_hours"] == 4.0
    d = client.get("/api/me/hours", headers=stu).json()
    assert d["total"] == 0 and d["records"][0]["reward_label"] == "劳务"
    assert d["records"][0]["confirmed_at"]
    # 截止前可以改主意
    _choose(client, stu, rec, "hours")
    assert client.get("/api/me/hours", headers=stu).json()["total"] == 4.0


def test_岗位没开劳务就选不了(client, admin, db, served):
    act, _g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert not rec["pay_allowed"] and not rec["parts"][0]["pay_ok"]
    r = _choose(client, stu, rec, "pay")
    assert r.status_code == 400 and "没有开放劳务" in r.json()["detail"]
    assert _choose(client, stu, rec, "hours").status_code == 200


def test_到点未表态视同确认并按志愿时长记(client, admin, db, served):
    act, _g, u = served
    _served_pay_position(db, act)
    _confirm(client, admin, act)
    _appeal_window_closes(db, act)
    _approve_lock(client, admin, act)
    db.expire_all()
    rec = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).one()
    assert rec.confirmed_at is not None and rec.pay_hours() == 0
    stu = login(client, u.student_id)
    assert client.get("/api/me/hours", headers=stu).json()["locked"] == 4.0
    # 锁定之后改不了主意
    assert _choose(client, stu, _record(client, stu), "pay").status_code == 409


def test_再次核准只惊动申诉过或数值变过的人(client, admin, db, served, take_student):
    """别人已经确认过、数值也没动，第二轮不该再收一遍通知、也不该丢掉确认。"""
    from backend.models import HoursAppeal

    act, g, u = served
    # 再来一位志愿者：手动补录 2 小时，第一轮就确认无误
    other = _enrolled(db, act, take_student())
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual", headers=admin,
                    json={"items": [{"student_id": other.student_id, "hours": 2, "note": "场外补录"}], "date": MANUAL_DATE})
    assert r.status_code == 200, r.text
    _confirm(client, admin, act)
    o = login(client, other.student_id)
    orec = _record(client, o)
    assert client.post(f"/api/me/hours/{orec['id']}/confirm", headers=o,
                       json={}).status_code == 200

    stu = login(client, u.student_id)
    rec = _record(client, stu)
    client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                json={"claimed_hours": 3, "reason": "忘记签退，实际提前走了"})
    assert not _record(client, stu)["can_confirm"]     # 申诉处理中不能确认
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                json={"accept": True})

    def approved_notices(user_id):
        return (db.query(Notification)
                .filter(Notification.user_id == user_id,
                        Notification.title.like("请确认志愿时长%")).count())

    r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                    headers=admin, json={})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert approved_notices(u.id) == 2 and approved_notices(other.id) == 1
    assert _record(client, o)["confirmed_at"]            # 确认照旧有效
    after = _record(client, stu)
    assert after["hours"] == 3.0 and not after["confirmed_at"] and after["can_confirm"]


# ======================= 明细与导出 =======================

def test_时长带明细且导出时长记录(client, admin, db, served, take_student):
    from io import BytesIO
    from openpyxl import load_workbook

    act, g, u = served
    u.id_card = "110101200001011234"
    other = _enrolled(db, act, take_student())
    db.commit()
    _confirm(client, admin, act, approve=False)
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual", headers=admin,
                    json={"items": [{"student_id": other.student_id, "hours": 2,
                                     "note": "场外搬运"}], "date": MANUAL_DATE})
    assert r.status_code == 200, r.text

    stu = login(client, u.student_id)
    rec = _record(client, stu)
    pos = db.query(Position).filter(Position.shift_id == g.id).one()
    seg = rec["parts"][0]
    assert seg.pop("id")                       # 每段都有编号
    assert rec["parts"] == [{"label": "服务场", "time": g.span_text(),
                             "position": "引导", "hours": 4.0, "kind": "shift",
                             "shift_id": g.id, "period_id": g.periods[0].id,
                             "position_id": pos.id,
                             "date": f"{g.start_time:%Y-%m-%d}",
                             "pay_ok": False, "pay_note": "", "reward": "hours"}]

    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    assert r.status_code == 200
    ws = load_workbook(BytesIO(r.content)).active
    rows = list(ws.values)
    assert rows[0] == ("姓名", "学号", "身份证号", "时长", "备注")
    by_name = {r_[0]: r_ for r_ in rows[1:]}
    # 没有「岗位」列：岗位写在备注的明细里
    assert by_name[u.name] == (u.name, u.student_id, "110101200001011234", 4.0,
                               f"{g.span_text()} 引导")
    assert by_name[other.name][1] == other.student_id
    assert by_name[other.name][3] == 2 and by_name[other.name][4] == "10-05 场外搬运"

    # 选了劳务的不导
    _served_pay_position(db, act)
    client.post(f"/api/staff/activities/{act.id}/hours/approve", headers=admin, json={})
    _choose(client, stu, rec, "pay")
    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    names = [r_[0] for r_ in list(load_workbook(BytesIO(r.content)).active.values)[1:]]
    assert u.name not in names and other.name in names


# ======================= 手动补录：并进本人那一笔 =======================
#
# 手动加的时长要是另起一条记录：合计分成两处、核准发两条通知、本人得确认两回，
# 管理者那张总表里还看不见这笔账。所以并进这个人这场活动的那一笔，
# 手动的部分作为明细里的一段存在（kind="manual"）。

def _add_manual(client, admin, act, sid, hours, note=""):
    return client.post(f"/api/staff/activities/{act.id}/hours/manual",
                       headers=admin,
                       json={"items": [{"student_id": sid, "hours": hours,
                                        "note": note}], "date": MANUAL_DATE})


def test_手动时长并进本人那一笔(client, admin, db, served):
    act, g, u = served
    _confirm(client, admin, act, approve=False)
    stu = login(client, u.student_id)
    assert _record(client, stu)["hours"] == 4.0

    assert _add_manual(client, admin, act, u.student_id, 2,
                       "场外搬运").status_code == 200

    row = _record(client, stu)
    assert row["hours"] == 6.0                      # 合计是一个数
    assert [p["kind"] for p in row["parts"]] == ["shift", "manual"]
    assert row["parts"][1]["label"] == "场外搬运"
    # 本人那边只有一笔账，不是同一个活动列两行
    assert len(client.get("/api/me/hours", headers=stu).json()["records"]) == 1

    # 管理者那张总表也是合起来的，没有另外一块「手动添加时长」
    d = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()
    assert "manual" not in d
    m = next(x for x in d["items"] if x["user"]["id"] == u.id)
    assert m["hours"] == 6.0 and len(m["parts"]) == 2

    # 导出的「备注」列不是另写的一句说明，是把明细一段段拼起来——
    # 签到算出来的那段和手动加的那段都在里头
    from io import BytesIO

    from openpyxl import load_workbook
    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    xrow = next(x for x in load_workbook(BytesIO(r.content)).active.values
                if x[0] == u.name)
    assert xrow[3] == 6.0 and xrow[4] == f"{g.span_text()} 引导；10-05 场外搬运"


def test_确认全部不会抹掉手动加的那几段(client, admin, db, served):
    """「确认全部」每次重算明细——不特意保住手动那几段，管理者加的东西就没了。

    这正是当初把它拆成单独一张表的原因，现在改成拆开记、合起来算。
    """
    act, g, u = served
    _confirm(client, admin, act, approve=False)
    _add_manual(client, admin, act, u.student_id, 2, "场外搬运")

    client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                headers=admin, json={})

    stu = login(client, u.student_id)
    row = _record(client, stu)
    assert row["hours"] == 6.0
    assert [p["kind"] for p in row["parts"]] == ["shift", "manual"]


def test_只补录过的人也在总表里(client, admin, db, served, take_student):
    """没签到、只被手动补录的人也得出现在这张表里，不然这笔账就没人核。"""
    act, g, u = served
    other = _enrolled(db, act, take_student())
    _add_manual(client, admin, act, other.student_id, 1.5, "场外搬运")

    d = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()
    m = next(x for x in d["items"] if x["user"]["id"] == other.id)
    assert m["hours"] == 1.5 and m["confirmed"] is True
    assert m["parts"][0]["kind"] == "manual"


def test_删手动那一段只减那一截(client, admin, db, served):
    act, g, u = served
    _confirm(client, admin, act, approve=False)
    _add_manual(client, admin, act, u.student_id, 2, "场外搬运")
    _add_manual(client, admin, act, u.student_id, 1, "通勤")

    stu = login(client, u.student_id)
    row = _record(client, stu)
    assert row["hours"] == 7.0 and len(row["parts"]) == 3
    rid, seg = row["id"], row["parts"][2]          # 最后一段是「通勤」
    assert seg["label"] == "通勤"

    # 签到算出来的那一段删不了：它是账的依据
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rid}/{row['parts'][0]['id']}",
        headers=admin).status_code == 404
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rid}/none",
        headers=admin).status_code == 404

    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rid}/{seg['id']}",
        headers=admin).status_code == 200
    row = _record(client, stu)
    assert row["hours"] == 6.0 and len(row["parts"]) == 2
    assert row["parts"][1]["label"] == "场外搬运"


def test_没确认过也删得对那一段(client, admin, db, served):
    """管理端的明细是现拼的（签到段在前、手动段在后），而没「确认全部」过的人，
    库里那一笔只存了手动那几段。两边顺序不一样，按位置删会删到另一段上。"""
    act, g, u = served
    _add_manual(client, admin, act, u.student_id, 2, "场外搬运")
    _add_manual(client, admin, act, u.student_id, 1, "通勤")

    def mine():
        d = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()
        return next(x for x in d["items"] if x["user"]["id"] == u.id)

    m = mine()
    assert [p["label"] for p in m["parts"]] == ["服务场", "场外搬运", "通勤"]
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{m['record_id']}/"
        f"{m['parts'][1]['id']}", headers=admin).status_code == 200

    m = mine()
    assert [p["label"] for p in m["parts"]] == ["服务场", "通勤"]
    assert m["hours"] == 5.0


def test_手动时长删空之后整笔账消失(client, admin, db, served, take_student):
    """本来源自补录的一笔，删空就不该留一条 0 小时的记录在表里。"""
    act, g, u = served
    other = _enrolled(db, act, take_student())
    _add_manual(client, admin, act, other.student_id, 2, "场外搬运")

    d = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()
    m = next(x for x in d["items"] if x["user"]["id"] == other.id)
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{m['record_id']}/"
        f"{m['parts'][0]['id']}", headers=admin).status_code == 200

    d = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()
    assert not [x for x in d["items"] if x["user"]["id"] == other.id]


def test_时长核准通知把人带到我的志愿时长(client, admin, db, served):
    """那笔账的确认/申诉按钮在「我的志愿时长」，活动详情页上没有。"""
    act, g, u = served
    _confirm(client, admin, act)
    n = (db.query(Notification)
         .filter(Notification.user_id == u.id,
                 Notification.title.like("请确认志愿时长%")).first())
    assert n is not None and n.data == {"route": "hours"}


def test_确认无误之后就不能再申诉(client, admin, db, served):
    """「确认无误」是对着这个数字说的，说过之后前端不给入口，后端也挡住。"""
    act, g, u = served
    _confirm(client, admin, act)
    stu = login(client, u.student_id)
    rec = _record(client, stu)
    assert rec["can_appeal"] is True

    client.post(f"/api/me/hours/{rec['id']}/confirm", headers=stu, json={})
    after = _record(client, stu)
    assert after["can_appeal"] is False
    assert after["can_confirm"] is True            # 还能改报酬方式

    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "实际提前走了"})
    assert r.status_code == 409 and "已确认" in r.json()["detail"]


def test_手动扣减要写备注也不能扣成负数(client, admin, db, served):
    """扣减和添加走同一个接口，传负数。手动动的就是账，备注必须写清楚。"""
    act, _g, u = served
    _confirm(client, admin, act, approve=False)
    url = f"/api/staff/activities/{act.id}/hours/manual"

    r = client.post(url, headers=admin,
                    json={"items": [{"student_id": u.student_id, "hours": -1}], "date": MANUAL_DATE})
    assert r.status_code == 400 and "备注必填" in r.json()["detail"]

    r = client.post(url, headers=admin,
                    json={"items": [{"student_id": u.student_id, "hours": -99,
                                     "note": "记错了"}], "date": MANUAL_DATE})
    assert r.status_code == 400 and "扣不了" in r.json()["detail"]

    r = client.post(url, headers=admin,
                    json={"items": [{"student_id": u.student_id, "hours": -1.5,
                                     "note": "中途离场 1.5 小时"}], "date": MANUAL_DATE})
    assert r.status_code == 200, r.text

    stu = login(client, u.student_id)
    row = _record(client, stu)
    assert row["hours"] == 2.5                     # 4 - 1.5
    assert row["parts"][-1]["hours"] == -1.5       # 那段是负数，明细里看得出来
    assert row["parts"][-1]["kind"] == "manual"


def test_没有时长记录就无从扣减(client, admin, db, served, take_student):
    """扣减要有个账可扣：这场活动他一条记录都没有，扣什么？"""
    act, _g, _u = served
    other = _enrolled(db, act, take_student())
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual", headers=admin,
                    json={"items": [{"student_id": other.student_id,
                                     "hours": -1, "note": "记错了"}], "date": MANUAL_DATE})
    assert r.status_code == 400 and "没有可扣的" in r.json()["detail"]


def test_明细写的是实际签到签退那一段不是场次时段(client, admin, db, served):
    """迟到早退的人，明细里写场次时段就对不上账了：标着「09:00-12:00」，
    小时数却只有 2 小时出头，谁看都得问一句。写实际那一段，数就解释得通。

    明细里的小时数是**原始值**（2 小时 10 分 → 2.17），只有合计才按
    「向上取整到 0.5」换算成 2.5——取整是给整笔账定的政策，明细记的是事实。
    """
    act, g, u = served
    late = g.start_time + timedelta(minutes=40)
    left = g.end_time - timedelta(minutes=70)
    (db.query(CheckinRecord).filter(CheckinRecord.activity_id == act.id)
       .update({"checkin_at": late, "checkout_at": left}))
    db.commit()

    _confirm(client, admin, act, approve=False)
    row = _record(client, login(client, u.student_id))
    assert row["hours"] == 2.5                     # 合计：2 小时 10 分向上取整
    assert row["parts"][0]["hours"] == 2.17        # 明细：实际那一段
    # 跨不跨零点格式不同（见 span_text），跑用例的钟点不同，这段可能跨天
    assert row["parts"][0]["time"] == span_text(late, left)
    assert row["parts"][0]["time"] != g.span_text()
    assert row["parts"][0]["position"] == "引导"


def test_多个岗位就是多句话(client, admin, db, served):
    """一天赶两场：明细两段、备注两句话，各带各的时段和岗位名。"""
    from io import BytesIO

    from openpyxl import load_workbook

    act, g, u = served
    g2 = mk_shift(id=uid("g"), activity_id=act.id, name="晚场",
               start_time=g.end_time + timedelta(days=1),
               end_time=g.end_time + timedelta(days=1, hours=3),
               mode="pick", sort=1)
    db.add(g2)
    db.flush()
    pos2 = Position(id=uid("p"), shift_id=g2.id, name="登记", quota=5, sort=0,
                    **POSITION_TEXT)
    db.add(pos2)
    db.flush()
    task2 = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g2.id,
                        period_id=g2.periods[0].id,
                        mode="shift", token=new_checkin_token(),
                        created_by=act.created_by, valid_from=g2.start_time,
                        valid_to=g2.end_time)
    db.add(task2)
    db.flush()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g2.id,
                      user_id=u.id, position_id=pos2.id, status="enrolled",
                      joined_at=now()))
    db.add(CheckinRecord(id=uid("ckr"), task_id=task2.id, activity_id=act.id,
                         shift_id=g2.id, period_id=g2.periods[0].id,
                         position_id=pos2.id, user_id=u.id,
                         method="manual", checkin_at=g2.start_time,
                         checkout_at=g2.end_time))
    db.commit()

    _confirm(client, admin, act, approve=False)
    stu = login(client, u.student_id)
    parts = _record(client, stu)["parts"]
    assert [p["position"] for p in parts] == ["引导", "登记"]
    assert [p["hours"] for p in parts] == [4.0, 3.0]

    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    ws = load_workbook(BytesIO(r.content)).active
    note = next(x for x in ws.values if x[0] == u.name)[4]
    assert note == (f"{g.span_text()} 引导；{g2.span_text()} 登记")


# ======================= 扣减与删段：账不能凭空变多 =======================

def _row(client, admin, act, user_id):
    d = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()
    return next((x for x in d["items"] if x["user"]["id"] == user_id), None)


def test_删段不截到零_账不会凭空多出来(client, admin, db, served):
    """加 2、扣 5、删掉加的那段：要是截成 0，再删扣的那段就多出 1 小时。"""
    act, g, u = served            # 签到 4 小时
    _confirm(client, admin, act, approve=False)
    assert _add_manual(client, admin, act, u.student_id, 2, "场外搬运").status_code == 200
    assert _add_manual(client, admin, act, u.student_id, -5, "中途离场").status_code == 200
    m = _row(client, admin, act, u.id)
    assert m["hours"] == 1.0
    plus = next(p for p in m["parts"] if p.get("hours") == 2)
    minus = next(p for p in m["parts"] if p.get("hours") == -5)
    url = f"/api/staff/activities/{act.id}/hours/manual/{m['record_id']}/"

    # 加的那段已经被扣减用掉了，不许单独删
    assert client.delete(url + plus["id"], headers=admin).status_code == 409
    # 按顺序删：先删扣减，再删加的，回到签到的 4 小时
    assert client.delete(url + minus["id"], headers=admin).status_code == 200
    assert client.delete(url + plus["id"], headers=admin).status_code == 200
    db.expire_all()
    assert db.get(HoursRecord, m["record_id"]).hours == 4.0
    assert _row(client, admin, act, u.id)["hours"] == 4.0


def test_扣到零的人仍在总表里(client, admin, db, served, take_student):
    act, g, u = served
    other = _enrolled(db, act, take_student())
    _add_manual(client, admin, act, other.student_id, 2, "场外搬运")
    assert _add_manual(client, admin, act, other.student_id, -2, "其实没去").status_code == 200
    m = _row(client, admin, act, other.id)
    assert m is not None and m["hours"] == 0 and len(m["parts"]) == 2


def test_没确认过时扣签到时长会提示先确认(client, admin, served):
    act, g, u = served
    _add_manual(client, admin, act, u.student_id, 1, "场外搬运")
    r = _add_manual(client, admin, act, u.student_id, -3, "中途离场")
    assert r.status_code == 400 and "确认全部" in r.json()["detail"]


def test_时长拒绝非有限数(client, admin, served):
    act, g, u = served
    for bad in ("nan", "inf", "-inf"):
        r = _add_manual(client, admin, act, u.student_id, bad, "x")
        assert r.status_code == 400 and "不是有效数字" in r.json()["detail"], bad


def test_同一批里同一人出现两次只有一笔(client, admin, db, served, take_student):
    act, g, u = served
    other = _enrolled(db, act, take_student())
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual", headers=admin,
                    json={"items": [
                        {"student_id": other.student_id, "hours": 2, "note": "搬运"},
                        {"student_id": other.student_id, "hours": -1, "note": "早退"}], "date": MANUAL_DATE})
    assert r.status_code == 200
    db.expire_all()
    recs = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id,
                                        HoursRecord.user_id == other.id).all()
    assert len(recs) == 1 and recs[0].hours == 1.0


# ======================= 党校同学 =======================

def test_党校同学_时长带标签_不进导出_不计入本人累计(client, admin, db, served,
                                       take_student):
    """党校同学只能经补招加进来；他们的时长不能同时拿去做学分认定。"""
    from io import BytesIO
    from openpyxl import load_workbook

    act, g, u = served
    party = student_row(db, take_student())
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": party.student_id, "party_school": True,
                          "force": True})       # 这一场名额已经排满
    assert r.status_code == 200, r.text
    db.expire_all()
    assert [e.source for e in db.query(Enrollment).filter(
        Enrollment.activity_id == act.id, Enrollment.user_id == party.id)] == ["party_school"]
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual", headers=admin,
                    json={"items": [{"student_id": party.student_id, "hours": 3,
                                     "note": "场外支援"}], "date": MANUAL_DATE})
    assert r.status_code == 200, r.text
    _confirm(client, admin, act)

    # 时长管理里一眼看得出来
    rows = client.get(f"/api/staff/activities/{act.id}/hours", headers=admin).json()["items"]
    assert {x["user"]["id"]: x["party_school"] for x in rows} == {u.id: False, party.id: True}
    # 导出表（给志愿北京录时长的）里没有他
    r = client.get(f"/api/staff/activities/{act.id}/hours.xlsx", headers=admin)
    names = [x[0] for x in list(load_workbook(BytesIO(r.content)).active.values)[1:]]
    assert u.name in names and party.name not in names
    # 本人看得到这条、带标签，但累计时长不算它
    mine = client.get("/api/me/hours", headers=login(client, party.student_id)).json()
    assert [(x["party_school"], x["hours"]) for x in mine["records"]] == [(True, 3)]
    assert mine["total"] == 0
    # 别人照常
    assert client.get("/api/me/hours", headers=login(client, u.student_id)).json()["total"] == 4
