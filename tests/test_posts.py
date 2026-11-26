"""岗位接口：普通岗位自带时段，一个岗位就是一场。

建、改、删、复制都走 /api/staff/activities/{id}/posts 与 /api/staff/posts/{id}，
接口自己维护「场次名 = 岗位名、一场只装一个岗位」。从场次那头动不了普通岗位，
见 test_staff.test_普通岗位不从场次接口动。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Attachment, AuditLog, Enrollment, Position, Shift,
                            ShiftPeriod, now, uid)
from conftest import editing, login, mk_post, student_row, upload_qr


def _periods(*spans) -> list[dict]:
    """(几天后, 几点, 几小时) 三元组列成 periods。"""
    out = []
    for days, hour, hours in spans:
        start = (now() + timedelta(days=days)).replace(
            hour=hour, minute=0, second=0, microsecond=0)
        out.append({"start_time": start.isoformat(),
                    "end_time": (start + timedelta(hours=hours)).isoformat()})
    return out


@pytest.fixture
def draft(client, admin):
    """一个还没发布的活动：改岗位不用进编辑态。"""
    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": upload_qr(client, admin),
                          "title": f"岗位接口 {uid('t')}", "organizer": "校青协",
                          "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_新建岗位连带建好它自己的那一场(client, admin, db, draft):
    r = mk_post(client, admin, draft, name="引导", quota=4,
                periods=_periods((9, 9, 3), (9, 14, 2)), lock_hours=6,
                group_name="引导组")
    assert r.status_code == 200, r.text
    out = r.json()
    assert set(out) == {"id", "shift_id", "warning"} and out["warning"] == ""

    g = db.get(Shift, out["shift_id"])
    s = db.get(Position, out["id"])
    assert (g.kind, g.mode) == ("service", "pick")   # 自带时段的普通岗位
    assert g.name == s.name == "引导"                # 场次名跟着岗位名
    assert [p.shift_id for p in g.positions] == [g.id] and len(g.positions) == 1
    assert [(p.start_time.hour, p.end_time.hour) for p in g.sorted_periods()] \
        == [(9, 12), (14, 16)]
    assert (s.quota, s.location, g.lock_hours) == (4, "北门", 6)


def test_读接口把时段跟状态挂在每个岗位上(client, admin, db, draft):
    """管理端「按岗位」平铺：shifts.flatMap(g => g.positions)，每项自带时间。"""
    r = mk_post(client, admin, draft, name="引导", quota=2,
                periods=_periods((8, 9, 3)))
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{draft}", headers=admin).json()
    p = d["shifts"][0]["positions"][0]
    assert p["shift_id"] == r.json()["shift_id"]
    g = p["shift"]
    assert g["id"] == p["shift_id"] and len(g["periods"]) == 1
    assert g["time_text"] == p["time_text"] == d["shifts"][0]["time_text"]
    assert g["needs_assign"] is False
    assert (g["enroll_paused"], g["list_locked"]) == (False, False)
    assert g["hours_label"] and g["lock_at"]
    # with_times 填进来的那几项：此刻锁没锁、还收不收人
    for key in ("locked", "enroll_open", "started", "closes_in_ms",
                "enroll_deadline"):
        assert key in g, key
    assert g["enroll_open"] is True and g["locked"] is False


def test_改岗位一次把时段名额名字都改掉(client, admin, db, draft):
    r = mk_post(client, admin, draft, name="引导", quota=2,
                periods=_periods((9, 9, 3)))
    pid, gid = r.json()["id"], r.json()["shift_id"]
    r = client.patch(f"/api/staff/posts/{pid}", headers=admin,
                     json={"name": "安检", "quota": 6,
                           "periods": _periods((9, 13, 4)),
                           "hours_mode": "fixed", "note": "带工牌"})
    assert r.status_code == 200, r.text
    labels = {c["label"] for c in r.json()["changes"]}
    assert {"岗位名", "名额", "时间", "时长认定"} <= labels, labels

    db.expire_all()
    g, s = db.get(Shift, gid), db.get(Position, pid)
    assert g.name == s.name == "安检"        # 场次名跟着改
    assert (s.quota, g.hours_mode, g.note) == (6, "fixed", "带工牌")
    assert [(p.start_time.hour, p.end_time.hour) for p in g.sorted_periods()] \
        == [(13, 17)]
    assert db.query(ShiftPeriod).filter(ShiftPeriod.shift_id == gid).count() == 1
    # 一次改动一条留痕，场次级和岗位级的改动写在同一条上
    log = (db.query(AuditLog).filter(AuditLog.target_id == pid,
                                     AuditLog.action == "update").one())
    assert log.target_type == "position" and "修改岗位「安检」" in log.summary
    assert "时间" in log.summary and "名额" in log.summary


def test_岗位名不能改成空(client, admin, draft):
    pid = mk_post(client, admin, draft, name="引导").json()["id"]
    r = client.patch(f"/api/staff/posts/{pid}", headers=admin, json={"name": " "})
    assert r.status_code == 400 and "岗位名称不能为空" in r.json()["detail"]


def test_删岗位连它那一场一起删(client, admin, db, draft, take_student):
    pid = mk_post(client, admin, draft, name="引导", quota=2).json()["id"]
    gid = db.get(Position, pid).shift_id
    r = client.delete(f"/api/staff/posts/{pid}", headers=admin)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Shift, gid) is None and db.get(Position, pid) is None


def test_有人报名的岗位要确认才删_报名一起取消(client, admin, db, take_student,
                                                open_group):
    act, g = open_group(3)
    pid = g.test_position_id
    sid = take_student()
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, sid), json={"position_id": pid})
    assert r.status_code == 200, r.text
    with editing(client, admin, act.id):
        r = client.delete(f"/api/staff/posts/{pid}", headers=admin)
        assert r.status_code == 409 and "1 人" in r.json()["detail"], r.text
        r = client.delete(f"/api/staff/posts/{pid}?force=1", headers=admin)
        assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Shift, g.id) is None
    e = db.query(Enrollment).filter(Enrollment.user_id ==
                                    student_row(db, sid).id).one()
    assert e.status == "cancelled"
    log = (db.query(AuditLog).filter(AuditLog.target_id == pid,
                                     AuditLog.action == "delete").one())
    assert "删除岗位「测试岗位」（含 1 条报名记录）" == log.summary


def test_复制岗位到另一个时段(client, admin, db, draft):
    r = mk_post(client, admin, draft, name="引导", quota=4,
                periods=_periods((9, 9, 3)), group_name="引导组",
                position_requirement="能久站", needs_leader=True)
    pid, gid = r.json()["id"], r.json()["shift_id"]
    db.add(Attachment(id=uid("att"), owner_type="position", owner_id=pid,
                      activity_id=draft, kind="file", title="培训材料.pdf",
                      stored_name="x.pdf", size=10))
    db.commit()

    r = client.post(f"/api/staff/posts/{pid}/copy", headers=admin,
                    json={"periods": _periods((10, 14, 3)), "name": "引导（下午）"})
    assert r.status_code == 200, r.text
    new_pid, new_gid = r.json()["id"], r.json()["shift_id"]
    assert new_gid != gid
    db.expire_all()
    new_g, new_s = db.get(Shift, new_gid), db.get(Position, new_pid)
    assert new_g.name == new_s.name == "引导（下午）"
    assert [(p.start_time.day, p.start_time.hour)
            for p in new_g.sorted_periods()] == \
        [((now() + timedelta(days=10)).day, 14)]
    assert (new_s.quota, new_s.group_name, new_s.position_requirement,
            new_s.needs_leader) == (4, "引导组", "能久站", True)
    assert db.query(Attachment).filter(
        Attachment.owner_type == "position",
        Attachment.owner_id == new_pid).count() == 1
    # 同组两个岗位时段不同：保存照常，只回一句提醒
    assert "时间不完全相同" in r.json()["warning"], r.json()["warning"]
    # 复制不带报名
    assert db.query(Enrollment).filter(Enrollment.shift_id == new_gid).count() == 0


def test_复制不给时段就沿用原来的(client, admin, db, draft):
    r = mk_post(client, admin, draft, name="引导",
                periods=_periods((9, 9, 3), (9, 14, 2)))
    r2 = client.post(f"/api/staff/posts/{r.json()['id']}/copy", headers=admin,
                     json={})
    assert r2.status_code == 200, r2.text
    old, new = (db.get(Shift, r.json()["shift_id"]),
                db.get(Shift, r2.json()["shift_id"]))
    assert new.name == old.name == "引导"
    assert [(p.start_time, p.end_time) for p in new.sorted_periods()] == \
        [(p.start_time, p.end_time) for p in old.sorted_periods()]


def test_岗位的暂停与锁定落在它那一场上(client, admin, db, open_group,
                                        take_student):
    act, g = open_group(3)
    pid = g.test_position_id
    r = client.post(f"/api/staff/posts/{pid}/pause", headers=admin,
                    json={"paused": True})
    assert r.status_code == 200 and r.json()["paused"] is True, r.text
    db.expire_all()
    assert db.get(Shift, g.id).enroll_paused is True
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()),
                    json={"position_id": pid})
    assert r.status_code == 409 and "暂停报名" in r.json()["detail"]

    assert client.post(f"/api/staff/posts/{pid}/pause", headers=admin,
                       json={"paused": False}).status_code == 200
    r = client.post(f"/api/staff/posts/{pid}/lock", headers=admin,
                    json={"locked": True})
    assert r.status_code == 200 and r.json()["locked"] is True, r.text
    db.expire_all()
    assert db.get(Shift, g.id).list_locked is True
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()),
                    json={"position_id": pid})
    assert r.status_code == 409 and "锁定" in r.json()["detail"]


def test_统一分配的岗位不走岗位接口(client, admin, db, open_group):
    """「待分配」和它旁边的真实岗位都挂在同一场上：那一场由场次接口管。"""
    act, g = open_group(3, mode="assign")
    holder = g.placeholder()
    with editing(client, admin, act.id):
        for path in (f"/api/staff/posts/{holder.id}/pause",
                     f"/api/staff/posts/{holder.id}/lock"):
            r = client.post(path, headers=admin, json={"paused": True,
                                                       "locked": True})
            assert r.status_code == 400, (path, r.text)
            assert "/api/staff/positions" in r.json()["detail"], r.text
        r = client.patch(f"/api/staff/posts/{holder.id}", headers=admin,
                         json={"quota": 5})
        assert r.status_code == 400, r.text
        assert "/api/staff/positions" in r.json()["detail"], r.text
        r = client.delete(f"/api/staff/posts/{holder.id}", headers=admin)
        assert r.status_code == 400, r.text
        assert "/api/staff/positions" in r.json()["detail"], r.text


def test_发布后改岗位要先进编辑态(client, admin, open_group):
    act, g = open_group(3)
    r = client.patch(f"/api/staff/posts/{g.test_position_id}", headers=admin,
                     json={"quota": 5})
    assert r.status_code == 409 and "调整岗位" in r.json()["detail"]
    r = mk_post(client, admin, act.id, name="又一个岗位")
    assert r.status_code == 409 and "调整岗位" in r.json()["detail"]


def test_补记录活动不设岗位(client, admin, db, draft):
    from backend import config
    from backend.models import Activity
    act = db.get(Activity, draft)
    act.recruit_mode = config.RECORD_MODE
    db.commit()
    r = mk_post(client, admin, draft, name="引导")
    assert r.status_code == 409 and "补记录活动" in r.json()["detail"], r.text


def test_一个岗位都没有时不能一键暂停或锁定(client, admin, draft):
    for path, body in (("pause-all", {"paused": True}),
                       ("lock-all", {"locked": True})):
        r = client.post(f"/api/staff/activities/{draft}/{path}", headers=admin,
                        json=body)
        assert r.status_code == 409, (path, r.text)
        assert "还没有岗位" in r.json()["detail"], r.text
