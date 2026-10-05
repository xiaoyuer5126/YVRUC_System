"""人员变动：流水、临期减员提醒、日报，以及组长联系方式的可见范围。"""
from __future__ import annotations

from datetime import timedelta

from backend import config, scheduled
from backend.models import (Enrollment, Notification, Position, Shift, now,
                            uid)
from conftest import POSITION_TEXT, editing, login, student_row


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


def _digest_bodies(db, act, g) -> list[str]:
    return [n.content for n in db.query(Notification)
            .filter(Notification.title == f"[人员变动] {act.title} · {g.name}")
            .all()]


def _second_shift(db, act, g, *, hours_later: float) -> Shift:
    """同一活动里再加一场服务，岗位名额与第一场相同。"""
    g2 = Shift(id=uid("g"), activity_id=act.id, name="第二场",
               start_time=g.start_time + timedelta(hours=hours_later),
               end_time=g.end_time + timedelta(hours=hours_later),
               mode="pick", sort=1)
    db.add(g2)
    db.flush()
    p = Position(id=uid("p"), shift_id=g2.id, name="测试岗位", quota=3,
                 sort=0, **POSITION_TEXT)
    db.add(p)
    db.commit()
    g2.test_position_id = p.id
    return g2


def _add_training(client, admin, act, *, hours: float) -> Shift:
    start = now().replace(microsecond=0) + timedelta(hours=hours)
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                        json={"name": "岗前培训", "kind": "training",
                              "applies_to_positions": [], "hours_mode": "fixed",
                              "location": "排练厅",
                              "start_time": start.isoformat(),
                              "end_time": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _excuse(client, db, shift_id, sid, excused, reason=""):
    u = student_row(db, sid)
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == shift_id, Enrollment.user_id == u.id)
           .one())
    r = client.post(f"/api/activities/enrollments/{enr.id}/excuse",
                    headers=login(client, sid),
                    json={"excused": excused, "reason": reason})
    assert r.status_code == 200, r.text
    return u


def test_日报按场次分开统计(client, db, open_group, take_student, monkeypatch):
    """同一活动两场：各报各的增减，不把整个活动的数字抄给每一场。"""
    act, g = open_group(3, day_offset=0, start_in_hours=30)
    g2 = _second_shift(db, act, g, hours_later=10)
    quiet = _second_shift(db, act, g, hours_later=20)
    for _ in range(2):
        assert _enroll(client, act, g, take_student()).status_code == 200
    gone = take_student()
    enr_id = _enroll(client, act, g2, gone).json()["enrollment_id"]
    client.delete(f"/api/activities/enrollments/{enr_id}",
                  headers=login(client, gone))
    assert _enroll(client, act, g2, take_student()).status_code == 200
    monkeypatch.setattr(config, "ROSTER_DIGEST_HOUR", 0)

    scheduled.send_roster_digests(db)
    [first] = _digest_bodies(db, act, g)
    assert "新增 2 人，退出 0 人" in first and "当前 2/3 人，缺 1 人" in first
    [second] = _digest_bodies(db, act, g2)
    assert "新增 2 人，退出 1 人" in second and "当前 1/3 人，缺 2 人" in second
    assert "请假" not in first + second     # 没有请假销假就没有这一段
    assert _digest_bodies(db, act, quiet) == [], "没变动的场次不发"


def test_培训请假进日报(client, admin, db, open_group, take_student,
                      monkeypatch):
    """培训/彩排只有请假销假时，正文只列这些，不出现「新增 0 人」。"""
    act, g = open_group(3, day_offset=0, start_in_hours=60)
    tid = _add_training(client, admin, act, hours=30)
    a, b, c = take_student(), take_student(), take_student()
    for sid in (a, b, c):
        assert _enroll(client, act, g, sid).status_code == 200
    ua = _excuse(client, db, tid, a, True, "与专业课冲突")
    ub = _excuse(client, db, tid, b, True, "临时有事")
    _excuse(client, db, tid, b, False)
    monkeypatch.setattr(config, "ROSTER_DIGEST_HOUR", 0)

    scheduled.send_roster_digests(db)
    training = db.get(Shift, tid)
    [body] = _digest_bodies(db, act, training)
    assert body.split("\n")[:4] == [
        "请假/销假：",
        f"· {ua.name}（{ua.student_id}）请假「岗前培训」：与专业课冲突",
        f"· {ub.name}（{ub.student_id}）请假「岗前培训」：临时有事",
        f"· {ub.name}（{ub.student_id}）销假「岗前培训」",
    ]
    assert "新增" not in body and "退出" not in body
    assert "当前在册 3 人，其中请假 1 人。" in body
    # 服务场次照常报增减，培训的请假不混进来
    [service] = _digest_bodies(db, act, g)
    assert "新增 3 人，退出 0 人" in service and "请假" not in service


def test_日报没有变动不发(client, db, open_group, take_student, monkeypatch):
    """24 小时之前的变动和候补的进出都不算。"""
    act, g = open_group(1, day_offset=0, start_in_hours=40)
    _enroll(client, act, g, take_student())
    from backend.models import AuditLog
    (db.query(AuditLog).filter(AuditLog.activity_id == act.id)
     .update({AuditLog.created_at: now() - timedelta(days=2)}))
    db.commit()
    assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"
    monkeypatch.setattr(config, "ROSTER_DIGEST_HOUR", 0)

    scheduled.send_roster_digests(db)
    assert _digest_bodies(db, act, g) == []


def test_请假不即时通知管理者(client, admin, db, open_group, take_student):
    """临近开始的请假也只进第二天的日报，不逐条推。"""
    act, g = open_group(3, day_offset=0, start_in_hours=60)
    tid = _add_training(client, admin, act, hours=5)
    sid = take_student()
    assert _enroll(client, act, g, sid).status_code == 200
    before = db.query(Notification).count()
    _excuse(client, db, tid, sid, True, "家里有事")
    assert db.query(Notification).count() == before


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
