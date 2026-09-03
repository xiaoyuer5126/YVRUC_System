"""培训/彩排（附属场次）：连带报名、跟着岗位走、时长认定方式。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Enrollment, Position, Shift, User, now, uid
from conftest import ADMIN, POSITION_TEXT, editing, login, student_row


@pytest.fixture
def act_with_positions(db, take_student):
    """一个已发布的 pick 活动：一个服务场次，两个岗位。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    from backend.models import ActivityManager

    act = Activity(id=uid("act"), title=f"彩排测试 {uid('t')}",
                   intro="x", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=2), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="演出当日",
              start_time=now() + timedelta(days=10),
              end_time=now() + timedelta(days=10, hours=4),
              mode="pick", sort=0)
    db.add(g)
    db.flush()
    east = Position(id=uid("p"), shift_id=g.id, name="东门引导", quota=3,
                    sort=0, **POSITION_TEXT)
    check = Position(id=uid("p"), shift_id=g.id, name="检录台", quota=3,
                     sort=1, **POSITION_TEXT)
    db.add_all([east, check])
    db.commit()
    return act, g, east, check


def _add_attached(client, admin, act, parent, *, kind="rehearsal",
                  positions=(), hours_mode="fixed", days=5):
    # 起止用同一个基准算，差几微秒会让「固定计时」的 2 小时进位成 2.5
    start = now().replace(microsecond=0) + timedelta(days=days)
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                        json={"name": "赛前彩排", "kind": kind,
                              "parent_id": parent.id,
                              "applies_to_positions": list(positions),
                              "hours_mode": hours_mode,
                              "location": "排练厅",
                              "start_time": start.isoformat(),
                              "end_time": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _checkin(db, act, shift_id, u):
    """给某人在某场次记一条签到（现场扫码的等价物）。"""
    from backend.models import CheckinRecord, CheckinTask

    g = db.get(Shift, shift_id)
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=shift_id,
                       mode="shift", token=uid("T")[:6].upper(),
                       created_by=admin.id,
                       valid_from=g.start_time, valid_to=g.end_time)
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=shift_id, user_id=u.id, method="manual"))
    db.commit()


def _enroll(client, sid, act, g, position):
    h = login(client, sid)
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=h, json={"position_id": position.id})
    assert r.status_code == 200, r.text
    return h


def test_报名主场次会连带报上彩排(client, admin, db, act_with_positions,
                                  take_student):
    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g)
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)

    assert db.query(Enrollment).filter(
        Enrollment.shift_id == sid, Enrollment.user_id == u.id,
        Enrollment.status == "enrolled").count() == 1


def test_彩排只对指定岗位生效(client, admin, db, act_with_positions,
                              take_student):
    """东门组周五彩排，检录组不该被叫来。"""
    act, g, east, check = act_with_positions
    sid = _add_attached(client, admin, act, g, positions=[east.id])

    a = student_row(db, take_student())
    b = student_row(db, take_student())
    _enroll(client, a.student_id, act, g, east)
    _enroll(client, b.student_id, act, g, check)

    joined = {r.user_id for r in db.query(Enrollment)
              .filter(Enrollment.shift_id == sid).all()}
    assert a.id in joined and b.id not in joined


def test_改岗位时彩排跟着走(client, admin, db, act_with_positions, take_student):
    act, g, east, check = act_with_positions
    sid = _add_attached(client, admin, act, g, positions=[east.id])
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                      Enrollment.user_id == u.id).one()

    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": enr.id,
                                    "position_id": check.id}],
                          "source": "manual"})
    assert r.status_code == 200, r.text
    assert db.query(Enrollment).filter(Enrollment.shift_id == sid,
                                       Enrollment.user_id == u.id).count() == 0


def test_退选主场次会连带退掉彩排(client, admin, db, act_with_positions,
                                  take_student):
    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g)
    u = student_row(db, take_student())
    h = _enroll(client, u.student_id, act, g, east)
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                      Enrollment.user_id == u.id).one()

    r = client.delete(f"/api/activities/enrollments/{enr.id}", headers=h)
    assert r.status_code == 200, r.text
    assert db.query(Enrollment).filter(Enrollment.shift_id == sid,
                                       Enrollment.user_id == u.id).count() == 0


def test_后加的彩排会补上已报名的人(client, admin, db, act_with_positions,
                                    take_student):
    """先有报名后加彩排是常事，不补的话这批人看不到也签不了到。"""
    act, g, east, _check = act_with_positions
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    sid = _add_attached(client, admin, act, g)

    assert db.query(Enrollment).filter(Enrollment.shift_id == sid,
                                       Enrollment.user_id == u.id).count() == 1


def test_彩排时间冲突会拦住报名(client, admin, db, act_with_positions,
                                take_student):
    """彩排那晚已经有别的活动，报完才发现等于把冲突留给现场。"""
    from backend.models import ActivityManager

    act, g, east, _check = act_with_positions
    _add_attached(client, admin, act, g, days=5)

    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    other = Activity(id=uid("act"), title=f"撞车活动 {uid('t')}",
                     intro="x", location="x",
                     contacts=[{"name": "测试", "phone": "13900000000"}],
                     status="published", reg_start=now() - timedelta(days=1),
                     reg_end=now() + timedelta(days=2), created_by=admin_user.id)
    db.add(other)
    db.add(ActivityManager(id=uid("mgr"), activity_id=other.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    og = Shift(id=uid("g"), activity_id=other.id, name="同晚场次",
               start_time=now() + timedelta(days=5),
               end_time=now() + timedelta(days=5, hours=2), mode="pick", sort=0)
    db.add(og)
    db.flush()
    op = Position(id=uid("p"), shift_id=og.id, name="别处", quota=3, sort=0,
                  **POSITION_TEXT)
    db.add(op)
    db.commit()

    u = student_row(db, take_student())
    h = login(client, u.student_id)
    assert client.post(f"/api/activities/{other.id}/shifts/{og.id}/enroll",
                       headers=h, json={"position_id": op.id}).status_code == 200

    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=h, json={"position_id": east.id})
    assert r.status_code == 409
    assert "彩排" in r.json()["detail"]["message"]


def test_固定计时不要求签退(client, admin, db, act_with_positions, take_student):
    """两小时的彩排人就在那儿，不值得卡分钟——也就没有「忘记签退」。"""
    from backend.models import CheckinRecord, CheckinTask

    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g, hours_mode="fixed")
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)

    _checkin(db, act, sid, u)

    row = next(x for x in client.get(f"/api/staff/activities/{act.id}/hours",
                                     headers=admin).json()["items"]
               if x["user"]["student_id"] == u.student_id)
    assert row["hours"] == 2.0
    assert not row["pending"] and not row["auto_checkout"]


def test_不计时长的场合不产生时长(client, admin, db, act_with_positions,
                                  take_student):
    from backend.models import CheckinRecord, CheckinTask

    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g, kind="training", hours_mode="none")
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    _checkin(db, act, sid, u)

    items = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()["items"]
    assert u.student_id not in [x["user"]["student_id"] for x in items]


def test_附属场次不占活动的名额与时间跨度(client, admin, db, act_with_positions):
    """彩排常排在正式服务前一周，算进去广场上的时间会从彩排那天开始。"""
    act, g, _e, _c = act_with_positions
    _add_attached(client, admin, act, g, days=5)

    brief = next(a for a in client.get("/api/staff/activities",
                                       headers=admin).json() if a["id"] == act.id)
    assert brief["shifts_count"] == 1
    assert brief["total_capacity"] == 6          # 两个岗位各 3
    assert f"{(now() + timedelta(days=10)):%m-%d}" in brief["time_text"]

    detail = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert len(detail["shifts"]) == 1
    assert [s["kind"] for s in detail["shifts"][0]["attached"]] == ["rehearsal"]


def test_培训必须挂在服务场次下(client, admin, db, act_with_positions):
    act, g, _e, _c = act_with_positions
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                        json={"name": "野生培训", "kind": "training",
                              "start_time": (now() + timedelta(days=3)).isoformat(),
                              "end_time": (now() + timedelta(days=3, hours=1)
                                           ).isoformat()})
        assert r.status_code == 400 and "必须挂在" in r.json()["detail"]


def test_招募文案带上岗位与彩排(client, admin, db, act_with_positions):
    act, g, east, _c = act_with_positions
    _add_attached(client, admin, act, g, positions=[east.id])
    client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                 json={"volunteer_requirement": "需全程站立",
                       "volunteer_benefit": "提供午餐与纪念徽章"})

    text = client.get(f"/api/staff/activities/{act.id}/poster",
                      headers=admin).json()["text"]
    for want in ("志愿者招募", "东门引导", "检录台", "需全程站立",
                 "提供午餐与纪念徽章", "彩排", "排练厅", "东门引导需参加"):
        assert want in text, f"招募文案里少了「{want}」\n{text}"
    # 招募文案对外，不能带名单
    assert "学号" not in text


def test_培训彩排可以请假(client, admin, db, act_with_positions, take_student):
    """请假不是退选：人还在名单上，只是这一场不来。"""
    act, g, east, _c = act_with_positions
    sid = _add_attached(client, admin, act, g, kind="training")
    u = student_row(db, take_student())
    h = _enroll(client, u.student_id, act, g, east)

    detail = client.get(f"/api/activities/{act.id}", headers=h).json()
    att = detail["shifts"][0]["attached"][0]
    assert att["mine"] and not att["excused"]

    r = client.post(f"/api/activities/enrollments/{att['my_enrollment_id']}/excuse",
                    headers=h, json={"excused": True, "reason": "与专业课冲突"})
    assert r.status_code == 200, r.text

    row = db.query(Enrollment).filter(Enrollment.shift_id == sid,
                                      Enrollment.user_id == u.id).one()
    assert row.excused and row.status == "enrolled"   # 还在名单上

    # 请假的人不进应到岗的分母，也不再被催签到
    board = client.get(f"/api/staff/shifts/{sid}/board", headers=admin).json()
    assert board["expected"] == 0 and board["excused"] == 1
    r = client.post(f"/api/staff/shifts/{sid}/checkin-reminder", headers=admin,
                    json={})
    assert r.status_code == 400 and "没有需要提醒的人" in r.json()["detail"]

    # 销假回到正常
    client.post(f"/api/activities/enrollments/{att['my_enrollment_id']}/excuse",
                headers=h, json={"excused": False})
    board = client.get(f"/api/staff/shifts/{sid}/board", headers=admin).json()
    assert board["expected"] == 1 and board["excused"] == 0


def test_服务场次不能请假(client, admin, db, act_with_positions, take_student):
    """服务场次不来就是退选，那条路有信用分规则，不能靠请假绕过。"""
    act, g, east, _c = act_with_positions
    u = student_row(db, take_student())
    h = _enroll(client, u.student_id, act, g, east)
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                      Enrollment.user_id == u.id).one()

    r = client.post(f"/api/activities/enrollments/{enr.id}/excuse", headers=h,
                    json={"excused": True, "reason": "有事"})
    assert r.status_code == 400 and "请走退选" in r.json()["detail"]


def test_开始后不能再改请假(client, admin, db, act_with_positions, take_student):
    act, g, east, _c = act_with_positions
    sid = _add_attached(client, admin, act, g, days=5)
    u = student_row(db, take_student())
    h = _enroll(client, u.student_id, act, g, east)
    enr = db.query(Enrollment).filter(Enrollment.shift_id == sid,
                                      Enrollment.user_id == u.id).one()

    s = db.get(Shift, sid)
    s.start_time = now() - timedelta(minutes=5)
    db.commit()

    r = client.post(f"/api/activities/enrollments/{enr.id}/excuse", headers=h,
                    json={"excused": True, "reason": "临时有事"})
    assert r.status_code == 409 and "已经开始" in r.json()["detail"]
