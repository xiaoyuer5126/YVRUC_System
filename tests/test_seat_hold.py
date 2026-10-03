"""临期退选的名额：只在报名已截止时扣留，扣留可由管理者释放。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Activity, Enrollment, User, now
from conftest import login


def _one_seat(client, db, open_group, take_student, *, reg_end_in_hours):
    """名额 1 的场次，一人占满、一人候补；报名截止时间可控。

    返回 (活动, 场次, 占位人学号, 占位人的报名 id, 候补的报名 id)。
    """
    act, g = open_group(1, day_offset=0, start_in_hours=10)
    taker = take_student()
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, taker),
                    json={"position_id": g.test_position_id})
    assert r.json()["status"] == "enrolled"
    q = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, take_student()),
                    json={"position_id": g.test_position_id})
    assert q.json()["status"] == "waitlisted"
    db.get(Activity, act.id).reg_end = now() + timedelta(hours=reg_end_in_hours)
    db.commit()
    return act, g, taker, r.json()["enrollment_id"], q.json()["enrollment_id"]


def _cancel(client, sid, enr_id):
    return client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=login(client, sid))


def _roster(client, admin, g):
    return client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()


def _public_shift(client, act, sid):
    """学生在活动详情里看到的那一场：剩余名额是按这里显示的。"""
    return client.get(f"/api/activities/{act.id}",
                      headers=login(client, sid)).json()["shifts"][0]


def test_报名未截止时临期退选不扣留(client, admin, db, open_group, take_student):
    """平台还在招人，把位子废掉自相矛盾。

    场次 10 小时后就开始（已在「开始前 24 小时」之内），但报名还开着，
    候补递补照样继续——自动转正截止点取两者中较晚的那个。
    """
    act, g, taker, enr_id, wl_id = _one_seat(client, db, open_group, take_student,
                                             reg_end_in_hours=5)
    r = _cancel(client, taker, enr_id)
    assert r.status_code == 200
    body = r.json()
    assert body["effect"] == "released"            # 名额照常释放
    assert "已释放" in body["message"]
    assert _roster(client, admin, g)["held"] == []
    # 名额放回去了，候补该递补
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "pending_confirm"


def test_报名已截止时临期退选扣留名额(client, admin, db, open_group, take_student):
    act, g, taker, enr_id, wl_id = _one_seat(client, db, open_group, take_student,
                                             reg_end_in_hours=-1)
    r = _cancel(client, taker, enr_id)
    assert r.json()["effect"] == "held"
    assert "扣留" in r.json()["message"]

    # 位子仍被占着：确认人数掉到 0，但剩余名额还是 0
    shift = _public_shift(client, act, taker)
    assert (shift["enrolled"], shift["remaining"], shift["held_seats"]) == (0, 0, 1)
    # 扣留的位子不放给候补
    roster = _roster(client, admin, g)
    assert [x["enrollment_id"] for x in roster["held"]] == [enr_id]
    assert [x["enrollment_id"] for x in roster["waitlist"]] == [wl_id]


def test_管理者可以释放扣留的名额(client, admin, db, open_group, take_student):
    act, g, taker, enr_id, wl_id = _one_seat(client, db, open_group, take_student,
                                             reg_end_in_hours=-1)
    assert _cancel(client, taker, enr_id).json()["effect"] == "held"

    roster = _roster(client, admin, g)
    assert [x["enrollment_id"] for x in roster["held"]] == [enr_id], \
        "扣留的记录要单独列出来，否则老师找不到"
    quota_before = [p["quota"] for p in roster["positions"]]

    r = client.post(f"/api/staff/enrollments/{enr_id}/release-seat", headers=admin)
    assert r.status_code == 200
    roster = _roster(client, admin, g)
    assert roster["held"] == []
    assert _public_shift(client, act, taker)["remaining"] == 1
    # 扣留从来不动老师填的数字，释放同样不动
    assert [p["quota"] for p in roster["positions"]] == quota_before

    # 已过自动转正截止点：位子放出来了但不会自动被占，接口要说清楚还差一步，
    # 否则老师以为补完了，位子一直空着
    assert r.json()["auto_promote_over"] is True
    assert r.json()["promoted"] == 0
    assert "手动" in r.json()["message"]
    assert [x["enrollment_id"] for x in roster["waitlist"]] == [wl_id]
    assert client.post(f"/api/staff/enrollments/{wl_id}/promote",
                       headers=admin).status_code == 200


def test_没扣留的记录不能释放(client, admin, db, open_group, take_student):
    act, g, taker, enr_id, _wl = _one_seat(client, db, open_group, take_student,
                                           reg_end_in_hours=5)
    r = client.post(f"/api/staff/enrollments/{enr_id}/release-seat", headers=admin)
    assert r.status_code == 409 and "没有扣留" in r.json()["detail"]


def test_提前截止报名后临期退选照样扣留(client, admin, db, open_group, take_student):
    """管理者点了「截止报名」，reg_end 还在未来，但招人确实已经停了。"""
    act, g, taker, enr_id, _wl = _one_seat(client, db, open_group, take_student,
                                           reg_end_in_hours=5)
    db.get(Activity, act.id).status = "closed"
    db.commit()
    assert _cancel(client, taker, enr_id).json()["effect"] == "held"
    assert [x["enrollment_id"] for x in _roster(client, admin, g)["held"]] == [enr_id]


def test_扣留的名额分配时不能占用(client, admin, db, open_group, take_student):
    """扣留的位子不释放给任何人，手动调岗同样不能拿它排人
    （一键分配那边见 test_placeholder 的「扣留的位子一键分配不用」）。"""
    from backend.models import Position, uid
    act, g = open_group(2, day_offset=0, start_in_hours=10)
    spare = Position(id=uid("p"), shift_id=g.id, name="备用岗位", quota=1, sort=1)
    db.add(spare)
    db.commit()
    stay, quitter, extra = take_student(), take_student(), take_student()
    for sid in (stay, quitter):
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=login(client, sid),
                        json={"position_id": g.test_position_id})
        assert r.json()["status"] == "enrolled"
    # 报满之后才关报名：扣留只发生在招人已经停了的时候
    db.get(Activity, act.id).reg_end = now() - timedelta(hours=1)
    db.commit()
    gone = (db.query(Enrollment)
            .join(User, Enrollment.user_id == User.id)
            .filter(Enrollment.shift_id == g.id,
                    User.student_id == quitter).one())
    assert _cancel(client, quitter, gone.id).json()["effect"] == "held"

    # 手动调岗：把第三个人塞进这个岗位要被拦下，除非勾强制
    client.post(f"/api/staff/shifts/{g.id}/add-walkin", headers=admin,
                json={"student_id": extra, "position_id": spare.id})
    third = (db.query(Enrollment)
             .join(User, Enrollment.user_id == User.id)
             .filter(Enrollment.shift_id == g.id,
                     User.student_id == extra).one())
    plan = [{"enrollment_id": third.id, "position_id": g.test_position_id}]
    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": plan, "source": "manual"})
    assert r.status_code == 409 and "超出名额" in r.json()["detail"]
    plan[0]["force"] = True
    assert client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                       json={"plan": plan, "source": "manual"}).status_code == 200
