"""名单锁定与暂停报名下的候补递补。

锁定：开场前 N 小时自动锁定，或管理者手动锁定场次 / 岗位。锁定后不收新报名、
新候补，候补不自动顶上（管理者手动转正），此时退出记一次「锁定后退出」。
暂停：调整岗位用，不收新报名、新候补，候补也不顶上；退出不记录。
暂停和锁定都解除时，按候补顺序补齐空位。
"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Enrollment, Position, Shift, now, uid
from conftest import mk_shift
from conftest import POSITION_TEXT, inbox, login


def _enroll(client, act, g, sid, pid=None):
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                       headers=login(client, sid),
                       json={"position_id": pid or g.test_position_id})


def _cancel(client, sid, enr_id):
    return client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=login(client, sid))


def _lock(client, admin, kind, target_id, locked=True):
    r = client.post(f"/api/staff/{kind}/{target_id}/lock", headers=admin,
                    json={"locked": locked})
    assert r.status_code == 200, r.text
    return r.json()


def _pause(client, admin, kind, target_id, paused=True):
    r = client.post(f"/api/staff/{kind}/{target_id}/pause", headers=admin,
                    json={"paused": paused})
    assert r.status_code == 200, r.text
    return r.json()


def _status(db, enr_id):
    db.expire_all()
    return db.get(Enrollment, enr_id).status


def _fill_and_queue(client, db, open_group, take_student, *, lock=False, **kw):
    """名额 1 的场次：一个人占满，一个人排队。返回 (活动, 场次, 候补学号, 候补的报名 id)。

    lock=True 时报满之后再锁定这一场：候补不会自动顶上，测手动转正用。
    """
    act, g = open_group(1, **kw)
    assert _enroll(client, act, g, take_student()).json()["status"] == "enrolled"
    queued = take_student()
    r = _enroll(client, act, g, queued)
    assert r.json()["status"] == "waitlisted"
    if lock:
        db.get(Shift, g.id).list_locked = True
        db.commit()
    return act, g, queued, r.json()["enrollment_id"]


def _raise_quota(client, admin, act, g, quota):
    """管理者加名额：进编辑态、改岗位、保存并发布。返回发布接口的结果。"""
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    r = client.patch(f"/api/staff/positions/{g.test_position_id}", headers=admin,
                     json={"quota": quota})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _roster(client, admin, g):
    return client.get(f"/api/staff/shifts/{g.id}/roster", headers=admin).json()


def _shift_view(client, act, sid):
    return client.get(f"/api/activities/{act.id}",
                      headers=login(client, sid)).json()["shifts"][0]


# ======================= 自动锁定 =======================

def test_开场前N小时自动锁定_之前照常报名(client, db, open_group, take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=10, lock_hours=12)
    sid = take_student()
    r = _enroll(client, act, g, sid)
    assert r.status_code == 409 and "名单已锁定" in r.json()["detail"]
    assert _shift_view(client, act, sid)["locked"] is True

    act2, g2 = open_group(3, day_offset=0, start_in_hours=20, lock_hours=12)
    assert _enroll(client, act2, g2, take_student()).status_code == 200
    v = _shift_view(client, act2, take_student())
    assert v["locked"] is False and v["lock_hours"] == 12


def test_N为0不自动锁定(client, db, open_group, take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=1, lock_hours=0)
    assert _enroll(client, act, g, take_student()).status_code == 200


def test_过了锁定点手动解锁后不会被自动锁回去(client, admin, db, open_group, take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=10, lock_hours=12)
    assert _lock(client, admin, "shifts", g.id, False)["locked"] is False
    assert _enroll(client, act, g, take_student()).status_code == 200
    # 只改了场次名：不碰锁定状态
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin, json={"name": "改个名"})
    assert r.status_code == 200, r.text
    client.post(f"/api/staff/activities/{act.id}/publish-changes", headers=admin)
    assert _enroll(client, act, g, take_student()).status_code == 200


def test_改N后锁定点已过的立即锁定_已锁的改小不自动解锁(client, admin, db, open_group,
                                                     take_student):
    act, g = open_group(3, day_offset=0, start_in_hours=20, lock_hours=12)
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin, json={"lock_hours": 24})
    assert r.status_code == 200, r.text
    db.expire_all()
    from backend.enroll_logic import shift_locked
    assert shift_locked(db.get(Shift, g.id))

    # 已经锁上的，把 N 改小（锁定点挪到将来）也不会自己解开
    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin, json={"lock_hours": 2})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert shift_locked(db.get(Shift, g.id))

    r = client.patch(f"/api/staff/shifts/{g.id}", headers=admin, json={"lock_hours": 73})
    assert r.status_code == 400


def test_新建场次带N_超出范围拒绝(client, admin, db, open_group):
    act, g = open_group(3)
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    body = {"name": "晚场",
            "periods": [{"start_time": (g.start_time + timedelta(days=1)).isoformat(),
            "end_time": (g.end_time + timedelta(days=1)).isoformat()}], "lock_hours": 30}
    r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin, json=body)
    assert r.status_code == 200, r.text
    assert db.get(Shift, r.json()["id"]).lock_hours == 30
    r = client.post(f"/api/staff/activities/{act.id}/shifts", headers=admin,
                    json={**body, "lock_hours": 80})
    assert r.status_code == 422


# ======================= 锁定后退出 =======================

def test_锁定后退出被记录_候补不顶上_通知管理者(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    _lock(client, admin, "shifts", g.id)

    r = _cancel(client, taker, enr_id)
    assert r.status_code == 200 and r.json()["effect"] == "locked"
    db.expire_all()
    assert db.get(Enrollment, enr_id).cancel_type == "locked"
    assert _status(db, wl_id) == "waitlisted"
    assert "名单已锁定" in _roster(client, admin, g)["auto_promote_stop"]
    assert len(inbox(db, "2025001", f"[锁定后减员] {act.title}")) == 1


def test_没锁定时退出不记录_候补照常顶上(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    r = _cancel(client, taker, enr_id)
    assert r.json()["effect"] == "released"
    db.expire_all()
    assert db.get(Enrollment, enr_id).cancel_type == "normal"
    assert _status(db, wl_id) == "pending_confirm"
    assert inbox(db, "2025001", f"[锁定后减员] {act.title}") == []


def test_锁定后放弃候补转正也通知管理者(client, admin, db, open_group, take_student):
    """待确认的人放弃转正，位子同样空出来、递补同样停着：和退选一个口径。"""
    act, g = open_group(1, day_offset=0, start_in_hours=40)
    taker, queued = take_student(), take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, queued).json()["enrollment_id"]
    # 还没锁定：正式那位退选，候补照常顶上待确认，这一步不该有减员通知
    assert _cancel(client, taker, enr_id).json()["effect"] == "released"
    db.expire_all()
    assert _status(db, wl_id) == "pending_confirm"
    assert inbox(db, "2025001", f"[锁定后减员] {act.title}") == []

    _lock(client, admin, "shifts", g.id)
    r = _cancel(client, queued, wl_id)
    assert r.status_code == 200 and r.json()["effect"] == "abandoned", r.text
    msgs = inbox(db, "2025001", f"[锁定后减员] {act.title}")
    assert len(msgs) == 1, msgs
    assert "放弃了" in msgs[0].content and "停止候补自动转正" in msgs[0].content


def test_锁定后转正超时也通知管理者_日报也计(client, admin, db, open_group, take_student,
                                        monkeypatch):
    """超时未确认同样空出一个位子；这条顺带补上留痕，日报里也数得出来。"""
    from backend import config, enroll_logic, scheduled
    from backend.models import Notification

    act, g = open_group(1, day_offset=0, start_in_hours=40)
    taker, queued = take_student(), take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, queued).json()["enrollment_id"]
    assert _cancel(client, taker, enr_id).json()["effect"] == "released"
    _lock(client, admin, "shifts", g.id)

    # 把确认窗拨到过去，再跑一次超时扫描
    db.get(Enrollment, wl_id).confirm_deadline = now() - timedelta(minutes=1)
    db.commit()
    assert enroll_logic.expire_stale_promotions(db) == 1
    db.expire_all()
    assert db.get(Enrollment, wl_id).cancel_type == "expired"
    msgs = inbox(db, "2025001", f"[锁定后减员] {act.title}")
    assert len(msgs) == 1 and "确认超时" in msgs[0].content, msgs

    # 日报也数这一条
    monkeypatch.setattr(config, "ROSTER_DIGEST_HOUR", 0)
    scheduled.send_roster_digests(db)
    notes = (db.query(Notification)
             .filter(Notification.title == f"[人员变动] {act.title} · {g.name}").all())
    assert notes and "候补转正超时未确认：1 人。" in notes[-1].content, notes


def test_打开我的报名只收口本人的超时转正(client, admin, db, open_group,
                                take_student):
    """「我的报名」是只读页面，打开时顶多替本人收口一次超时的确认窗。

    不带 user_id 去扫全站，抢报高峰时这条只读请求会去抢别人的场次锁，轻则
    白等 3 秒，重则直接撞上「当前操作人数较多」503——学生什么都没做就看到报错。
    """
    # 别人的一场：别的同学候补顶上后超时未确认
    other_act, og = open_group(1)
    taker = take_student()
    t_enr = _enroll(client, other_act, og, taker).json()["enrollment_id"]
    other = take_student()
    other_wl = _enroll(client, other_act, og, other).json()["enrollment_id"]
    assert _cancel(client, taker, t_enr).json()["effect"] == "released"
    db.get(Enrollment, other_wl).confirm_deadline = now() - timedelta(minutes=1)

    # 本人自己的一场：同样超时未确认
    me = take_student()
    my_act, mg = open_group(1)
    my_taker = take_student()
    mt_enr = _enroll(client, my_act, mg, my_taker).json()["enrollment_id"]
    my_wl = _enroll(client, my_act, mg, me).json()["enrollment_id"]
    assert _cancel(client, my_taker, mt_enr).json()["effect"] == "released"
    db.get(Enrollment, my_wl).confirm_deadline = now() - timedelta(minutes=1)
    db.commit()
    assert db.get(Enrollment, my_wl).status == "pending_confirm"
    assert db.get(Enrollment, other_wl).status == "pending_confirm"

    r = client.get("/api/me/enrollments", headers=login(client, me))
    assert r.status_code == 200, r.text
    db.expire_all()
    # 本人的超时当场收口，页面上看到的状态和实际一致
    assert db.get(Enrollment, my_wl).status == "cancelled"
    assert db.get(Enrollment, my_wl).cancel_type == "expired"
    # 别人那条不动：只读页面不替全站收口，等后台定时任务处理
    assert db.get(Enrollment, other_wl).status == "pending_confirm"


def test_报名截止后锁定前候补照常递补(client, admin, db, open_group, take_student):
    from backend.models import Activity
    act, g = open_group(1)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    db.get(Activity, act.id).reg_end = now() - timedelta(hours=1)
    db.commit()
    assert _cancel(client, taker, enr_id).json()["effect"] == "released"
    assert _status(db, wl_id) == "pending_confirm"


def test_解锁时按候补顺序补齐_重新收报名(client, admin, db, open_group, take_student):
    act, g = open_group(2)
    a, b = take_student(), take_student()
    ea = _enroll(client, act, g, a).json()["enrollment_id"]
    eb = _enroll(client, act, g, b).json()["enrollment_id"]
    w1 = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    w2 = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    _lock(client, admin, "shifts", g.id)
    assert _enroll(client, act, g, take_student()).status_code == 409
    _cancel(client, a, ea)
    _cancel(client, b, eb)
    assert _status(db, w1) == "waitlisted" and _status(db, w2) == "waitlisted"

    d = _lock(client, admin, "shifts", g.id, False)
    assert d["promoted"] == 2 and d["hint"] == ""
    assert _status(db, w1) == "pending_confirm" and _status(db, w2) == "pending_confirm"
    # 位子都给了候补，新来的人只能排候补
    assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"


def test_锁定期间确认窗里的人照常确认(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    queued = take_student()
    wl_id = _enroll(client, act, g, queued).json()["enrollment_id"]
    _cancel(client, taker, enr_id)
    assert _status(db, wl_id) == "pending_confirm"
    _lock(client, admin, "shifts", g.id)
    r = client.post(f"/api/activities/enrollments/{wl_id}/confirm-promotion",
                    headers=login(client, queued))
    assert r.status_code == 200, r.text
    assert _status(db, wl_id) == "enrolled"


# ======================= 岗位锁定 =======================

def test_锁定岗位只管这个岗位(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    other = Position(id=uid("p"), shift_id=g.id, name="检录", quota=1,
                     waitlist_quota=3, sort=1, **POSITION_TEXT)
    db.add(other)
    db.commit()
    pid = g.test_position_id
    a, b = take_student(), take_student()
    ea = _enroll(client, act, g, a, pid).json()["enrollment_id"]
    eb = _enroll(client, act, g, b, other.id).json()["enrollment_id"]
    wa = _enroll(client, act, g, take_student(), pid).json()["enrollment_id"]
    wb = _enroll(client, act, g, take_student(), other.id).json()["enrollment_id"]
    _lock(client, admin, "positions", pid)

    r = _enroll(client, act, g, take_student(), pid)
    assert r.status_code == 409 and "岗位「测试岗位」名单已锁定" in r.json()["detail"]
    assert _cancel(client, a, ea).json()["effect"] == "locked"
    assert _cancel(client, b, eb).json()["effect"] == "released"
    assert _status(db, wa) == "waitlisted"
    assert _status(db, wb) == "pending_confirm"

    d = _lock(client, admin, "positions", pid, False)
    assert d["promoted"] == 1
    assert _status(db, wa) == "pending_confirm"


# ======================= 暂停 =======================

def test_暂停期间候补不顶上_恢复时补齐(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    pid = g.test_position_id
    a = take_student()
    first = _enroll(client, act, g, a, pid).json()["enrollment_id"]
    queued = _enroll(client, act, g, take_student(), pid).json()["enrollment_id"]
    _pause(client, admin, "positions", pid)
    r = _cancel(client, a, first)
    assert r.json()["effect"] == "released"          # 暂停期间退出不记录
    db.expire_all()
    assert db.get(Enrollment, first).cancel_type == "normal"
    assert _status(db, queued) == "waitlisted"
    assert "暂停" in _roster(client, admin, g)["auto_promote_stop"]

    d = _pause(client, admin, "positions", pid, False)
    assert d["promoted"] == 1
    assert _status(db, queued) == "pending_confirm"


def test_既暂停又锁定_只解除其一不补并提醒(client, admin, db, open_group, take_student):
    act, g = open_group(1)
    a = take_student()
    first = _enroll(client, act, g, a).json()["enrollment_id"]
    queued = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    _pause(client, admin, "shifts", g.id)
    _lock(client, admin, "shifts", g.id)
    _cancel(client, a, first)

    d = _pause(client, admin, "shifts", g.id, False)
    assert d["promoted"] == 0 and "仍在锁定中" in d["hint"]
    assert _status(db, queued) == "waitlisted"

    _pause(client, admin, "shifts", g.id)
    d = _lock(client, admin, "shifts", g.id, False)
    assert d["promoted"] == 0 and "仍在暂停中" in d["hint"]
    assert _status(db, queued) == "waitlisted"

    d = _pause(client, admin, "shifts", g.id, False)
    assert d["promoted"] == 1 and d["hint"] == ""
    assert _status(db, queued) == "pending_confirm"


def test_待分配与培训不单独锁定_学生不能锁(client, admin, db, open_group, take_student):
    act, g = open_group(3, mode="assign")
    r = client.post(f"/api/staff/positions/{g.test_position_id}/lock", headers=admin,
                    json={"locked": True})
    assert r.status_code == 400 and "整个场次" in r.json()["detail"]
    att = mk_shift(id=uid("g"), activity_id=act.id, name="彩排", kind="rehearsal",
                start_time=g.start_time, end_time=g.end_time, sort=1)
    db.add(att)
    db.commit()
    r = client.post(f"/api/staff/shifts/{att.id}/lock", headers=admin, json={"locked": True})
    assert r.status_code == 400
    r = client.post(f"/api/staff/shifts/{g.id}/lock", headers=login(client, take_student()),
                    json={"locked": True})
    assert r.status_code == 403


def test_截止后分配的真实岗位不单独暂停锁定(client, admin, db, open_group):
    """assign 场次的候补是整场一条队，单锁一个真实岗位，空位照样被候补顶上。"""
    act, g = open_group(3, mode="assign")
    real = Position(id=uid("p"), shift_id=g.id, name="检录", quota=2, sort=0,
                    **POSITION_TEXT)
    db.add(real)
    db.commit()
    for kind, body in (("lock", {"locked": True}), ("pause", {"paused": True})):
        r = client.post(f"/api/staff/positions/{real.id}/{kind}", headers=admin,
                        json=body)
        assert r.status_code == 400 and "整个场次" in r.json()["detail"]


def test_排锁期间被暂停_锁里再判一次(client, db, open_group, take_student, monkeypatch):
    """锁外判过「还收报名」，排到锁里时负责人刚点了暂停：照样要拦下。"""
    from backend import enroll_logic as logic
    from backend.database import SessionLocal

    act, g = open_group(3)
    real = logic.closed_reason
    calls = []

    def pause_right_after(shift, position):
        if calls:
            return real(shift, position)
        calls.append(1)
        # 锁外这一关刚判完，另一个会话把这一场暂停了：请求手里的对象还是旧的
        s = SessionLocal()
        try:
            s.get(Shift, shift.id).enroll_paused = True
            s.commit()
        finally:
            s.close()
        return ""

    monkeypatch.setattr(logic, "closed_reason", pause_right_after)
    r = _enroll(client, act, g, take_student())
    assert r.status_code == 409 and "暂停报名" in r.json()["detail"]
    db.expire_all()
    assert db.query(Enrollment).filter(Enrollment.shift_id == g.id).count() == 0


def test_发布时补位排不上锁_汇总照发并提示(client, admin, db, open_group, take_student,
                                  monkeypatch):
    """恢复报名正是抢报的时候：补位拿不到场次锁就跳过那一场，不让请求报错——
    编辑态已经解除，报错的话这次的变更汇总就再没人发了。"""
    from backend.locks import _busy
    from backend.models import Notification, User
    from backend.routers import staff_activities

    act, g = open_group(1)
    first = take_student()
    assert _enroll(client, act, g, first).json()["status"] == "enrolled"
    assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    assert client.patch(f"/api/staff/positions/{g.test_position_id}", headers=admin,
                        json={"quota": 2}).status_code == 200

    def busy(*_a, **_k):
        raise _busy()

    monkeypatch.setattr(staff_activities, "shift_lock", busy)
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes", headers=admin)
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == 0 and r.json()["busy"] == [g.name]
    db.expire_all()
    u = db.query(User).filter(User.student_id == first).one()
    assert db.query(Notification).filter(
        Notification.user_id == u.id,
        Notification.title == f"活动变更提醒｜{act.title}").count() == 1
    # 再点一次：已经不在编辑态，不会再发一份
    assert client.post(f"/api/staff/activities/{act.id}/publish-changes",
                       headers=admin).status_code == 409


# ======================= 手动转正 =======================

def test_锁定后管理者手动转正仍要本人确认(client, admin, db, open_group, take_student):
    act, g, queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                            lock=True)
    assert _raise_quota(client, admin, act, g, 2)["promoted"] == 0, \
        "锁定期间不该自动转正"
    assert [x["enrollment_id"] for x in _roster(client, admin, g)["waitlist"]] == [wl_id]

    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 200
    db.expire_all()
    row = db.get(Enrollment, wl_id)
    assert row.status == "pending_confirm" and row.confirm_deadline is not None
    r = client.post(f"/api/activities/enrollments/{wl_id}/confirm-promotion",
                    headers=login(client, queued))
    assert r.status_code == 200, r.text
    assert _status(db, wl_id) == "enrolled"


def test_没空位时不能手动转正(client, admin, db, open_group, take_student):
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             lock=True)
    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 409 and "名额" in r.json()["detail"]


def test_扩容后保存并发布会补齐候补(client, admin, db, open_group, take_student):
    """加名额是常见改动，名额空出来就该轮到候补，不必等下一个人退选。"""
    act, g = open_group(1)
    assert _enroll(client, act, g, take_student()).json()["status"] == "enrolled"
    for _ in range(2):
        assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"
    assert _raise_quota(client, admin, act, g, 3)["promoted"] == 2


def test_待确认期间名额不会被别人抢走(client, db, open_group, take_student):
    """确认窗内位子是给他留的，这两小时里别人报名只能进候补。"""
    act, g = open_group(1)
    taker = take_student()
    enr_id = _enroll(client, act, g, taker).json()["enrollment_id"]
    wl_id = _enroll(client, act, g, take_student()).json()["enrollment_id"]
    assert _cancel(client, taker, enr_id).json()["effect"] == "released"
    assert _status(db, wl_id) == "pending_confirm"
    assert _enroll(client, act, g, take_student()).json()["status"] == "waitlisted"
