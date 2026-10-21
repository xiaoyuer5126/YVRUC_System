"""培训/彩排（附属场次）：连带报名、跟着岗位走、时长认定方式。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import Activity, Enrollment, Position, Shift, User, now, uid
from conftest import set_shift_time
from conftest import mk_shift
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
    g = mk_shift(id=uid("g"), activity_id=act.id, name="演出当日",
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
                              "applies_to_positions": list(positions),
                              "hours_mode": hours_mode,
                              "location": "排练厅",
                              "periods": [{"start_time": start.isoformat(),
                              "end_time": (start + timedelta(hours=2)).isoformat()}]})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _checkin(db, act, shift_id, u):
    """给某人在某场次记一条签到（现场扫码的等价物）。"""
    from backend.models import CheckinRecord, CheckinTask, new_checkin_token

    g = db.get(Shift, shift_id)
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=shift_id,
                       period_id=g.periods[0].id,
                       mode="shift", token=new_checkin_token(),
                       created_by=admin.id,
                       valid_from=g.start_time, valid_to=g.end_time)
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=shift_id, period_id=g.periods[0].id,
                         user_id=u.id, method="manual"))
    db.commit()


def _roster(client, admin, shift_id) -> dict[str, str]:
    """某场次（含彩排/培训）名单上在册的人：学号 → 报名记录 id。"""
    r = client.get(f"/api/staff/shifts/{shift_id}/roster", headers=admin)
    assert r.status_code == 200, r.text
    return {x["user"]["student_id"]: x["enrollment_id"] for x in r.json()["enrolled"]}


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

    assert u.student_id in _roster(client, admin, sid)


def test_彩排只对指定岗位生效(client, admin, db, act_with_positions,
                              take_student):
    """东门组周五彩排，检录组不该被叫来。"""
    act, g, east, check = act_with_positions
    sid = _add_attached(client, admin, act, g, positions=[east.id])

    a = student_row(db, take_student())
    b = student_row(db, take_student())
    _enroll(client, a.student_id, act, g, east)
    _enroll(client, b.student_id, act, g, check)

    joined = _roster(client, admin, sid)
    assert a.student_id in joined and b.student_id not in joined


def test_改岗位时彩排跟着走(client, admin, db, act_with_positions, take_student):
    act, g, east, check = act_with_positions
    sid = _add_attached(client, admin, act, g, positions=[east.id])
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    assert u.student_id in _roster(client, admin, sid)
    enr_id = _roster(client, admin, g.id)[u.student_id]

    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": enr_id,
                                    "position_id": check.id}],
                          "source": "manual"})
    assert r.status_code == 200, r.text
    assert u.student_id not in _roster(client, admin, sid)


def test_退选主场次会连带退掉彩排(client, admin, db, act_with_positions,
                                  take_student):
    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g)
    u = student_row(db, take_student())
    h = _enroll(client, u.student_id, act, g, east)
    assert u.student_id in _roster(client, admin, sid)
    enr_id = _roster(client, admin, g.id)[u.student_id]

    r = client.delete(f"/api/activities/enrollments/{enr_id}", headers=h)
    assert r.status_code == 200, r.text
    assert u.student_id not in _roster(client, admin, sid)


def test_后加的彩排会补上已报名的人(client, admin, db, act_with_positions,
                                    take_student):
    """先有报名后加彩排是常事，不补的话这批人看不到也签不了到。"""
    act, g, east, _check = act_with_positions
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    sid = _add_attached(client, admin, act, g)

    assert u.student_id in _roster(client, admin, sid)


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
    og = mk_shift(id=uid("g"), activity_id=other.id, name="同晚场次",
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
    act, g, east, _check = act_with_positions
    sid = _add_attached(client, admin, act, g, kind="training", hours_mode="none")
    u = student_row(db, take_student())
    _enroll(client, u.student_id, act, g, east)
    _checkin(db, act, sid, u)

    items = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()["items"]
    assert u.student_id not in [x["user"]["student_id"] for x in items]


def test_活动时间只写年月日带星期():
    """「2026-10-16（周五）~ 2026-10-17（周六）」；同一天只写一个日期；没场次写待定。"""
    from datetime import datetime

    def act_with(*spans):
        act = Activity(id=uid("act"), title="x", recruit_mode="pick")
        act.shifts = [mk_shift(id=uid("s"), name=f"第{i}场", kind="service",
                            start_time=a, end_time=b)
                      for i, (a, b) in enumerate(spans)]
        return act

    two_days = act_with((datetime(2026, 10, 16, 9), datetime(2026, 10, 16, 12)),
                        (datetime(2026, 10, 17, 14), datetime(2026, 10, 17, 17)))
    assert two_days.time_text() == "2026-10-16（周五）~ 2026-10-17（周六）"
    one_day = act_with((datetime(2026, 10, 16, 9), datetime(2026, 10, 16, 17)))
    assert one_day.time_text() == "2026-10-16（周五）"
    assert act_with().time_text() == "待定"


def test_附属场次不占活动的名额与时间跨度(client, admin, db, act_with_positions):
    """彩排常排在正式服务前一周，算进去广场上的时间会从彩排那天开始。"""
    act, g, _e, _c = act_with_positions
    _add_attached(client, admin, act, g, days=5)

    brief = next(a for a in client.get("/api/staff/activities",
                                       headers=admin).json()["items"]
                 if a["id"] == act.id)
    assert brief["shifts_count"] == 1
    assert brief["total_capacity"] == 6          # 两个岗位各 3
    assert f"{(now() + timedelta(days=10)):%m-%d}" in brief["time_text"]
    assert f"{(now() + timedelta(days=5)):%m-%d}" not in brief["time_text"]

    detail = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert len(detail["shifts"]) == 1
    assert [s["kind"] for s in detail["shifts"][0]["attached"]] == ["rehearsal"]


def _second_shift(db, act, g):
    """再加一个场次，也有「检录台」：同一个岗位的另一个时段。"""
    g2 = mk_shift(id=uid("g"), activity_id=act.id, name="第二天",
               start_time=g.start_time + timedelta(days=1),
               end_time=g.end_time + timedelta(days=1), mode="pick", sort=1)
    db.add(g2)
    db.flush()
    check2 = Position(id=uid("p"), shift_id=g2.id, name="检录台", quota=3, sort=0,
                      **POSITION_TEXT)
    db.add(check2)
    db.commit()
    return g2, check2


def test_培训挂在活动上_跨场次圈人_最后一个报名退掉才退(client, admin, db,
                                             act_with_positions, take_student):
    act, g, east, check = act_with_positions
    g2, check2 = _second_shift(db, act, g)
    tid = _add_attached(client, admin, act, g, positions=[check.id, check2.id])
    sid = take_student()
    h = login(client, sid)
    u = db.query(User).filter(User.student_id == sid).one()

    def in_training():
        db.expire_all()
        return db.query(Enrollment).filter(Enrollment.shift_id == tid,
                                           Enrollment.user_id == u.id).count()

    e1 = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll", headers=h,
                     json={"position_id": check.id}).json()["enrollment_id"]
    e2 = client.post(f"/api/activities/{act.id}/shifts/{g2.id}/enroll", headers=h,
                     json={"position_id": check2.id}).json()["enrollment_id"]
    assert in_training() == 1, "两个时段都叫同一场培训，只报一次"
    assert client.delete(f"/api/activities/enrollments/{e1}", headers=h).status_code == 200
    assert in_training() == 1, "第二天的检录台还在，培训照样要来"
    assert client.delete(f"/api/activities/enrollments/{e2}", headers=h).status_code == 200
    assert in_training() == 0
    # 东门引导没被圈到
    other = login(client, take_student())
    client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll", headers=other,
                json={"position_id": east.id})
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    [t] = d["trainings"]
    assert t["applies_text"] == "检录台" and t["enrolled"] == 0


def test_删岗位删场次_只叫它们的培训要先处理(client, admin, db, act_with_positions):
    act, g, east, check = act_with_positions
    g2, check2 = _second_shift(db, act, g)
    _add_attached(client, admin, act, g, positions=[check.id])
    both = _add_attached(client, admin, act, g, positions=[check.id, check2.id])
    with editing(client, admin, act.id):
        r = client.delete(f"/api/staff/shifts/{g.id}", headers=admin)
        assert r.status_code == 409 and "只叫要删的这些岗位" in r.json()["detail"]
        r = client.delete(f"/api/staff/positions/{check2.id}", headers=admin)
        assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Shift, both).applies_to_positions == [check.id], "删掉的岗位从范围里拿掉"


def test_强制删场次_只因这一场才去的培训跟着退(client, admin, db, act_with_positions,
                                       take_student):
    act, g, east, check = act_with_positions
    g2, check2 = _second_shift(db, act, g)
    tid = _add_attached(client, admin, act, g, positions=[check.id, check2.id])
    only_g, both = take_student(), take_student()
    _enroll(client, only_g, act, g, check)
    _enroll(client, both, act, g, check)
    _enroll(client, both, act, g2, check2)
    assert set(_roster(client, admin, tid)) == {only_g, both}

    with editing(client, admin, act.id):
        r = client.delete(f"/api/staff/shifts/{g.id}?force=1", headers=admin)
        assert r.status_code == 200, r.text
    # 只报了被删那一场的人退掉培训；第二天还有检录台的人照样要来
    assert set(_roster(client, admin, tid)) == {both}


def test_复制出的时段也叫原岗位的培训_圈外岗位不收(client, admin, db, act_with_positions):
    act, g, east, check = act_with_positions
    g2, _check2 = _second_shift(db, act, g)
    tid = _add_attached(client, admin, act, g, positions=[east.id])
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/shifts/{g2.id}/positions", headers=admin,
                        json={"name": "东门引导", "quota": 2, "copy_from": east.id,
                              **POSITION_TEXT})
        assert r.status_code == 200, r.text
        new_id = r.json()["id"]
        bad = client.patch(f"/api/staff/shifts/{tid}", headers=admin,
                           json={"applies_to_positions": ["p_not_here"]})
        assert bad.status_code == 400 and "不属于本活动" in bad.json()["detail"]
    db.expire_all()
    assert db.get(Shift, tid).applies_to_positions == [east.id, new_id]
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert d["trainings"][0]["applies_text"] == "东门引导"


def test_招募文案按校青协定的格式拼(client, admin, db, act_with_positions):
    import re
    from backend import config

    act, g, east, _c = act_with_positions
    _add_attached(client, admin, act, g, positions=[east.id])
    assert client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                        json={"intro": "演出当天需要引导与检录。", "organizer": "校团委",
                              "volunteer_requirement": "需全程站立",
                              "volunteer_benefit": "提供午餐与纪念徽章",
                              "recruit_stages": [config.STAGES[0]]}).status_code == 200
    base = "https://v.ruc.edu.cn"           # 测试环境配的正式域名
    url = f"/api/staff/activities/{act.id}/poster?base={base}"
    text = client.get(url, headers=admin).json()["text"]
    lines = text.split("\n")
    # 简介去掉句号接「现面向……」；[玫瑰] 原样写，发到微信里显示成表情
    assert lines[:6] == [f"志愿招募|{act.title}", "各位同学大家好！", "【活动内容简介】",
                         f"演出当天需要引导与检录，现面向全校{config.STAGES[0]}招募志愿者[玫瑰]",
                         "【活动主办单位】", "校团委"]
    # 活动时间一整段起止，和网页上的「活动时间」同一个写法
    assert lines[6] == "【活动时间】"
    assert lines[7] == act.time_text()
    assert re.fullmatch(r"\d{4}-\d\d-\d\d（周.）(~ \d{4}-\d\d-\d\d（周.）)?", lines[7])
    assert client.get(f"/api/activities/{act.id}", headers=admin).json()["time_text"] == lines[7]
    # 选填的段有内容才出现，夹在「服务内容」和「志愿者保障」之间
    assert [s for s in lines if s.startswith("【")] == [
        "【活动内容简介】", "【活动主办单位】", "【活动时间】", "【活动地点】",
        "【服务内容及招募人数】", "【培训与彩排】", "【志愿者要求】", "【志愿者保障】",
        "【报名方式】"]
    assert "  · 东门引导 3人：无特殊要求" in lines and "    岗位地点：北门" in lines
    assert any("排练厅（东门引导需参加，计入时长）" in s for s in lines)
    assert lines[lines.index("【志愿者保障】") + 1] == "提供午餐与纪念徽章"
    # 报名方式按校青协定的原文；链接两边留空格，免得微信把旁边的字吞进网址
    assert lines[-1].startswith(
        f"有意愿参加的同学请【点击链接 {base}/activity/{act.id} 或 扫描下方二维码报名】(二选一)，")
    # 报名截止、联系方式不写；招募文案对外，更不能带名单
    assert "报名截止" not in text and "13900000000" not in text and "学号" not in text

    client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                 json={"volunteer_requirement": ""})
    assert "【志愿者要求】" not in client.get(url, headers=admin).json()["text"]
    # base 只认本平台域名
    r = client.get(f"/api/staff/activities/{act.id}/poster?base=https://evil.example",
                   headers=admin)
    assert r.status_code == 400


def test_招募文案多场次时场次一行带菱形_岗位缩进带圆点(client, admin, db, act_with_positions):
    act, g, _east, _c = act_with_positions
    g2, _check2 = _second_shift(db, act, g)
    text = client.get(f"/api/staff/activities/{act.id}/poster", headers=admin).json()["text"]
    lines = text.split("\n")
    # 两个场次也只写一整段活动时间
    assert lines[lines.index("【活动时间】") + 1] == act.time_text()
    body = lines[lines.index("【服务内容及招募人数】") + 1:]
    heads = [s for s in body if s.startswith("◆ ")]
    assert [h.split("　")[0] for h in heads] == [f"◆ {g.name}", f"◆ {g2.name}"]
    assert body[0] == heads[0]
    assert any(s.startswith("  · 检录台 3人") for s in body)


def test_招募文案的时间与招募范围写法():
    from datetime import datetime
    from types import SimpleNamespace

    from backend.routers.staff_ops import _cn_span
    from backend.serializers import recruit_scope_phrase

    assert _cn_span(datetime(2026, 10, 18, 7, 30), datetime(2026, 10, 18, 12)) == \
        "10月18日（周日）07:30—12:00"
    assert _cn_span(datetime(2026, 10, 1, 22), datetime(2026, 10, 2, 2)) == \
        "10月1日（周四）22:00—10月2日（周五）02:00"

    def scope(campuses=(), colleges=(), stages=()):
        return recruit_scope_phrase(SimpleNamespace(
            recruit_campuses=list(campuses), recruit_colleges=list(colleges),
            recruit_stages=list(stages)))
    assert scope() == "全校同学"
    assert scope(stages=["本科生", "硕士研究生"]) == "全校本科生、硕士研究生"
    assert scope(campuses=["中关村校区"], stages=["本科生"]) == "中关村校区本科生"
    assert scope(colleges=["信息学院", "法学院"]) == "信息学院、法学院同学"


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
    assert r.status_code == 400 and "没有需要提醒的志愿者" in r.json()["detail"]

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
    set_shift_time(db, s, start=now() - timedelta(minutes=5))
    db.commit()

    r = client.post(f"/api/activities/enrollments/{enr.id}/excuse", headers=h,
                    json={"excused": True, "reason": "临时有事"})
    assert r.status_code == 409 and "已经开始" in r.json()["detail"]


# ======================= 补招：培训/彩排由负责人勾选 =======================

def _walkin(client, admin, g, sid, **body):
    return client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                       json={"student_id": sid, **body})


def test_补招默认不连带培训彩排(client, admin, db, act_with_positions, take_student):
    act, g, east, _check = act_with_positions
    rehearsal = _add_attached(client, admin, act, g)
    sid = take_student()
    r = _walkin(client, admin, g, sid, position_id=east.id)
    assert r.status_code == 200 and r.json()["attached"] == []
    assert sid in _roster(client, admin, g.id)
    assert sid not in _roster(client, admin, rehearsal)


def test_补招勾选的培训彩排一并报上(client, admin, db, act_with_positions, take_student):
    act, g, east, _check = act_with_positions
    rehearsal = _add_attached(client, admin, act, g)
    training = _add_attached(client, admin, act, g, kind="training", days=6)
    sid = take_student()
    r = _walkin(client, admin, g, sid, position_id=east.id, attached_ids=[rehearsal])
    assert r.status_code == 200 and r.json()["attached"] == [rehearsal]
    assert sid in _roster(client, admin, rehearsal)
    assert sid not in _roster(client, admin, training)       # 没勾的不报
    db.expire_all()
    from backend.models import AuditLog, Notification
    assert (db.query(AuditLog).filter(AuditLog.activity_id == act.id,
                                      AuditLog.summary.like("%并报上彩排%")).count() == 1)
    u = student_row(db, sid)
    note = (db.query(Notification).filter(Notification.user_id == u.id,
                                          Notification.title.like("您已被补招%")).one())
    assert "排练厅" in note.content


def test_补招只能勾对这个岗位生效的彩排(client, admin, db, act_with_positions, take_student):
    act, g, east, check = act_with_positions
    east_only = _add_attached(client, admin, act, g, positions=[east.id])
    r = _walkin(client, admin, g, take_student(), position_id=check.id,
                attached_ids=[east_only])
    assert r.status_code == 400 and "不生效" in r.json()["detail"]


def test_补招时彩排时段冲突先提示_确认后照报(client, admin, db, act_with_positions,
                                        take_student):
    act, g, east, _check = act_with_positions
    rehearsal = _add_attached(client, admin, act, g, days=5)
    sid = take_student()
    # 这个人在彩排那段时间已经报了别的活动
    other_act = Activity(id=uid("act"), title=f"撞车活动 {uid('t')}", intro="x",
                         location="x", status="published",
                         created_by=student_row(db, ADMIN).id)
    db.add(other_act)
    db.flush()
    rs = db.get(Shift, rehearsal)
    other = mk_shift(id=uid("g"), activity_id=other_act.id, name="撞车场",
                  start_time=rs.start_time, end_time=rs.end_time, mode="pick", sort=0)
    db.add(other)
    db.flush()                  # 会话不自动 flush：先让场次落库，报名的外键才对得上
    db.add(Enrollment(id=uid("enr"), activity_id=other_act.id, shift_id=other.id,
                      user_id=student_row(db, sid).id, status="enrolled",
                      joined_at=now()))
    db.commit()
    r = _walkin(client, admin, g, sid, position_id=east.id, attached_ids=[rehearsal])
    detail = r.json()["detail"]
    assert r.status_code == 409 and detail["code"] == "time_conflict"
    assert "彩排" in detail["message"] and detail["conflicts"][0]["shift_name"] == "撞车场"
    # 勾强制不管用：强制只管名额，冲突要确认
    r = _walkin(client, admin, g, sid, position_id=east.id, attached_ids=[rehearsal],
                force=True)
    assert r.status_code == 409 and r.json()["detail"]["code"] == "time_conflict"
    r = _walkin(client, admin, g, sid, position_id=east.id, attached_ids=[rehearsal],
                confirm_conflict=True)
    assert r.status_code == 200 and r.json()["attached"] == [rehearsal]
    assert any("冲突" in x for x in r.json()["forced_reasons"])


def _walkin_notice(client, sid, act):
    items = client.get("/api/me/notifications", headers=login(client, sid)).json()["items"]
    return [n for n in items if act.title in n["title"]]


def test_补招通知写明岗位与培训_点开进我的报名(client, admin, db, act_with_positions,
                                     take_student):
    act, g, east, _check = act_with_positions
    rehearsal = _add_attached(client, admin, act, g)
    sid = take_student()
    assert _walkin(client, admin, g, sid, position_id=east.id,
                   attached_ids=[rehearsal]).status_code == 200
    [n] = _walkin_notice(client, sid, act)
    assert n["title"] == f"您已被补招｜{act.title}"
    assert "岗位：东门引导" in n["content"] and "彩排" in n["content"]
    assert "如无法参加" in n["content"] and n["page"] == "/my"


def test_场次结束后补招_通知写补记而不叫人来(client, admin, db, act_with_positions,
                                  take_student):
    act, g, east, _check = act_with_positions
    s = db.get(Shift, g.id)
    set_shift_time(db, s, start=now() - timedelta(hours=5),
                   end=now() - timedelta(hours=1))
    db.commit()
    sid = take_student()
    assert _walkin(client, admin, g, sid, position_id=east.id,
                   force=True).status_code == 200
    [n] = _walkin_notice(client, sid, act)
    assert n["title"] == f"已补记为志愿者｜{act.title}"
    assert "时长以负责人登记为准" in n["content"]
    assert "如无法参加" not in n["content"] and n["page"] == "/my"
