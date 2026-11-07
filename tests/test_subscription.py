"""订阅报名开始提醒：订阅 / 取消、能订的条件、定时提醒、概览人数。"""
from __future__ import annotations

from datetime import timedelta

from backend import scheduled
from backend.models import Activity, ActivitySubscription, now

from conftest import inbox, login, student_row

TITLE = "志愿活动报名提醒｜"


def _upcoming(db, open_group, opens_in=timedelta(hours=2)):
    """一个还没开始报名的活动：报名开始时间在 opens_in 之后。"""
    act, _g = open_group(5)
    act.reg_start = now() + opens_in
    db.commit()
    return act


def _url(act):
    return f"/api/activities/{act.id}/subscription"


def _due(db, act):
    """把报名开始挪到提醒窗口里（离开始不到 3 分钟），模拟时间走到了。"""
    db.expire_all()
    a = db.get(Activity, act.id)
    a.reg_start = now() + timedelta(minutes=2)
    db.commit()


def test_订阅后详情和广场都显示已订阅_取消后恢复(client, db, open_group, take_student):
    act = _upcoming(db, open_group)
    h = login(client, take_student())
    assert client.get(f"/api/activities/{act.id}", headers=h).json()["subscribed"] is False

    r = client.post(_url(act), headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["lead_minutes"] == 3
    # 连点两下不报错，也不多出一条
    assert client.post(_url(act), headers=h).status_code == 200
    db.expire_all()
    assert db.query(ActivitySubscription).filter_by(activity_id=act.id).count() == 1

    assert client.get(f"/api/activities/{act.id}", headers=h).json()["subscribed"] is True
    square = client.get("/api/activities?status=upcoming&size=50", headers=h).json()
    card = next(x for x in square["items"] if x["id"] == act.id)
    assert card["subscribed"] is True
    assert square["reg_reminder_minutes"] == 3

    assert client.delete(_url(act), headers=h).status_code == 200
    assert client.get(f"/api/activities/{act.id}", headers=h).json()["subscribed"] is False
    # 没订过也能取消，不报错
    assert client.delete(_url(act), headers=h).status_code == 200


def test_已开始报名或马上开始的活动不能订阅(client, db, open_group, take_student):
    h = login(client, take_student())
    started, _g = open_group(5)          # open_group 默认昨天就开始报名了
    r = client.post(_url(started), headers=h)
    assert r.status_code == 409 and "已经开始" in r.json()["detail"]

    soon = _upcoming(db, open_group, opens_in=timedelta(minutes=2))
    r = client.post(_url(soon), headers=h)
    assert r.status_code == 409 and "马上开始" in r.json()["detail"]


def test_招募范围外的人不能订阅(client, db, open_group, take_student):
    act = _upcoming(db, open_group)
    act.recruit_campuses = ["苏州校区"]   # 用例学生在中关村校区
    db.commit()
    h = login(client, take_student())
    assert client.post(_url(act), headers=h).status_code == 403


def test_报名开始前提醒一次_不重复发(client, db, open_group, take_student):
    act = _upcoming(db, open_group)
    sid = take_student()
    h = login(client, sid)
    assert client.post(_url(act), headers=h).status_code == 200

    # 还没到提醒窗口：不发
    scheduled.send_reg_open_reminders(db)
    assert inbox(db, sid, TITLE + act.title) == []

    _due(db, act)
    scheduled.send_reg_open_reminders(db)
    notes = inbox(db, sid, TITLE + act.title)
    assert len(notes) == 1
    assert "即将开始报名" in notes[0].content and "测试地点" in notes[0].content
    assert notes[0].data["activity_id"] == act.id

    # 下一分钟再跑一遍：已经按这个开始时间提醒过了
    scheduled.send_reg_open_reminders(db)
    assert len(inbox(db, sid, TITLE + act.title)) == 1


def test_改了报名开始时间按新时间再提醒(client, db, open_group, take_student):
    act = _upcoming(db, open_group)
    sid = take_student()
    assert client.post(_url(act), headers=login(client, sid)).status_code == 200
    _due(db, act)
    scheduled.send_reg_open_reminders(db)
    assert len(inbox(db, sid, TITLE + act.title)) == 1

    # 管理者推迟报名，新时间到了再提醒一次
    _due(db, act)
    scheduled.send_reg_open_reminders(db)
    assert len(inbox(db, sid, TITLE + act.title)) == 2


def test_取消订阅_已开始报名_活动撤回_都不提醒(client, db, open_group, take_student):
    cancelled = _upcoming(db, open_group)
    started = _upcoming(db, open_group)
    withdrawn = _upcoming(db, open_group)
    sid = take_student()
    h = login(client, sid)
    for act in (cancelled, started, withdrawn):
        assert client.post(_url(act), headers=h).status_code == 200
    assert client.delete(_url(cancelled), headers=h).status_code == 200

    for act in (cancelled, withdrawn):
        _due(db, act)
    db.expire_all()
    # 停机期间报名已经开了：恢复后不补发「即将开始」
    db.get(Activity, started.id).reg_start = now() - timedelta(minutes=1)
    db.get(Activity, withdrawn.id).status = "draft"
    db.commit()

    scheduled.send_reg_open_reminders(db)
    for act in (cancelled, started, withdrawn):
        assert inbox(db, sid, TITLE + act.title) == []


def test_订阅后被移出招募范围不提醒(client, db, open_group, take_student):
    act = _upcoming(db, open_group)
    sid = take_student()
    assert client.post(_url(act), headers=login(client, sid)).status_code == 200
    _due(db, act)
    db.get(Activity, act.id).recruit_campuses = ["苏州校区"]
    db.commit()
    scheduled.send_reg_open_reminders(db)
    assert inbox(db, sid, TITLE + act.title) == []


def test_概览显示订阅人数(client, db, open_group, take_student, admin):
    act = _upcoming(db, open_group)
    for _ in range(2):
        assert client.post(_url(act), headers=login(client, take_student())).status_code == 200
    d = client.get(f"/api/staff/activities/{act.id}/overview", headers=admin).json()
    assert d["subscribers"] == 2
    assert d["reg_started"] is False


def test_删除草稿时连订阅一起清掉(client, db, open_group, take_student, admin):
    act = _upcoming(db, open_group)
    sid = take_student()
    assert client.post(_url(act), headers=login(client, sid)).status_code == 200
    db.expire_all()
    db.get(Activity, act.id).status = "draft"
    db.commit()
    r = client.delete(f"/api/staff/activities/{act.id}", headers=admin)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(ActivitySubscription).filter_by(
        user_id=student_row(db, sid).id).count() == 0
