"""编辑冻结：管理者调整安排期间暂停报名与退选。"""
from __future__ import annotations

from datetime import timedelta

from backend import config, scheduled
from backend.models import Activity, Enrollment, Notification, now
from conftest import POSITION_TEXT, editing, login


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


def test_两个管理者不能同时进入编辑态(client, admin, initiator, db,
                                    enrolled_activity):
    """后来的那位要被挡下，并被告知是谁在改，好去找人。"""
    from backend.models import ActivityManager, User, uid
    from conftest import ADMIN, INITIATOR

    act, _shift, _u = enrolled_activity
    other = db.query(User).filter(User.student_id == INITIATOR).one()
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=other.id, added_by=other.id))
    db.commit()
    editor = db.query(User).filter(User.student_id == ADMIN).one()

    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    r = client.post(f"/api/staff/activities/{act.id}/edit-lock",
                    headers=initiator)
    assert r.status_code == 409
    assert r.json()["detail"].startswith(f"{editor.name} 正在编辑本活动")


def test_超时自动解冻并通知管理者(client, admin, db, enrolled_activity):
    act, _shift, _u = enrolled_activity
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    db.expire_all()          # 接口在别的会话里写的，本会话要先失效缓存
    row = db.get(Activity, act.id)
    row.editing_since = now() - timedelta(hours=config.EDIT_LOCK_HOURS + 1)
    editor = row.editing_by
    db.commit()

    # 别按「全库解冻了几个」「这个人收到几条」来断言：用例之间共用一个库，
    # 别处留下的冻结会一起被解掉，通知也一起发给同一个管理员，这条就随机
    # 失败。通知标题里带活动名，而活动名每个用例唯一——按它来筛。
    assert scheduled.release_stale_edit_locks(db) >= 1
    db.expire_all()
    assert db.get(Activity, act.id).editing_since is None
    assert (db.query(Notification)
            .filter(Notification.user_id == editor,
                    Notification.title == f"报名已自动恢复｜{act.title}")
            .count() == 1)


def test_未冻结时不能保存并发布(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes",
                    headers=admin)
    assert r.status_code == 409


def test_余几人待分配的统计(client, admin, db, enrolled_activity, take_student):
    """没分到真实岗位的人要按人头数出来，管理者要当场看得见。

    名额给得比人多：按「已确认人数 − 岗位名额之和」算的话这里是负数，
    落空的那一个人就被名额盖掉了。
    """
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    pos.quota = 5
    db.commit()
    # 再塞一个人进来但不给岗位（岗位被删过、或管理员手工补录留下的）
    from backend.models import User, uid
    other = db.query(User).filter(
        User.student_id == take_student()).one()
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=shift.id,
                      user_id=other.id, status="enrolled", joined_at=now()))
    db.commit()

    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    sh = d["shifts"][0]
    assert sh["unassigned_count"] == 1
    assert sh["position_quota_sum"] == 5


# ======================= 未进编辑态时结构只读 =======================

def test_已发布活动不进编辑态改不了场次与岗位(client, admin, db,
                                            enrolled_activity):
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]

    def denied(r):
        return r.status_code == 409 and "调整岗位" in r.json()["detail"]

    assert denied(client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                               json={"name": "改个名"}))
    assert denied(client.post(f"/api/staff/activities/{act.id}/shifts",
                              headers=admin,
                              json={"name": "新场次", "mode": "pick",
                                    "start_time": (now() + timedelta(days=9)).isoformat(),
                                    "end_time": (now() + timedelta(days=9, hours=2)).isoformat()}))
    assert denied(client.post(f"/api/staff/shifts/{shift.id}/positions",
                              headers=admin, json={"name": "新岗位", "quota": 2}))
    assert denied(client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                               json={"quota": 9}))
    assert denied(client.delete(f"/api/staff/positions/{pos.id}", headers=admin))

    # 进入编辑态后照常改
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    assert client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                        json={"quota": 9}).status_code == 200


def test_筹备中的活动可以直接改(client, admin, db, enrolled_activity):
    """还没公开、没人报名，不必走暂停报名那一套。"""
    act, shift, _u = enrolled_activity
    db.get(Activity, act.id).status = "draft"
    db.commit()
    assert client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                        json={"name": "草稿随便改"}).status_code == 200
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 409


def test_已截止的活动也能进编辑态建岗位(client, admin, db, enrolled_activity):
    """统一分配的岗位就是报名截止之后才建的。"""
    act, shift, _u = enrolled_activity
    db.get(Activity, act.id).status = "closed"
    db.commit()
    assert client.post(f"/api/staff/activities/{act.id}/edit-lock",
                       headers=admin).status_code == 200
    assert client.post(f"/api/staff/shifts/{shift.id}/positions", headers=admin,
                       json={"name": "整理岗", "quota": 3,
                              **POSITION_TEXT}).status_code == 200


# ======================= 编辑期间的改动按人汇总通知 =======================

def _changed(db, user_id, act):
    """这个人收到的本活动「活动变更提醒」。按标题筛：活动名每个用例唯一。"""
    db.expire_all()
    return (db.query(Notification)
            .filter(Notification.user_id == user_id,
                    Notification.title == f"活动变更提醒｜{act.title}")
            .all())


def _second_position(db, shift, take_student, name="检录"):
    """同一场次再开一个岗位，放一个人进去。"""
    from backend.models import Position, uid
    from conftest import student_row
    pos = Position(id=uid("p"), shift_id=shift.id, name=name, quota=5,
                   waitlist_quota=2, sort=1, **POSITION_TEXT)
    db.add(pos)
    db.flush()
    v = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=shift.activity_id,
                      shift_id=shift.id, user_id=v.id, position_id=pos.id,
                      status="enrolled", joined_at=now()))
    db.commit()
    return pos, v


def test_编辑期间不发通知_发布后每人一条只写跟他有关的(client, admin, db,
                                                  enrolled_activity,
                                                  take_student):
    act, shift, u = enrolled_activity
    pos = shift.positions[0]
    _pos2, v = _second_position(db, shift, take_student)

    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                     json={"location": "南门"})
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                     json={"note": "改到 8 点集合"})
    assert r.status_code == 200, r.text
    assert _changed(db, u.id, act) == [] and _changed(db, v.id, act) == []
    assert not (db.query(Notification)
                .filter(Notification.user_id == u.id,
                        Notification.template_key == "assignment").count())

    r = client.post(f"/api/staff/activities/{act.id}/publish-changes",
                    headers=admin)
    assert r.status_code == 200, r.text

    mine = _changed(db, u.id, act)
    assert len(mine) == 1
    assert "岗位「" in mine[0].content and "北门→南门" in mine[0].content
    assert "→改到 8 点集合" in mine[0].content
    # 别的岗位的人只收到场次那一条，看不到引导岗改了地点
    theirs = _changed(db, v.id, act)
    assert len(theirs) == 1
    assert "→改到 8 点集合" in theirs[0].content and "南门" not in theirs[0].content


def test_删掉有报名的场次_发布时告诉被取消的人(client, admin, db,
                                          enrolled_activity, take_student):
    from datetime import timedelta as td
    from backend.models import Position, Shift, uid
    from conftest import student_row
    act, shift, _u = enrolled_activity
    extra = Shift(id=uid("s"), activity_id=act.id, name="下午场",
                  start_time=shift.start_time + td(hours=5),
                  end_time=shift.end_time + td(hours=5), mode="pick", sort=1)
    db.add(extra)
    db.flush()
    p = Position(id=uid("p"), shift_id=extra.id, name="引导", quota=5,
                 waitlist_quota=2, sort=0, **POSITION_TEXT)
    db.add(p)
    db.flush()
    w = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=extra.id,
                      user_id=w.id, position_id=p.id, status="enrolled",
                      joined_at=now()))
    db.commit()
    extra_id = extra.id

    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.delete(f"/api/staff/shifts/{extra_id}?force=1", headers=admin)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert not db.query(Enrollment).filter(Enrollment.shift_id == extra_id).count()
    assert _changed(db, w.id, act) == []

    client.post(f"/api/staff/activities/{act.id}/publish-changes", headers=admin)
    got = _changed(db, w.id, act)
    # 发出去的是志愿者口径，不是留痕那句带「（强制，含 N 条报名记录）」的摘要
    assert len(got) == 1 and "场次「下午场」已取消" in got[0].content
    assert "强制" not in got[0].content and "报名记录" not in got[0].content


def test_超时自动解冻也按人发出改动(client, admin, db, enrolled_activity):
    act, shift, u = enrolled_activity
    pos = shift.positions[0]
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    db.expire_all()
    row = db.get(Activity, act.id)
    row.editing_since = now() - timedelta(hours=config.EDIT_LOCK_HOURS + 1)
    db.commit()
    client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                 json={"location": "西门"})
    assert _changed(db, u.id, act) == []

    scheduled.release_stale_edit_locks(db)
    got = _changed(db, u.id, act)
    assert len(got) == 1 and "北门→西门" in got[0].content


def test_不在编辑态改活动信息照样即时通知(client, admin, db, enrolled_activity):
    act, _shift, u = enrolled_activity
    r = client.patch(f"/api/staff/activities/{act.id}", headers=admin,
                     json={"location": "明德楼"})
    assert r.status_code == 200, r.text
    got = _changed(db, u.id, act)
    assert len(got) == 1 and "明德楼" in got[0].content


def test_改动太多时通知只写前几项(client, admin, db, enrolled_activity):
    """编辑期间改几十处，一条通知念不完：只写前 8 项，余下指到变更记录。"""
    from backend.notify import _MAX_LINES
    act, shift, u = enrolled_activity
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    n = _MAX_LINES + 3
    for i in range(n):
        r = client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                         json={"note": f"第 {i} 次调整"})
        assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{act.id}/publish-changes",
                    headers=admin)
    assert r.status_code == 200, r.text

    got = _changed(db, u.id, act)
    assert len(got) == 1
    lines = got[0].content.splitlines()
    body = [x for x in lines if x.startswith("场次「")]
    assert len(body) == _MAX_LINES, got[0].content
    assert f"另有 {n - _MAX_LINES} 项改动" in got[0].content
    # 「结束编辑并发布」那条留痕也不该把全部改动拼进去
    assert f"等共 {n} 项" in r.json()["summary"]


def test_岗位字段超长被拦下(client, admin, db, enrolled_activity):
    """库里是 String(256)：不拦就是插入时报错，管理者看到一个 500。"""
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                         json={"needs_leader": True, "leader_task": "带" * 257})
        assert r.status_code == 400 and "组长任务" in r.json()["detail"], r.text
        r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                         json={"attire": "own", "attire_note": "白" * 257})
        assert r.status_code == 400 and "服装备注" in r.json()["detail"], r.text
        r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                         json={"name": "岗" * 129})
        assert r.status_code == 400 and "岗位名" in r.json()["detail"], r.text
        # 新建这条路由走 PositionBody 的 max_length，422
        r = client.post(f"/api/staff/shifts/{shift.id}/positions", headers=admin,
                        json={"name": "检录", "quota": 2,
                              "pay_optional": True, "pay_note": "元" * 257,
                              **POSITION_TEXT})
        assert r.status_code == 422, r.text


def test_生成过签到码的场次不能删(client, admin, db, enrolled_activity):
    """签到记录挂在场次上，删了它没处安放：生成过签到码就拒删（force 也不行）。"""
    from backend.models import CheckinTask, Shift, new_checkin_token, uid
    act, shift, u = enrolled_activity
    db.add(CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=shift.id,
                       mode="shift", token=new_checkin_token(), created_by=u.id))
    db.commit()
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.delete(f"/api/staff/shifts/{shift.id}?force=1", headers=admin)
    assert r.status_code == 409 and "签到码" in r.json()["detail"], r.text
    db.expire_all()
    assert db.get(Shift, shift.id) is not None


def test_有报名历史的岗位不能删(client, admin, db, enrolled_activity):
    """报名记录（退选过的也算）与签到码都挂在 position_id 上（外键）：
    有引用就不给删，删了这些记录就要跟着丢；不再用的话保留岗位、暂停报名即可。"""
    from backend.models import CheckinTask, Position, new_checkin_token, uid
    act, shift, u = enrolled_activity
    pos = shift.positions[0]
    enr = (db.query(Enrollment)
           .filter(Enrollment.user_id == u.id, Enrollment.shift_id == shift.id)
           .one())
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=login(client, u.student_id)).status_code == 200
    client.post(f"/api/staff/activities/{act.id}/edit-lock", headers=admin)
    r = client.delete(f"/api/staff/positions/{pos.id}", headers=admin)
    assert r.status_code == 409 and "报名历史" in r.json()["detail"], r.text
    db.expire_all()
    assert db.get(Position, pos.id) is not None

    # 签到码是另一道：把报名历史摘掉、只剩码，也照样拒删
    (db.query(Enrollment).filter(Enrollment.position_id == pos.id)
     .update({"position_id": None}, synchronize_session=False))
    db.add(CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=shift.id,
                       position_id=pos.id, mode="position",
                       token=new_checkin_token(), created_by=u.id))
    db.commit()
    r = client.delete(f"/api/staff/positions/{pos.id}", headers=admin)
    assert r.status_code == 409 and "签到码" in r.json()["detail"], r.text
