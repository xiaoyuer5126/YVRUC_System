"""编辑冻结：管理者调整安排期间暂停报名与退选。"""
from __future__ import annotations

from datetime import timedelta

from backend import config, scheduled
from backend.models import Activity, Enrollment, Notification, now
from conftest import login


def test_冻结期间报不了名也退不了选(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, u = enrolled_activity
    pos = shift.positions[0]

    r = client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    assert r.status_code == 200 and r.json()["editing"] is True

    t = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                    headers=t, json={"position_id": pos.id})
    assert r.status_code == 409 and "暂停" in r.json()["detail"]

    enr = db.query(Enrollment).filter(Enrollment.user_id == u.id).first()
    mine = login(client, u.student_id)
    r = client.delete(f"/api/activities/enrollments/{enr.id}", headers=mine)
    assert r.status_code == 409

    # 学生看到的活动详情要能看出正在调整
    detail = client.get(f"/api/activities/{act.id}", headers=mine).json()
    assert detail["editing"] is True


def test_保存并发布后恢复报名并汇总改动(client, admin, db, enrolled_activity,
                                      take_student):
    act, shift, _u = enrolled_activity
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                 json={"name": "上午场（改到北门集合）"})

    r = client.post(f"/api/staff/activities/{act.id}/publish-changes",
                    headers=admin)
    assert r.status_code == 200
    assert r.json()["editing"] is False and r.json()["changes"] >= 1

    t = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                    headers=t, json={"position_id": shift.positions[0].id})
    assert r.status_code == 200


def test_两个管理者不能同时进入编辑态(client, admin, db, enrolled_activity):
    act, _shift, _u = enrolled_activity
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 409


def test_超时自动解冻并通知管理者(client, admin, db, enrolled_activity):
    act, _shift, _u = enrolled_activity
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    db.expire_all()          # 接口在别的会话里写的，本会话要先失效缓存
    row = db.get(Activity, act.id)
    row.editing_since = now() - timedelta(hours=config.EDIT_LOCK_HOURS + 1)
    editor = row.editing_by
    db.commit()

    assert scheduled.release_stale_edit_locks(db) == 1
    db.expire_all()
    assert db.get(Activity, act.id).editing_since is None
    assert (db.query(Notification)
            .filter(Notification.user_id == editor,
                    Notification.title.like("报名已自动恢复%")).count() == 1)


def test_未冻结时不能保存并发布(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes",
                    headers=admin)
    assert r.status_code == 409


# ======================= 统一分配场次的建岗位约束 =======================

def test_统一分配的场次必须先切模式才能加岗位(client, admin, db,
                                          enrolled_activity):
    act, shift, _u = enrolled_activity
    shift.mode = "assign"
    shift.capacity = 10
    db.commit()

    r = client.post(f"/api/staff/shifts/{shift.id}/positions", headers=admin,
                    json={"name": "整理A", "quota": 3})
    assert r.status_code == 409
    assert "报名即选岗位" in r.json()["detail"]

    assert client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                        json={"mode": "pick"}).status_code == 200
    assert client.post(f"/api/staff/shifts/{shift.id}/positions", headers=admin,
                       json={"name": "整理A", "quota": 3}).status_code == 200


def test_余几人待分配的统计(client, admin, db, enrolled_activity, take_student):
    """已确认人数 − 岗位名额之和 = 落空的人，管理者要当场看得见。"""
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    pos.quota = 1
    db.commit()
    # 再塞一个人进来但不给岗位（模拟统一分配后还没分完）
    from backend.models import User, uid
    other = db.query(User).filter(
        User.student_id == take_student()).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=shift.id,
                      user_id=other.id, status="confirmed", joined_at=now()))
    db.commit()

    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    sh = d["shifts"][0]
    assert sh["unassigned_count"] == 1
    assert sh["position_quota_sum"] == 1
