"""人员变动：流水、临期减员提醒、日报，以及组长联系方式的可见范围。"""
from __future__ import annotations

from datetime import timedelta

from backend import config, scheduled
from backend.models import Enrollment, Notification, Position, now, uid
from conftest import POSITION_TEXT, login


def _enroll(client, act, g, sid, **body):
    # 不能用 setdefault：默认值会被提前求值，而 test_position_id 只有
    # open_group 造出来的场次才有
    if "position_id" not in body:
        body["position_id"] = g.test_position_id
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=login(client, sid), json=body)


def test_报名与退选进人员变动流水(client, admin, db, open_group, take_student):
    """此前完全不留痕，活动前一天少了人只能到现场才发现。"""
    act, g = open_group(3, day_offset=0, start_in_hours=100)
    sid = take_student()
    enr_id = _enroll(client, act, g, sid).json()["enrollment_id"]
    client.delete(f"/api/activities/enrollments/{enr_id}",
                  headers=login(client, sid))

    roster = client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()
    actions = [c["action"] for c in roster["roster_changes"]]
    assert "enroll" in actions and "cancel" in actions
    assert any(sid in c["summary"] for c in roster["roster_changes"])


def test_候补与转正也记流水(client, admin, db, open_group, take_student):
    act, g = open_group(1, day_offset=0, start_in_hours=100)
    first = take_student()
    enr_id = _enroll(client, act, g, first).json()["enrollment_id"]
    queued = take_student()
    r = _enroll(client, act, g, queued)
    assert r.json()["status"] == "waitlisted"
    wl_id = r.json()["enrollment_id"]
    client.delete(f"/api/activities/enrollments/{enr_id}",
                  headers=login(client, first))
    # 名额空出来 → 候补第一位待确认，本人点确认才算转正
    r = client.post(f"/api/activities/enrollments/{wl_id}/confirm-promotion",
                    headers=login(client, queued))
    assert r.status_code == 200, r.text

    changes = client.get(f"/api/staff/shifts/{g.id}/roster",
                         headers=admin).json()["roster_changes"]
    assert any(c["action"] == "waitlist" and queued in c["summary"]
               for c in changes)
    assert any(c["action"] == "promote" and queued in c["summary"]
               for c in changes)


def _late_dropout_notices(db, act) -> int:
    return (db.query(Notification)
            .filter(Notification.title == f"[临近减员] {act.title}").count())


def test_临期退选立刻提醒管理者(client, db, admin, open_group, take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=5)
    sid = take_student()
    enr_id = _enroll(client, act, g, sid).json()["enrollment_id"]
    client.delete(f"/api/activities/enrollments/{enr_id}",
                  headers=login(client, sid))
    assert _late_dropout_notices(db, act) == 1, \
        "距开始不足 24 小时的减员要立刻推给管理者"


def test_报名期的退选不打扰管理者(client, db, open_group, take_student):
    """报名期一天几十条变动，逐条推等于让老师关掉通知。"""
    act, g = open_group(3, day_offset=0, start_in_hours=100)
    sid = take_student()
    enr_id = _enroll(client, act, g, sid).json()["enrollment_id"]
    client.delete(f"/api/activities/enrollments/{enr_id}",
                  headers=login(client, sid))
    assert _late_dropout_notices(db, act) == 0


def test_人员变动日报每天一条(client, db, open_group, take_student, monkeypatch):
    act, g = open_group(3, day_offset=0, start_in_hours=40)
    _enroll(client, act, g, take_student())
    monkeypatch.setattr(config, "ROSTER_DIGEST_HOUR", 0)

    def digests():
        return (db.query(Notification)
                .filter(Notification.title == f"[人员变动] {act.title} · {g.name}")
                .count())

    scheduled.send_roster_digests(db)
    assert digests() == 1          # 管理者只有 admin 一个
    scheduled.send_roster_digests(db)      # 同一天再跑不重复推
    assert digests() == 1


def test_日报不到点不发(client, db, open_group, take_student, monkeypatch):
    """扫描每分钟一跑，不定点的话零点整就发出去了。"""
    act, g = open_group(3, day_offset=0, start_in_hours=40)
    _enroll(client, act, g, take_student())
    fake = now().replace(hour=config.ROSTER_DIGEST_HOUR) - timedelta(hours=1)
    monkeypatch.setattr(scheduled, "now", lambda: fake)
    assert scheduled.send_roster_digests(db) == 0


def test_组长联系方式只给同组正式志愿者(client, admin, db, enrolled_activity,
                                      take_student):
    act, shift, first = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = True
    other = Position(id=uid("p"), shift_id=shift.id, name="别的组", quota=3,
                     group_name="西门", sort=1, **POSITION_TEXT)
    db.add(other)
    db.commit()

    leader_sid = take_student()
    assert _enroll(client, act, shift, leader_sid, position_id=pos.id,
                   leader_willing=True).status_code == 200
    # 组长由管理者指定
    from backend.models import User
    leader_uid = db.query(User).filter(User.student_id == leader_sid).one().id
    leader_enr = (db.query(Enrollment)
                  .filter(Enrollment.user_id == leader_uid,
                          Enrollment.shift_id == shift.id).one())
    assert client.post(f"/api/staff/enrollments/{leader_enr.id}/leader",
                       headers=admin, json={"is_leader": True}).status_code == 200

    def positions_seen_by(sid):
        d = client.get(f"/api/activities/{act.id}",
                       headers=login(client, sid)).json()
        return {p["name"]: p for p in d["shifts"][0]["positions"]}

    # 同组正式志愿者：看得到姓名与联系方式
    leader = db.get(User, leader_uid)
    mine = positions_seen_by(first.student_id)[pos.name]
    assert mine["leader_name"] == leader.name
    assert mine["leader_contact"] == {"phone": leader.phone,
                                      "wechat": leader.wechat}

    # 别的组的人：看得到姓名，拿不到联系方式
    outsider = take_student()
    assert _enroll(client, act, shift, outsider,
                   position_id=other.id).status_code == 200
    seen = positions_seen_by(outsider)[pos.name]
    assert seen["leader_name"] == leader.name
    assert seen["leader_contact"] is None

    # 完全没报名的人：连姓名都看不到
    stranger = positions_seen_by(take_student())[pos.name]
    assert stranger["leader_name"] == "" and stranger["leader_contact"] is None
