"""管理端：草稿可见性、活动级权限、分配留痕、补录闸门、时长待核。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, FormQuestion, Shift,
                            Position, User, now, uid)
from conftest import (ADMIN, POSITION_TEXT, QINGXIE, approve, editing,
                      login, student_row)


@pytest.fixture
def draft_activity(db):
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title="尚未发布的草稿活动",
                   intro="筹备中", location="待定",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="draft",
                   reg_start=now(), reg_end=now() + timedelta(days=3),
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    db.add(Shift(id=uid("g"), activity_id=act.id, name="草稿场次",
                         start_time=now() + timedelta(days=40),
                         end_time=now() + timedelta(days=40, hours=3),
                          mode="assign", sort=0))
    db.commit()
    return act


def test_draft_hidden_from_students(client, draft_activity, take_student):
    """筹备中的活动没定稿，学生不该在广场上看到、也不该能直接翻到详情。"""
    h = login(client, take_student())

    for status in ("published", ""):
        titles = [a["title"] for a in
                  client.get(f"/api/activities?status={status}",
                             headers=h).json()["items"]]
        assert draft_activity.title not in titles

    assert client.get(f"/api/activities/{draft_activity.id}",
                      headers=h).status_code == 404


def test_draft_visible_to_its_manager(client, admin, draft_activity):
    assert client.get(f"/api/activities/{draft_activity.id}",
                      headers=admin).status_code == 200


def test_non_manager_cannot_touch_others_activity(client, initiator,
                                                  draft_activity):
    """老师只能管自己参与共管的活动。"""
    r = client.patch(f"/api/staff/activities/{draft_activity.id}",
                     json={"location": "被别人改了"}, headers=initiator)
    assert r.status_code == 403


@pytest.fixture
def assigned_group(db):
    """一个二级待定的场次，两个岗位，两个已确认的人。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"分配测试 {uid('t')}",
                   intro="x", location="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="closed",
                   reg_start=now() - timedelta(days=2), reg_end=now(),
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="待分配场次",
                      start_time=now() + timedelta(days=50),
                      end_time=now() + timedelta(days=50, hours=4),
                          mode="assign", sort=0)
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
                     user_id=u.id, status="enrolled", position_id=s1.id,
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
                     user_id=u.id, status="enrolled", position_id=s1.id,
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
    row = next(x for x in roster["enrolled"] if x["user"]["id"] == u.id)
    assert row["allow_reassign"] is False


def test_walkin_needs_force_when_group_is_full(client, admin, db,
                                               assigned_group, take_student):
    act, g, s1, _s2 = assigned_group
    for _ in range(g.total_capacity()):
        u = db.query(User).filter(User.student_id == take_student()).one()
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=u.id, status="enrolled", joined_at=now()))
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
    act = Activity(id=uid("act"), title=f"时长测试 {uid('t')}",
                   intro="x", location="x",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="finished",
                   reg_start=now() - timedelta(days=9),
                   reg_end=now() - timedelta(days=8),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="已结束场次",
                      start_time=now() - timedelta(days=2),
                      end_time=now() - timedelta(days=2) + timedelta(hours=4),
                          mode="assign", sort=0)
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
                          user_id=u.id, status="enrolled", joined_at=now()))
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
    got = {i["user"]["id"]: i["confirmed"] for i in after}
    assert got[stayer.id] is True and got[quitter.id] is False


def test_qingxie_sees_users_page_initiator_does_not(client, initiator):
    qingxie = login(client, QINGXIE)
    assert client.get("/api/staff/users", headers=qingxie).status_code == 200
    assert client.get("/api/staff/users", headers=initiator).status_code == 403
    assert client.get("/api/staff/overview", headers=initiator).status_code == 403


# ======================= 复制 =======================

def test_copy_activity_shifts_times_and_drops_enrollments(
        client, admin, db, assigned_group, take_student):
    """复制只带结构：场次、岗位跟着走，报名不跟着走，时间整体平移。"""
    act, g, p1, _p2 = assigned_group
    u = db.query(User).filter(User.student_id == take_student()).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, status="enrolled", position_id=p1.id,
                      joined_at=now()))
    db.commit()

    # 新活动新群：不传二维码不给复制
    r = client.post(f"/api/staff/activities/{act.id}/copy", headers=admin,
                    json={"shift_days": 7, "title": "复制出来的活动"})
    assert r.status_code == 400 and "联络群" in r.json()["detail"]
    r = client.post(f"/api/staff/activities/{act.id}/copy", headers=admin,
                    json={"shift_days": 7, "title": "复制出来的活动",
                          "contact_group_qr": "data:new"})
    assert r.status_code == 200
    new_id = r.json()["id"]

    detail = client.get(f"/api/activities/{new_id}", headers=admin).json()
    assert detail["status"] == "draft"
    assert len(detail["shifts"]) == 1
    new_shift = detail["shifts"][0]
    assert len(new_shift["positions"]) == len(g.positions)
    assert new_shift["enrolled"] == 0          # 报名没有跟过来

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


def test_统一分配的场次必须先切模式才能加岗位(client, admin, db, assigned_group):
    """两种场次的分界线：assign 报名时只锁时段，岗位是截止后才建的。

    前端在点「添加岗位」当下就提示并给一键切换，后端这道闸门是兜底。
    """
    act, g, _p1, _p2 = assigned_group
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "岗位丙", "quota": 1, **POSITION_TEXT})
        assert r.status_code == 409
        assert "报名即选岗位" in r.json()["detail"]

        client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                     json={"mode": "pick"})
        r = client.post(f"/api/staff/shifts/{g.id}/positions", headers=admin,
                        json={"name": "岗位丙", "quota": 1, **POSITION_TEXT})
        assert r.status_code == 200, r.text
    assert len(db.get(Shift, g.id).positions) == 3


def test_有人报名就不能改回统一分配(client, admin, db, assigned_group,
                                    take_student):
    """反向切模式要作废所有人已经选定的岗位，是一次实打实的减损。

    与其做一个带二次确认的破坏性操作，不如干脆不给：建错模式的场次
    删了重建就是。
    """
    act, g, p1, _p2 = assigned_group
    u = db.query(User).filter(User.student_id == take_student()).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=u.id, status="enrolled", position_id=p1.id,
                      joined_at=now()))
    g.mode = "pick"
    db.commit()

    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                         json={"mode": "assign"})
        assert r.status_code == 409
        assert "已有 1 人报名" in r.json()["detail"]

    db.expire_all()
    assert db.get(Shift, g.id).mode == "pick"
    enr = db.query(Enrollment).filter(Enrollment.user_id == u.id,
                                      Enrollment.shift_id == g.id).one()
    assert enr.position_id == p1.id, "被拦下就不该动任何人的岗位"


def test_没人报名时可以切回统一分配(client, admin, db, assigned_group):
    """建错模式是常事，一个人都还没报的时候不该逼人删了重建。"""
    act, g, _p1, _p2 = assigned_group
    g.mode = "pick"
    db.commit()
    with editing(client, admin, act.id):
        # 切成 assign 必须给人数上限：名额记在自动生成的占位岗位上
        r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin,
                         json={"mode": "assign", "capacity": 12})
        assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Shift, g.id)
    assert row.mode == "assign"
    holder = row.placeholder()
    assert holder is not None and holder.quota == 12


def test_联络群二维码必填且不公开(client, admin, take_student):
    """每场活动一个联络群：建活动就要传二维码；详情页不放，报名成功才给本人。"""
    body = {"title": "无群活动", "organizer": "校青协", "intro": "x",
            "location": "世纪馆",
            "contacts": [{"name": "王", "phone": "13900000000"}],
            "reg_start": (now() - timedelta(days=1)).isoformat(),
            "reg_end": (now() + timedelta(days=1)).isoformat()}
    r = client.post("/api/staff/activities", headers=admin, json=body)
    assert r.status_code == 400 and "联络群" in r.json()["detail"]
    r = client.post("/api/staff/activities", headers=admin,
                    json={**body, "contact_group_qr": "data:qr"})
    assert r.status_code == 200, r.text
    act_id = r.json()["id"]
    assert client.get(f"/api/activities/{act_id}",
                      headers=admin).json()["contact_group_qr"] == "data:qr"
    # 改成空也不行
    r = client.patch(f"/api/staff/activities/{act_id}", headers=admin,
                     json={"contact_group_qr": ""})
    assert r.status_code == 400


def test_没有岗位的场次不能发布(client, admin, db):
    """pick 模式没岗位就是没名额，发出去学生一个都报不了。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": "空场次活动", "organizer": "校青协", "intro": "x",
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

    # 结构不全在**提交审批**就被拦下，不必等到发布，也不该让审批人替他查
    dept = client.get("/api/staff/departments", headers=admin).json()[0]
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                    json={"department_id": dept["id"]})
    assert r.status_code == 400 and "还没有岗位" in r.json()["detail"]

    # 草稿也不能绕过审批直接发布
    r = client.post(f"/api/staff/activities/{aid}/publish", headers=admin)
    assert r.status_code == 409 and "先提交校青协审批" in r.json()["detail"]

    client.post(f"/api/staff/shifts/{shift_id}/positions", headers=admin,
                json={"name": "引导", "quota": 3, **POSITION_TEXT})
    approve(client, admin, aid)
    assert client.get(f"/api/activities/{aid}",
                      headers=admin).json()["status"] == "published"


def test_恢复报名必须重设截止时间(client, admin, db, take_student):
    """只把状态改回「报名中」而不动 reg_end，定时任务下一分钟就又截止了。"""
    from backend.models import Activity, ActivityManager

    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"恢复报名 {uid('t')}",
                   intro="x", location="x", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="closed", reg_start=now() - timedelta(days=3),
                   reg_end=now() - timedelta(hours=1), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.add(Shift(id=uid("g"), activity_id=act.id, name="将来场次",
                 start_time=now() + timedelta(days=5),
                 end_time=now() + timedelta(days=5, hours=3),
                 mode="pick", sort=0))
    db.commit()
    url = f"/api/staff/activities/{act.id}/reopen"

    # 过去的时间：等于没恢复
    r = client.post(url, headers=admin,
                    json={"reg_end": (now() - timedelta(hours=1)).isoformat()})
    assert r.status_code == 400 and "晚于当前时间" in r.json()["detail"]

    # 晚于场次开始：报名到一半人已经在现场了
    r = client.post(url, headers=admin,
                    json={"reg_end": (now() + timedelta(days=6)).isoformat()})
    assert r.status_code == 400 and "不能晚于" in r.json()["detail"]

    r = client.post(url, headers=admin,
                    json={"reg_end": (now() + timedelta(days=2)).isoformat()})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, act.id).status == "published"

    # 已经在报名中的，不能再「恢复」
    assert client.post(url, headers=admin,
                       json={"reg_end": (now() + timedelta(days=2)).isoformat()}
                       ).status_code == 409


def test_场次都开过了就不能恢复报名(client, admin, db):
    """只有一个场次，它已经开场了——恢复出来一个人也报不进来。"""
    from backend.models import Activity, ActivityManager

    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"已开始 {uid('t')}",
                   intro="x", location="x", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="closed", reg_start=now() - timedelta(days=3),
                   reg_end=now() - timedelta(days=1), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.add(Shift(id=uid("g"), activity_id=act.id, name="进行中",
                 start_time=now() - timedelta(hours=1),
                 end_time=now() + timedelta(hours=2), mode="pick", sort=0))
    db.commit()

    r = client.post(f"/api/staff/activities/{act.id}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(hours=1)).isoformat()})
    assert r.status_code == 409 and "所有场次都已开始" in r.json()["detail"]


def test_扣留的名额不挡住岗位的其它修改(client, admin, db, open_group, take_student):
    """一个人临期退选，不该把整个岗位锁死。

    扣留是一条已取消的记录占着位子，管理者本来就有「释放」按钮；
    拿它挡住改培训内容，等于名额一被占满就再也改不了。
    """
    # day_offset=0 才是「今天再过 5 小时」；不给的话场次会排到 30 天后
    act, g = open_group(2, day_offset=0, start_in_hours=5)
    pos_id = g.test_position_id
    for _ in range(2):
        h = login(client, take_student())
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=h, json={"position_id": pos_id})
        assert r.status_code == 200, r.text
    # 报上名之后才截止报名：扣留额外要求「报名已截止」
    act.reg_end = now() - timedelta(hours=1)
    db.commit()

    enr = (db.query(Enrollment)
           .filter(Enrollment.position_id == pos_id,
                   Enrollment.status == "enrolled").first())
    who = db.get(User, enr.user_id)
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=login(client, who.student_id)).status_code == 200
    db.expire_all()
    assert db.get(Enrollment, enr.id).seat_held

    # 名额 2 / 已报名 1 / 已占用 2：改培训内容不该被拦
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{pos_id}", headers=admin,
                         json={"training_note": "换个说法"})
        assert r.status_code == 200, r.text
        # 降到已报名人数以下仍然要拦
        assert client.patch(f"/api/staff/positions/{pos_id}", headers=admin,
                            json={"quota": 0}).status_code == 400


def test_删除草稿要连带清干净引用(client, admin, db):
    """草稿身上至少永远挂着一条创建者的共管记录，不清就撞外键报 500。"""
    from backend.models import ActivityManager, ApprovalEvent

    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"待删草稿 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "x",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=1)).isoformat()})
    aid = r.json()["id"]
    assert db.query(ActivityManager).filter(
        ActivityManager.activity_id == aid).count() == 1

    # 场次、岗位、被驳回过的审批流水都在，一并要能删掉
    g = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=6)).isoformat(),
                          "end_time": (now() + timedelta(days=6, hours=3)).isoformat()}
                    ).json()["id"]
    client.post(f"/api/staff/shifts/{g}/positions", headers=admin,
                json={"name": "引导", "quota": 2, **POSITION_TEXT})
    client.put(f"/api/staff/activities/{aid}/form", headers=admin,
               json={"questions": [{"id": "", "kind": "text", "title": "为什么报名",
                                    "options": [], "required": True,
                                    "show_if": {}}]})
    dept = next(d for d in client.get("/api/staff/departments",
                                      headers=admin).json() if d["has_head"])
    client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                json={"department_id": dept["id"]})
    client.post(f"/api/staff/activities/{aid}/decide", headers=admin,
                json={"approve": False, "comment": "先撤下"})
    assert db.query(ApprovalEvent).filter(
        ApprovalEvent.activity_id == aid).count() == 2

    assert client.delete(f"/api/staff/activities/{aid}",
                         headers=admin).status_code == 200
    db.expire_all()
    assert db.get(Activity, aid) is None
    assert db.query(ActivityManager).filter(
        ActivityManager.activity_id == aid).count() == 0
    assert db.query(ApprovalEvent).filter(
        ApprovalEvent.activity_id == aid).count() == 0
    assert db.query(FormQuestion).filter(
        FormQuestion.activity_id == aid).count() == 0


def test_管理者分工只筛通知不限操作(client, admin, db, enrolled_activity,
                                    take_student):
    """分了工的人只收自己那摊的通知；没分工的照收全部，操作权限一点不变。"""
    from backend.models import ActivityManager, Notification
    from backend.notify import notify_managers

    act, shift, _u = enrolled_activity
    other = student_row(db, take_student())
    other.roles = ["student", "initiator"]
    db.commit()
    r = client.post(f"/api/staff/activities/{act.id}/managers", headers=admin,
                    json={"student_id": other.student_id})
    assert r.status_code == 200, r.text

    d = client.get(f"/api/staff/activities/{act.id}/managers", headers=admin).json()
    assert "hours" in d["duties"] and "roster" in d["duties"]
    assert all(m["duties"] == [] for m in d["items"])

    r = client.patch(
        f"/api/staff/activities/{act.id}/managers/{other.id}/duties",
        headers=admin, json={"duties": ["hours"]})
    assert r.status_code == 200 and r.json()["duties"] == ["hours"]
    assert client.patch(
        f"/api/staff/activities/{act.id}/managers/{other.id}/duties",
        headers=admin, json={"duties": ["nope"]}).status_code == 400

    before = db.query(Notification).filter(Notification.user_id == other.id).count()
    notify_managers(db, act.id, "[人员变动] 测试", "只发给管志愿者管理的人",
                    duty="roster")
    db.commit()
    assert db.query(Notification).filter(
        Notification.user_id == other.id).count() == before
    notify_managers(db, act.id, "[时长申诉] 测试", "这条他要收", duty="hours")
    db.commit()
    assert db.query(Notification).filter(
        Notification.user_id == other.id).count() == before + 1

    # 分工不影响能不能干活：他照样能进这场活动的名单
    them = login(client, other.student_id)
    assert client.get(f"/api/staff/shifts/{shift.id}/roster",
                      headers=them).status_code == 200
