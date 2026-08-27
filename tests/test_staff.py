"""管理端：草稿可见性、活动级权限、分配留痕、补录闸门、时长待核。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, Shift, Position,
                            User, now, uid)
from conftest import ADMIN, POSITION_TEXT, QINGXIE, editing, login


@pytest.fixture
def draft_activity(db):
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title="尚未发布的草稿活动", type="A",
                   intro="筹备中", location="待定",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   qa_group_qr="x", status="draft",
                   reg_start=now(), reg_end=now() + timedelta(days=3),
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    db.add(Shift(id=uid("g"), activity_id=act.id, name="草稿场次",
                         start_time=now() + timedelta(days=40),
                         end_time=now() + timedelta(days=40, hours=3),
                         capacity=5, mode="single", sort=0))
    db.commit()
    return act


def test_draft_hidden_from_students(client, draft_activity, take_student):
    """筹备中的活动没定稿，学生不该在广场上看到、也不该能直接翻到详情。"""
    h = login(client, take_student())

    for scope in ("open", "all"):
        titles = [a["title"] for a in
                  client.get(f"/api/activities?scope={scope}", headers=h).json()]
        assert draft_activity.title not in titles

    assert client.get(f"/api/activities/{draft_activity.id}",
                      headers=h).status_code == 404


def test_draft_visible_to_its_manager(client, admin, draft_activity):
    assert client.get(f"/api/activities/{draft_activity.id}",
                      headers=admin).status_code == 200


def test_non_manager_cannot_touch_others_activity(client, teacher,
                                                  draft_activity):
    """老师只能管自己参与共管的活动。"""
    r = client.patch(f"/api/staff/activities/{draft_activity.id}",
                     json={"location": "被别人改了"}, headers=teacher)
    assert r.status_code == 403


@pytest.fixture
def assigned_group(db):
    """一个二级待定的场次，两个岗位，两个已确认的人。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"分配测试 {uid('t')}", type="A",
                   intro="x", location="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   qa_group_qr="x", status="closed",
                   reg_start=now() - timedelta(days=2), reg_end=now(),
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="待分配场次",
                      start_time=now() + timedelta(days=50),
                      end_time=now() + timedelta(days=50, hours=4),
                      capacity=4, mode="assign", sort=0)
    db.add(g)
    db.flush()
    s1 = Position(id=uid("s"), shift_id=g.id, name="岗位甲", quota=2, sort=0)
    s2 = Position(id=uid("s"), shift_id=g.id, name="岗位乙", quota=2, sort=1)
    db.add_all([s1, s2])
    db.commit()
    return act, g, s1, s2


def test_assignment_audit_records_the_old_position(client, admin, db,
                                                  assigned_group, take_student):
    """「谁动了我的岗位」：留痕里的旧值必须是真的旧岗位。"""
    act, g, s1, s2 = assigned_group
    sid = take_student()
    u = db.query(User).filter(User.student_id == sid).one()
    enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                     user_id=u.id, status="confirmed", position_id=s1.id,
                     joined_at=now())
    db.add(enr)
    db.commit()

    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": enr.id,
                                    "position_id": s2.id}],
                          "source": "manual"})
    assert r.status_code == 200
    assert r.json()["rows"][0]["from_position_name"] == "岗位甲"

    logs = client.get(f"/api/staff/audit-logs?activity_id={act.id}",
                      headers=admin).json()["items"]
    change = next(c for l in logs for c in l["changes"]
                  if c["label"] == u.name)
    assert change["old"] == "岗位甲" and change["new"] == "岗位乙"


def test_moving_unwilling_volunteer_is_allowed_but_recorded(
        client, admin, db, assigned_group, take_student):
    """志愿者说不接受调剂也拦不住管理者——但这一下必须留在留痕里。"""
    act, g, s1, s2 = assigned_group
    u = db.query(User).filter(User.student_id == take_student()).one()
    enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                     user_id=u.id, status="confirmed", position_id=s1.id,
                     allow_reassign=False, joined_at=now())
    db.add(enr)
    db.commit()

    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": enr.id,
                                    "position_id": s2.id}],
                          "source": "manual"})
    assert r.status_code == 200
    body = r.json()
    assert body["unwilling_moved"] == 1
    assert u.name in body["summary"] and "未接受调剂" in body["summary"]
    assert body["rows"][0]["allow_reassign"] is False

    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    row = next(x for x in roster["confirmed"] if x["user"]["id"] == u.id)
    assert row["allow_reassign"] is False


def test_walkin_needs_force_when_group_is_full(client, admin, db,
                                               assigned_group, take_student):
    act, g, s1, _s2 = assigned_group
    for _ in range(g.capacity):
        u = db.query(User).filter(User.student_id == take_student()).one()
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, status="confirmed", joined_at=now()))
    db.commit()

    extra = take_student()
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": extra})
    assert r.status_code == 409 and "名额已满" in r.json()["detail"]

    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": extra, "force": True})
    assert r.status_code == 200 and r.json()["forced_reasons"]


def test_unchecked_out_hours_need_review(client, admin, db, take_student):
    """扫码即走的人不能和全程服务的人一样自动拿满时长。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"时长测试 {uid('t')}", type="A",
                   intro="x", location="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   qa_group_qr="x", status="finished",
                   reg_start=now() - timedelta(days=9),
                   reg_end=now() - timedelta(days=8),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="已结束场次",
                      start_time=now() - timedelta(days=2),
                      end_time=now() - timedelta(days=2) + timedelta(hours=4),
                      capacity=5, mode="single", sort=0)
    db.add(g)
    db.flush()
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       mode="group", token=uid("T")[:6].upper(),
                       valid_from=g.start_time, valid_to=g.end_time,
                       created_by=admin_user.id)
    db.add(task)
    db.flush()

    quitter = db.query(User).filter(User.student_id == take_student()).one()
    stayer = db.query(User).filter(User.student_id == take_student()).one()
    for u, checkout in ((quitter, None), (stayer, g.end_time)):
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, status="confirmed", joined_at=now()))
        db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                             shift_id=g.id, user_id=u.id, method="scan",
                             checkin_at=g.start_time, checkout_at=checkout))
    db.commit()

    items = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()["items"]
    flags = {i["user"]["id"]: i for i in items}
    assert flags[quitter.id]["auto_checkout"] is True
    assert flags[stayer.id]["auto_checkout"] is False

    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    json={}, headers=admin)
    assert r.status_code == 200
    assert r.json()["count"] == 1                 # 只确认了正常签退的那个
    assert "待核" in r.json()["message"]

    after = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()["items"]
    got = {i["user"]["id"]: i["status"] for i in after}
    assert got[stayer.id] == "confirmed" and got[quitter.id] is None


def test_qingxie_sees_users_page_teacher_does_not(client, teacher):
    qingxie = login(client, QINGXIE)
    assert client.get("/api/staff/users", headers=qingxie).status_code == 200
    assert client.get("/api/staff/users", headers=teacher).status_code == 403
    assert client.get("/api/staff/overview", headers=teacher).status_code == 403


# ======================= 复制 =======================

def test_copy_activity_shifts_times_and_drops_enrollments(
        client, admin, db, assigned_group, take_student):
    """复制只带结构：场次、岗位跟着走，报名不跟着走，时间整体平移。"""
    act, g, p1, _p2 = assigned_group
    u = db.query(User).filter(User.student_id == take_student()).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, status="confirmed", position_id=p1.id,
                      joined_at=now()))
    db.commit()

    r = client.post(f"/api/staff/activities/{act.id}/copy", headers=admin,
                    json={"shift_days": 7, "title": "复制出来的活动"})
    assert r.status_code == 200
    new_id = r.json()["id"]

    detail = client.get(f"/api/activities/{new_id}", headers=admin).json()
    assert detail["status"] == "draft"
    assert len(detail["shifts"]) == 1
    new_shift = detail["shifts"][0]
    assert len(new_shift["positions"]) == len(g.positions)
    assert new_shift["confirmed"] == 0          # 报名没有跟过来

    # 比数据库里的值：接口输出按分钟截断，差值会变成 6 天 23:59:xx
    old_start = db.get(Shift, g.id).start_time
    new_start = db.get(Shift, new_shift["id"]).start_time
    assert new_start - old_start == timedelta(days=7)


def test_copy_positions_skips_duplicate_names(client, admin, db,
                                              assigned_group):
    act, g, _p1, _p2 = assigned_group
    other = Shift(id=uid("s"), activity_id=act.id, name="第二场",
                  start_time=g.start_time + timedelta(days=1),
                  end_time=g.end_time + timedelta(days=1),
                  mode="pick", sort=1)
    db.add(other)
    db.commit()

    body = {"target_shift_ids": [other.id]}
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/shifts/{g.id}/positions/copy",
                        headers=admin, json=body)
        assert r.status_code == 200 and r.json()["copied"] == 2

        # 再来一次：同名的都该被跳过，不会复制出两套一样的岗位
        r = client.post(f"/api/staff/shifts/{g.id}/positions/copy",
                        headers=admin, json=body)
        assert r.json()["copied"] == 0 and len(r.json()["skipped"]) == 2


def test_答疑群二维码非必填(client, admin):
    """群二维码是选填：不是每个活动都建群。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": "无群活动", "type": "A", "intro": "x",
                          "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{r.json()['id']}", headers=admin).json()
    assert d["qa_group_qr"] == ""


def test_没有岗位的场次不能发布(client, admin, db):
    """pick 模式没岗位就是没名额，发出去学生一个都报不了。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": "空场次活动", "type": "A", "intro": "x",
                          "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=1)).isoformat()})
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=5)).isoformat(),
                          "end_time": (now() + timedelta(days=5, hours=3)).isoformat()})
    shift_id = r.json()["id"]

    r = client.post(f"/api/staff/activities/{aid}/publish", headers=admin)
    assert r.status_code == 400 and "还没有岗位" in r.json()["detail"]

    client.post(f"/api/staff/shifts/{shift_id}/positions", headers=admin,
                json={"name": "引导", "quota": 3, **POSITION_TEXT})
    assert client.post(f"/api/staff/activities/{aid}/publish",
                       headers=admin).status_code == 200
