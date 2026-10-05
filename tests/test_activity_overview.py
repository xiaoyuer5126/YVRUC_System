"""活动管理页的「活动概况」：各项数字的口径、权限、补记录活动、SQL 条数。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import (Activity, ActivityManager, CheckinRecord, CheckinTask,
                            Enrollment, HoursRecord, Position, Shift,
                            ViolationRecord, now, uid)
from conftest import ADMIN, POSITION_TEXT, login, student_row
from test_query_budget import sql_of

_days = iter(range(700, 10_000, 3))


def _activity(db, **kw) -> Activity:
    admin = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"概况 {uid('t')}", intro="x",
                   location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=1), created_by=admin.id, **kw)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    db.flush()
    return act


def _shift(db, act, name, start, **kw) -> Shift:
    g = Shift(id=uid("g"), activity_id=act.id, name=name, start_time=start,
              end_time=start + timedelta(hours=3), **kw)
    db.add(g)
    db.flush()
    return g


def _pos(db, g, name, quota, **kw) -> Position:
    p = Position(id=uid("p"), shift_id=g.id, name=name, quota=quota,
                 waitlist_quota=3, **POSITION_TEXT, **kw)
    db.add(p)
    db.flush()
    return p


def _enr(db, act, g, p, u, status="enrolled", **kw) -> Enrollment:
    e = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                   position_id=p.id if p else None, user_id=u.id,
                   status=status, joined_at=now(), **kw)
    db.add(e)
    return e


def _checkin(db, act, g, task, u, out=False):
    db.add(CheckinRecord(id=uid("ck"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, user_id=u.id,
                         checkin_at=now() - timedelta(minutes=30),
                         checkout_at=now() if out else None))


def test_概况各项数字(client, admin, db, take_student):
    u1, u2, u3, u4, u5, u6, u7 = (student_row(db, take_student()) for _ in range(7))
    act = _activity(db)
    # 已开始的「报名即选岗位」场次：岗位甲 2 人满、岗位乙暂停报名
    s1 = _shift(db, act, "上午场", now() - timedelta(hours=1), mode="pick", sort=0)
    pa = _pos(db, s1, "岗位甲", 2, sort=0)
    pb = _pos(db, s1, "岗位乙", 3, sort=1, enroll_paused=True)
    _enr(db, act, s1, pa, u1)
    _enr(db, act, s1, pa, u2)
    _enr(db, act, s1, pa, u3, "waitlisted")
    _enr(db, act, s1, pb, u4, source="party_school")
    _enr(db, act, s1, pb, u5, "cancelled", cancel_type="late", seat_held=True)
    # 还没开始的「截止后分配」场次：整场 4 个名额，另有一个 2 人的真实岗位
    day = next(_days)
    s2 = _shift(db, act, "下午场", now() + timedelta(days=day), mode="assign", sort=1)
    holder = _pos(db, s2, "待分配", 4, is_placeholder=True, sort=-1)
    real = _pos(db, s2, "引导", 2, sort=0)
    _enr(db, act, s2, holder, u1)
    _enr(db, act, s2, real, u6)
    _enr(db, act, s2, holder, u7, "pending_confirm")
    # 培训不进名额、报名、签到
    tr = _shift(db, act, "培训", now() - timedelta(hours=5), kind="training")
    _enr(db, act, tr, None, u1)
    _enr(db, act, tr, None, u3)

    task = CheckinTask(id=uid("ct"), activity_id=act.id, shift_id=s1.id,
                       token=uid("t")[-12:], created_by=u1.id)
    db.add(task)
    db.flush()
    _checkin(db, act, s1, task, u1)               # 签了到没签退
    _checkin(db, act, s1, task, u2, out=True)
    _checkin(db, act, tr, task, u3)               # 培训的签到不算
    db.add_all([
        HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u1.id, hours=3),
        HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u2.id, hours=2,
                    parts=[{"kind": "shift", "shift_id": s1.id, "hours": 2,
                            "reward": "pay"}]),     # 选了劳务，不计时长
        HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u4.id, hours=1.5),
        ViolationRecord(id=uid("vio"), user_id=u4.id, activity_id=act.id,
                        kind="late"),
    ])
    db.commit()

    r = client.get(f"/api/staff/activities/{act.id}/overview", headers=admin)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["record"] is False
    assert d["enroll"] == {"count": 5, "people": 4, "capacity": 9, "occupied": 7,
                           "waitlist": 2, "applied": 0}
    assert d["checkin"] == {"due": 3, "arrived": 2, "rate": 0.6667,
                            "not_checked_out": 1}
    assert d["hours"] == {"total": 4.5, "party_school": 1.5, "records": 3,
                          "approved": False, "locked": False}
    assert d["violations"] == 1 and d["late_cancel"] == 1

    assert [g["name"] for g in d["shifts"]] == ["上午场", "下午场"]
    g1, g2 = d["shifts"]
    assert (g1["enrolled"], g1["occupied"], g1["capacity"], g1["waitlist"],
            g1["full"], g1["started"]) == (3, 4, 5, 1, False, True)
    by = {p["name"]: p for p in g1["positions"]}
    assert (by["岗位甲"]["occupied"], by["岗位甲"]["quota"], by["岗位甲"]["full"],
            by["岗位甲"]["waitlist"]) == (2, 2, True, 1)
    assert by["岗位乙"]["paused"] is True and by["岗位乙"]["occupied"] == 2
    assert by["岗位乙"]["full"] is False
    assert (g2["capacity"], g2["occupied"], g2["unassigned"], g2["full"],
            g2["started"]) == (4, 3, 1, False, False)
    assert [(p["name"], p["enrolled"], p["quota"]) for p in g2["positions"]] \
        == [("引导", 1, 2)]


def test_概况的权限(client, admin, initiator, db, take_student):
    act = _activity(db)
    db.commit()
    path = f"/api/staff/activities/{act.id}/overview"
    assert client.get(path, headers=initiator).status_code == 403
    assert client.get(path, headers=login(client, take_student())).status_code == 403
    assert client.get("/api/staff/activities/act_nope/overview",
                      headers=admin).status_code == 404


def test_补记录活动只给名单时长违规(client, admin, db, take_student):
    u1, u2 = (student_row(db, take_student()) for _ in range(2))
    act = _activity(db, recruit_mode="record")
    db.add_all([
        HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u1.id, hours=4,
                    source="import"),
        HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u2.id, hours=2.5,
                    source="import"),
        ViolationRecord(id=uid("vio"), user_id=u2.id, activity_id=act.id,
                        kind="other"),
    ])
    db.commit()
    d = client.get(f"/api/staff/activities/{act.id}/overview", headers=admin).json()
    assert d["record"] is True and d["roster"] == 2
    assert d["hours"]["total"] == 6.5 and d["violations"] == 1
    assert "enroll" not in d and "shifts" not in d


def _crowd(db, take_student, n: int) -> str:
    """两个场次、各 n 个岗位，每岗位一人报名并签到、一人候补、一笔时长。"""
    act = _activity(db)
    for k in range(2):
        g = _shift(db, act, f"场次{k}", now() - timedelta(days=next(_days) % 7 + 1),
                   mode="pick", sort=k)
        task = CheckinTask(id=uid("ct"), activity_id=act.id, shift_id=g.id,
                           token=uid("t")[-12:], created_by=act.created_by)
        db.add(task)
        db.flush()
        for i in range(n):
            p = _pos(db, g, f"岗位{i}", 1, sort=i)
            a, b = (student_row(db, take_student()) for _ in range(2))
            _enr(db, act, g, p, a)
            _enr(db, act, g, p, b, "waitlisted")
            _checkin(db, act, g, task, a)
            db.add(HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=a.id,
                               hours=1))
            db.add(ViolationRecord(id=uid("vio"), user_id=a.id,
                                   activity_id=act.id, kind="late"))
    db.commit()
    return act.id


def test_概况的条数不随人数涨(client, admin, db, take_student):
    few, many = _crowd(db, take_student, 1), _crowd(db, take_student, 8)
    path = "/api/staff/activities/{}/overview"
    assert sql_of(client, admin, path.format(few)) \
        == sql_of(client, admin, path.format(many))
    d = client.get(path.format(many), headers=admin).json()
    assert d["enroll"]["count"] == 16 and d["checkin"]["rate"] == 1
