"""内部提示分的算法：按活动计、按时间顺序逐条累计，每一步夹在 [0, 满分]。

直接往库里造「过去某个时刻」的记录再算分——走接口造不出一个月前结束、
当时签过到的活动。撤销违规那条走接口，看分数跟着回来。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import timedelta

from sqlalchemy import event

from backend import config, scoring
from backend.database import engine
from backend.models import (Activity, ActivityManager, CheckinRecord, CheckinTask,
                            Enrollment, Shift, ViolationRecord, iso, now, uid)
from conftest import ADMIN, student_row

W = config.VIOLATION_WEIGHTS
PLUS, LOCKED = config.SCORE_PER_ACTIVITY, config.LOCKED_QUIT_WEIGHT


def _activity(db, *ends, title="提示分测试"):
    """一个活动，每个 end（datetime）一个主场次，三小时长。返回 (活动, [场次])。"""
    admin = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"{title} {uid('t')}", intro="x",
                   location="世纪馆", contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
                   reg_start=now() - timedelta(days=90),
                   reg_end=now() - timedelta(days=60), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=admin.id,
                           added_by=admin.id))
    shifts = []
    for i, end in enumerate(ends):
        g = Shift(id=uid("g"), activity_id=act.id, name=f"第{i + 1}场",
                  start_time=end - timedelta(hours=3), end_time=end,
                  mode="pick", sort=i)
        db.add(g)
        shifts.append(g)
    db.flush()
    return act, shifts


def _enroll(db, u, act, g, *, cancel_type="", cancelled_at=None):
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id, user_id=u.id,
                      status="cancelled" if cancel_type else "enrolled",
                      cancel_type=cancel_type, cancelled_at=cancelled_at,
                      joined_at=g.start_time - timedelta(days=7)))


def _checkin(db, u, act, g, method="scan"):
    t = CheckinTask(id=uid("ckt"), activity_id=act.id, shift_id=g.id,
                    token=uid("k")[-16:], created_by=u.id)
    db.add(t)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=t.id, activity_id=act.id,
                         shift_id=g.id, user_id=u.id, method=method,
                         checkin_at=g.start_time))


def _served(db, u, end, **kw):
    """一个顺利参与的活动：报名、签到、结束在 end。"""
    act, (g,) = _activity(db, end, **kw)
    _enroll(db, u, act, g)
    _checkin(db, u, act, g)
    return act


def _violate(db, u, at, kind, act=None):
    if act is None:
        act, _ = _activity(db, at, title="违规所在活动")
    v = ViolationRecord(id=uid("vio"), user_id=u.id, activity_id=act.id,
                        activity_title=act.title, kind=kind, detail="x",
                        created_at=at)
    db.add(v)
    return v


def _day(n):
    """n 天前的某一刻；n 越大越早。"""
    return now() - timedelta(days=n)


def test_按时间顺序逐条累计(db, take_student):
    """100 −10 +5 +5 +5 +5 −15 = 85：到顶之后的加分不存起来。
    旧算法先加总再夹：100 − 10 + 20 − 15 = 95。"""
    u = student_row(db, take_student())
    _violate(db, u, _day(60), "late")
    for n in (50, 40, 30, 20):
        _served(db, u, _day(n))
    _violate(db, u, _day(10), "leave")
    db.commit()

    d = scoring.detail(db, u)
    assert [it["delta"] for it in d["items"]] == [-W["late"], PLUS, PLUS, PLUS, PLUS,
                                                  -W["leave"]]
    assert [it["score"] for it in d["items"]] == [90, 95, 100, 100, 100, 85]
    assert d["score"] == 85 == scoring.score_of(db, u.id)
    assert [it["label"] for it in d["items"]][:2] == ["迟到早退", "顺利参与"]
    assert all(it["at"] and it["activity_title"] for it in d["items"])


def test_满分时加分不累积(db, take_student):
    u = student_row(db, take_student())
    for n in range(20):
        _served(db, u, _day(100 - n))
    for n in range(5):
        _violate(db, u, _day(50 - n), "leave")
    db.commit()
    assert scoring.score_of(db, u.id) == max(config.SCORE_MIN,
                                            config.SCORE_BASE - 5 * W["leave"])
    assert scoring.brief(db, u.id)["score_alert"] is True


def test_有违规或锁定后退出的活动不加分(db, take_student):
    u = student_row(db, take_student())
    _violate(db, u, _day(60), "leave")                  # 先压到 85，看得出加没加
    # 签过到但登记了违规：只扣不加
    act = _served(db, u, _day(40))
    _violate(db, u, _day(39), "other", act)
    # 一场签过到、另一场锁定后退了：只扣锁定后退出，不加
    act2, (g1, g2) = _activity(db, _day(30), _day(29))
    _enroll(db, u, act2, g1)
    _checkin(db, u, act2, g1)
    _enroll(db, u, act2, g2, cancel_type="locked", cancelled_at=_day(31))
    db.commit()

    d = scoring.detail(db, u)
    assert [it["label"] for it in d["items"]] == ["无故请假", "其他", "锁定后退出"]
    assert d["score"] == config.SCORE_BASE - W["leave"] - W["other"] - LOCKED


def test_多场次只加一次也只扣一次(db, take_student):
    u = student_row(db, take_student())
    _violate(db, u, _day(60), "leave")
    # 两场都报了、都签到：加一次，时间是后一场结束
    act, (g1, g2) = _activity(db, _day(41), _day(40))
    for g in (g1, g2):
        _enroll(db, u, act, g)
        _checkin(db, u, act, g)
    # 三场都锁定后退了：扣一次，时间是最早那次退选
    act2, gs = _activity(db, _day(20), _day(19), _day(18))
    cancels = [_day(21), _day(22), _day(21.5)]
    for g, at in zip(gs, cancels):
        _enroll(db, u, act2, g, cancel_type="locked", cancelled_at=at)
    db.commit()

    items = scoring.detail(db, u)["items"]
    assert [it["delta"] for it in items] == [-W["leave"], PLUS, -LOCKED]
    assert items[1]["at"] == iso(g2.end_time)
    assert items[2]["at"] == iso(cancels[1])
    assert scoring.score_of(db, u.id) == config.SCORE_BASE - W["leave"] + PLUS - LOCKED


def test_没签到或活动没结束不加分(db, take_student):
    u = student_row(db, take_student())
    _violate(db, u, _day(60), "leave")
    # 报了、结束了，没签到
    act, (g,) = _activity(db, _day(40))
    _enroll(db, u, act, g)
    # 第一场签过到，第二场还没结束：整个活动还不算完
    act2, (g1, g2) = _activity(db, _day(1), now() + timedelta(days=1))
    for x in (g1, g2):
        _enroll(db, u, act2, x)
    _checkin(db, u, act2, g1)
    db.commit()
    assert scoring.score_of(db, u.id) == config.SCORE_BASE - W["leave"]

    # 管理者补签也算到岗
    _checkin(db, u, act, g, method="manual")
    db.commit()
    assert scoring.score_of(db, u.id) == config.SCORE_BASE - W["leave"] + PLUS


def test_撤销违规后分数恢复(client, admin, db, take_student):
    u = student_row(db, take_student())
    _violate(db, u, _day(60), "leave")
    act = _served(db, u, _day(30))
    db.commit()
    url = f"/api/staff/users/{u.id}/archive"
    before = client.get(url, headers=admin).json()["score"]["score"]
    assert before == config.SCORE_BASE - W["leave"] + PLUS

    r = client.post("/api/staff/violations", headers=admin,
                    json={"user_id": u.id, "activity_id": act.id,
                          "kind": "late", "detail": "迟到半小时"})
    assert r.status_code == 200, r.text
    # 登记在这个活动上：扣一条，这个活动的加分也没了
    d = client.get(url, headers=admin).json()["score"]
    assert d["score"] == config.SCORE_BASE - W["leave"] - W["late"]
    assert [it["label"] for it in d["items"]] == ["无故请假", "迟到早退"]

    assert client.delete(f"/api/staff/violations/{r.json()['id']}",
                         headers=admin).status_code == 200
    assert client.get(url, headers=admin).json()["score"]["score"] == before


@contextmanager
def _counting():
    n = {"sql": 0}

    def _count(*_a):
        n["sql"] += 1
    event.listen(engine, "before_cursor_execute", _count)
    try:
        yield n
    finally:
        event.remove(engine, "before_cursor_execute", _count)


def test_批量算分条数固定且与逐个算一致(db, take_student):
    users = [student_row(db, take_student()) for _ in range(4)]
    for i, u in enumerate(users):
        for n in range(i + 1):
            _served(db, u, _day(40 - n))
        _violate(db, u, _day(50), "leave")
        _violate(db, u, _day(10), "late")
    db.commit()
    ids = [u.id for u in users]
    with _counting() as n:
        got = scoring.briefs(db, ids)
    # 违规、主场次报名、签到、补记录名单各一条，与人数无关
    assert n["sql"] == 4, n
    assert got == {uid_: scoring.brief(db, uid_) for uid_ in ids}
    assert scoring.briefs(db, []) == {}
