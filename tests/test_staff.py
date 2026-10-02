"""管理端：草稿可见性、活动级权限、分配留痕、补录闸门、时长待核。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, CheckinRecord,
                            CheckinTask, Enrollment, FormQuestion, Shift,
                            Position, User, new_checkin_token, now, uid)
from conftest import (ADMIN, POSITION_TEXT, QINGXIE, approve, editing,
                      upload_proposal,
                      login, student_row)


@pytest.fixture
def draft_activity(db):
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"尚未发布的草稿活动 {uid('t')}",
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

    # 按标题搜：只翻第一页的话，草稿排在第二页也会被当成「看不到」
    for status in ("published", ""):
        titles = [a["title"] for a in
                  client.get("/api/activities", headers=h,
                             params={"status": status,
                                     "q": draft_activity.title}).json()["items"]]
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
    """一个截止后统一分配的场次，两个岗位，两个已确认的人。"""
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
                       mode="group", token=new_checkin_token(),
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
                         headers=login(client, who.student_id)
                         ).json()["effect"] == "held"

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
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert db.query(ActivityManager).filter(
        ActivityManager.activity_id == aid).count() == 1

    # 场次、岗位、问卷、被驳回过的审批流水都在，一并要能删掉。
    # 每一步都得真的建成——哪步悄悄失败了，删除时就少验一种引用
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=6)).isoformat(),
                          "end_time": (now() + timedelta(days=6, hours=3)).isoformat()})
    assert r.status_code == 200, r.text
    g = r.json()["id"]
    r = client.post(f"/api/staff/shifts/{g}/positions", headers=admin,
                    json={"name": "引导", "quota": 2, **POSITION_TEXT})
    assert r.status_code == 200, r.text
    r = client.put(f"/api/staff/activities/{aid}/form", headers=admin,
                   json={"questions": [{"id": "", "kind": "text", "title": "为什么报名",
                                        "options": [], "required": True,
                                        "show_if": {}}]})
    assert r.status_code == 200, r.text
    form = client.get(f"/api/staff/activities/{aid}/form", headers=admin).json()
    assert len(form["questions"]) == 1
    dept = next(d for d in client.get("/api/staff/departments",
                                      headers=admin).json() if d["has_head"])
    upload_proposal(client, admin, aid)
    assert client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                       json={"department_id": dept["id"]}).status_code == 200
    assert client.post(f"/api/staff/activities/{aid}/decide", headers=admin,
                       json={"approve": False, "comment": "先撤下"}).status_code == 200
    assert db.query(ApprovalEvent).filter(
        ApprovalEvent.activity_id == aid).count() == 2

    assert client.delete(f"/api/staff/activities/{aid}",
                         headers=admin).status_code == 200
    assert client.get(f"/api/activities/{aid}", headers=admin).status_code == 404
    # 活动没了，挂在它身上的记录已无接口可查，只能直接看库
    db.expire_all()
    assert db.query(ActivityManager).filter(
        ActivityManager.activity_id == aid).count() == 0
    assert db.query(ApprovalEvent).filter(
        ApprovalEvent.activity_id == aid).count() == 0
    assert db.query(FormQuestion).filter(
        FormQuestion.activity_id == aid).count() == 0


def test_管理者分工只筛通知不限操作(client, admin, db, open_group, take_student):
    """分了工的人只收自己那摊的通知；没分工的照收全部，操作权限一点不变。

    用真实的「临近减员」提醒来验（它属于「志愿者管理」那一摊）：5 小时后
    开始的场次里有人退选，就会通知管理者。
    """
    act, g = open_group(2, day_offset=0, start_in_hours=5)
    other = student_row(db, take_student())
    other.roles = ["student", "initiator"]
    db.commit()
    r = client.post(f"/api/staff/activities/{act.id}/managers", headers=admin,
                    json={"student_id": other.student_id})
    assert r.status_code == 200, r.text

    d = client.get(f"/api/staff/activities/{act.id}/managers", headers=admin).json()
    assert "hours" in d["duties"] and "roster" in d["duties"]
    assert all(m["duties"] == [] for m in d["items"])

    url = f"/api/staff/activities/{act.id}/managers/{other.id}/duties"
    r = client.patch(url, headers=admin, json={"duties": ["hours"]})
    assert r.status_code == 200 and r.json()["duties"] == ["hours"]
    assert client.patch(url, headers=admin,
                        json={"duties": ["nope"]}).status_code == 400

    them = login(client, other.student_id)

    def dropouts(headers):
        items = client.get("/api/me/notifications", headers=headers).json()["items"]
        return [n for n in items
                if n["title"] == f"[临近减员] {act.title}"]

    def late_dropout():
        h = login(client, take_student())
        enr_id = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                             headers=h, json={"position_id": g.test_position_id}
                             ).json()["enrollment_id"]
        assert client.delete(f"/api/activities/enrollments/{enr_id}",
                             headers=h).status_code == 200

    # 只管时长的人收不到减员提醒；没分工的管理者照收
    late_dropout()
    assert dropouts(them) == []
    assert len(dropouts(admin)) == 1
    # 把这一摊也分给他，就收得到了
    assert client.patch(url, headers=admin,
                        json={"duties": ["hours", "roster"]}).status_code == 200
    late_dropout()
    assert len(dropouts(them)) == 1
    assert len(dropouts(admin)) == 2

    # 分工不影响能不能干活：他照样能进这场活动的名单
    assert client.get(f"/api/staff/shifts/{g.id}/roster",
                      headers=them).status_code == 200


def test_活动结束后名单与签到码只读(client, admin, db, assigned_group, take_student):
    """时长按结束那一刻的岗位与名单结账，事后再动就跟账对不上。"""
    act, g, s1, s2 = assigned_group
    u = db.query(User).filter(User.student_id == take_student()).one()
    enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                     user_id=u.id, status="enrolled", position_id=s1.id,
                     joined_at=now())
    db.add(enr)
    db.get(Activity, act.id).status = "finished"
    db.commit()

    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": enr.id, "position_id": s2.id}],
                          "source": "manual"})
    assert r.status_code == 409 and "已结束" in r.json()["detail"]
    for r in (client.post(f"/api/staff/enrollments/{enr.id}/leader", headers=admin,
                          json={"is_leader": True}),
              client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                          json={"mode": "shift"})):
        assert r.status_code == 409 and "已结束" in r.json()["detail"]
    # 账还没结完：补招仍然要能用（现场漏登记的人事后才补进来）
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": take_student(), "position_id": s2.id})
    assert r.status_code == 200, r.text


def test_时长封账后补招与补签也停(client, admin, db, assigned_group, take_student):
    """活动结束后补招、补签本来还开着（见上一条），时长一送审就都得停：
    部长看的必须和最终封的是同一份账。"""
    from backend.models import HoursRequest

    act, g, _s1, s2 = assigned_group
    # 码要在活动结束前建好：结束之后建码本身就被拦了
    tid = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                      json={"mode": "shift"}).json()["id"]
    sid = take_student()
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": sid, "position_id": s2.id})
    assert r.status_code == 200, r.text

    row = db.get(Activity, act.id)
    row.status = "finished"
    db.add(HoursRequest(id=uid("hrq"), activity_id=act.id, kind="lock",
                        reason="送审", requested_by=""))
    db.commit()
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": take_student(), "position_id": s2.id})
    assert r.status_code == 409 and "审批" in r.json()["detail"]
    r = client.post(f"/api/staff/checkin-tasks/{tid}/batch", headers=admin,
                    json={"action": "checkin", "student_ids": [sid]})
    assert r.status_code == 409 and "审批" in r.json()["detail"]


def test_活动不许重名(client, admin, db, draft_activity):
    """重名的活动在广场、名单、导出文件名里全都分不清是哪一场。"""
    body = {"contact_group_qr": "x", "title": draft_activity.title,
            "organizer": "校青协", "intro": "x", "location": "世纪馆",
            "contacts": [{"name": "王", "phone": "13900000000"}],
            "reg_start": now().isoformat(),
            "reg_end": (now() + timedelta(days=2)).isoformat()}
    r = client.post("/api/staff/activities", headers=admin, json=body)
    assert r.status_code == 409 and "同名" in r.json()["detail"]

    body["title"] = f"另起一个 {uid('t')}"
    other = client.post("/api/staff/activities", headers=admin, json=body).json()["id"]
    # 改名撞上也拦；改成自己原来的名字不算撞
    r = client.patch(f"/api/staff/activities/{other}", headers=admin,
                     json={"title": draft_activity.title})
    assert r.status_code == 409
    assert client.patch(f"/api/staff/activities/{other}", headers=admin,
                        json={"title": body["title"]}).status_code == 200

    # 复制不打断操作：默认名撞了就往后加序号
    r = client.post(f"/api/staff/activities/{other}/copy", headers=admin,
                    json={"shift_days": 1, "contact_group_qr": "x"})
    assert r.status_code == 200, r.text
    first_copy = r.json()["title"]
    r = client.post(f"/api/staff/activities/{other}/copy", headers=admin,
                    json={"shift_days": 1, "contact_group_qr": "x"})
    assert r.json()["title"] == f"{first_copy} 2"


def test_补招要通知本人(client, admin, db, assigned_group, take_student):
    """补招是管理者单方面加进来的：不通知的话本人一点动静都没有——
    不知道自己报了名，更不知道哪天要去。"""
    from backend.models import Notification, User

    act, g, _s1, s2 = assigned_group
    sid = take_student()
    r = client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                    json={"student_id": sid, "position_id": s2.id})
    assert r.status_code == 200, r.text

    u = db.query(User).filter(User.student_id == sid).one()
    n = (db.query(Notification)
         .filter(Notification.user_id == u.id,
                 Notification.title.like("您已被补招%")).first())
    assert n is not None and n.data.get("activity_id") == act.id
    assert g.name in n.content


# ======================= 志愿者保障与必填项 =======================

def _new_activity(client, headers, **over):
    from datetime import timedelta
    from backend.models import now, uid
    body = {"contact_group_qr": "x", "title": f"保障测试 {uid('t')}", "organizer": "青协",
            "intro": "x", "location": "世纪馆",
            "contacts": [{"name": "王", "phone": "13900000000"}],
            "reg_start": (now() - timedelta(days=1)).isoformat(),
            "reg_end": (now() + timedelta(days=5)).isoformat(), **over}
    return client.post("/api/staff/activities", headers=headers, json=body)


def test_志愿者保障必填_没填就是默认那句(client, admin, db):
    from backend import config
    default = "根据实际服务情况认定志愿时长（根据实际情况填写，如午餐与饮水、纪念证书、意外险等）"
    # 新建表单预填的那句来自后端
    assert client.get("/api/auth/constants", headers=admin).json()[
        "volunteer_benefit_default"] == default == config.DEFAULT_VOLUNTEER_BENEFIT
    r = _new_activity(client, admin)
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert client.get(f"/api/activities/{aid}", headers=admin).json()[
        "volunteer_benefit"] == default
    r = _new_activity(client, admin, volunteer_benefit="  ")
    assert r.status_code == 400 and "志愿者保障" in r.json()["detail"]
    # 修改时也不能改成空的
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"volunteer_benefit": ""})
    assert r.status_code == 400 and r.json()["detail"] == "志愿者保障不能为空"
    # 补记录活动表单上没有这一项，空着就用默认那句
    from backend.models import now
    from datetime import timedelta
    r = _new_activity(client, admin, recruit_mode="record", volunteer_benefit="",
                      held_start=(now() - timedelta(days=3)).isoformat(),
                      held_end=(now() - timedelta(days=3, hours=-2)).isoformat())
    assert r.status_code == 200, r.text


def test_修改活动时必填项不能清空(client, admin, db):
    """新建时逐个查过的，改的时候同样不能改成空串。"""
    aid = _new_activity(client, admin).json()["id"]
    for key, label in (("title", "活动名称"), ("organizer", "组织单位"),
                       ("location", "活动地点"), ("intro", "活动简介")):
        r = client.patch(f"/api/staff/activities/{aid}", headers=admin, json={key: " "})
        assert r.status_code == 400 and r.json()["detail"] == f"{label}不能为空", key


# ======================= 活动地点定位 =======================

def test_活动地点定位选填_详情里带着(client, admin, db):
    from datetime import timedelta
    from backend.models import now
    r = _new_activity(client, admin, location_point={"lat": 39.9690012345, "lng": 116.30741})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    assert client.get(f"/api/activities/{aid}", headers=admin).json()[
        "location_point"] == {"lat": 39.969001, "lng": 116.30741}

    # 改点位进变更记录，写成坐标而不是一个字典
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"location_point": {"lat": 39.97, "lng": 116.31}})
    assert r.status_code == 200, r.text
    change = next(c for c in r.json()["changes"] if c["field"] == "location_point")
    assert change["label"] == "地点定位"
    assert change["old"] == "39.96900, 116.30741" and change["new"] == "39.97000, 116.31000"
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"location_point": None})
    assert next(c for c in r.json()["changes"]
                if c["field"] == "location_point")["new"] == "未定位"

    # 不选就是没有；坐标不对的不收
    assert client.get(f"/api/activities/{_new_activity(client, admin).json()['id']}",
                      headers=admin).json()["location_point"] is None
    for bad in ({"lat": 91, "lng": 0}, {"lat": "x", "lng": 1}, {"lng": 116}):
        r = _new_activity(client, admin, location_point=bad)
        assert r.status_code == 400 and "地点定位" in r.json()["detail"], bad

    # 补记录活动没有人要导航，传了也不收
    r = _new_activity(client, admin, recruit_mode="record",
                      location_point={"lat": 39.97, "lng": 116.31},
                      held_start=(now() - timedelta(days=3)).isoformat(),
                      held_end=(now() - timedelta(days=3, hours=-2)).isoformat())
    assert r.status_code == 200, r.text
    assert client.get(f"/api/activities/{r.json()['id']}", headers=admin).json()[
        "location_point"] is None
