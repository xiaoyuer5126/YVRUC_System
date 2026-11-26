"""整个活动一张名单：全活动名单接口、跨岗位调岗、按岗位发通知与补招。

管理端「志愿者管理」不再按场次翻页，一次拿到全部服务场次的人，筛选在前端做；
普通岗位各自带一个场次，所以「换岗位」就是换场次（/enrollments/{id}/move）。
"""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest

from backend.models import (Activity, ActivityManager, AuditLog, CheckinRecord,
                            CheckinTask, Enrollment, Notification, Position,
                            Shift, User, new_checkin_token, now, uid)
from conftest import ADMIN, POSITION_TEXT, mk_shift, student_row

# 场次按天错开，和别的用例、别的夹具都不撞时段
_days = itertools.count(700, 4)


def _span(day: int, hour: int, hours: int = 3) -> dict:
    start = (now() + timedelta(days=day)).replace(
        hour=hour, minute=0, second=0, microsecond=0)
    return {"start_time": start, "end_time": start + timedelta(hours=hours)}


def _post(db, act, name: str, span: dict, *, quota: int = 2,
          needs_leader: bool = False) -> Position:
    """一个普通岗位：自带一个只装它的场次，场次名 = 岗位名。"""
    g = mk_shift(id=uid("g"), activity_id=act.id, name=name, mode="pick",
                 sort=0, periods=[span])
    db.add(g)
    db.flush()
    p = Position(id=uid("p"), shift_id=g.id, name=name, quota=quota,
                 waitlist_quota=3, needs_leader=needs_leader,
                 leader_task="带队进场" if needs_leader else "",
                 sort=0, **POSITION_TEXT)
    db.add(p)
    db.flush()
    return p


def _enroll(db, act, p: Position, sid: str, *, status: str = "enrolled",
            is_leader: bool = False, allow_reassign: bool = True,
            joined_after: int = 0) -> Enrollment:
    u = student_row(db, sid)
    e = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=p.shift_id,
                   user_id=u.id, position_id=p.id, status=status,
                   is_leader=is_leader, allow_reassign=allow_reassign,
                   joined_at=now() + timedelta(seconds=joined_after))
    db.add(e)
    db.flush()
    return e


@pytest.fixture
def roster_act(db, take_student):
    """一个报名中的活动：三个普通岗位（引导、检录、安检）+ 一批统一分配。

    引导与检录同一天、时段压在一起（调岗时段冲突要用）；安检在第二天。
    """
    admin = student_row(db, ADMIN)
    day = next(_days)
    act = Activity(id=uid("act"), title=f"名单测试 {uid('t')}",
                   intro="x", location="世纪馆", contact_group_qr="x",
                   contacts=[{"name": "王", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=day), created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin.id, added_by=admin.id))
    db.flush()
    guide = _post(db, act, "引导", _span(day, 9), quota=2, needs_leader=True)
    check = _post(db, act, "检录", _span(day, 10), quota=1)
    secure = _post(db, act, "安检", _span(day + 1, 9), quota=2)
    # 统一分配：占位岗位「待分配」装还没安排的人，另有一个分配用的真实岗位
    big = mk_shift(id=uid("g"), activity_id=act.id, name="闭幕式统一分配",
                   mode="assign", sort=3, periods=[_span(day + 2, 14)])
    db.add(big)
    db.flush()
    holder = Position(id=uid("p"), shift_id=big.id,
                      name=Position.PLACEHOLDER_NAME, is_placeholder=True,
                      quota=5, sort=-1, **POSITION_TEXT)
    stage = Position(id=uid("p"), shift_id=big.id, name="舞台组", quota=2,
                     sort=0, **POSITION_TEXT)
    db.add_all([holder, stage])
    db.flush()

    sids = [take_student() for _ in range(5)]
    rows = {
        # 引导：组长 + 一名队员（名额 2 已满）+ 一名候补
        "enr_leader": _enroll(db, act, guide, sids[0], is_leader=True),
        "enr_member": _enroll(db, act, guide, sids[1], joined_after=1),
        "enr_waiting": _enroll(db, act, guide, sids[2], status="waitlisted",
                               joined_after=2),
        "enr_secure": _enroll(db, act, secure, sids[3]),
        "enr_unplaced": _enroll(db, act, holder, sids[4]),
    }
    db.commit()
    return {"act": act, "day": day, "guide": guide, "check": check,
            "secure": secure, "holder": holder, "stage": stage,
            "sids": sids, **rows}


def _roster(client, admin, act_id: str) -> dict:
    r = client.get(f"/api/staff/activities/{act_id}/roster", headers=admin)
    assert r.status_code == 200, r.text
    return r.json()


def _move(client, admin, enr_id: str, position_id: str, **kw):
    return client.post(f"/api/staff/enrollments/{enr_id}/move", headers=admin,
                       json={"position_id": position_id, **kw})


def _checkin(db, act, p: Position, user_id: str) -> None:
    """给某人在某个岗位记一条签到（现场扫码的等价物）。"""
    g = db.get(Shift, p.shift_id)
    admin = student_row(db, ADMIN)
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       period_id=g.periods[0].id, mode="shift",
                       token=new_checkin_token(), created_by=admin.id,
                       valid_from=g.start_time, valid_to=g.end_time)
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=g.id, period_id=g.periods[0].id,
                         position_id=p.id, user_id=user_id, method="manual"))
    db.commit()


# ======================= 全活动名单 =======================

def test_全活动名单一次给出所有岗位的人(client, admin, db, roster_act):
    d = _roster(client, admin, roster_act["act"].id)
    names = {r["position_name"] for r in d["enrolled"]}
    assert names == {"引导", "安检", Position.PLACEHOLDER_NAME}
    assert len(d["enrolled"]) == 4            # 引导 2 + 安检 1 + 待分配 1
    # 每一行都带岗位所属的那一场：整张名单混着十几个岗位，光有岗位名
    # 认不出「上午那个引导岗」和「下午那个引导岗」
    row = next(r for r in d["enrolled"] if r["position_name"] == "引导")
    assert row["shift_id"] == roster_act["guide"].shift_id
    assert row["needs_assign"] is False
    assert row["time_text"] and len(row["days"]) == 1
    assert row["user"]["student_id"] == roster_act["sids"][0]
    assert row["is_leader"] is True
    big = next(r for r in d["enrolled"]
               if r["position_name"] == Position.PLACEHOLDER_NAME)
    assert big["needs_assign"] is True and big["shift_name"].endswith("统一分配")


def test_全活动名单带候补与退选(client, admin, db, roster_act):
    _enroll(db, roster_act["act"], roster_act["secure"],
            roster_act["sids"][1], status="cancelled")
    db.commit()
    d = _roster(client, admin, roster_act["act"].id)
    assert [w["user"]["student_id"] for w in d["waitlist"]] \
        == [roster_act["sids"][2]]
    # 队列按岗位算：引导岗的候补第一位就是第 1 位
    assert d["waitlist"][0]["queue_position"] == 1
    assert d["waitlist"][0]["position_name"] == "引导"
    assert [c["position_name"] for c in d["cancelled"]] == ["安检"]


def test_名单里的岗位清单带时段与统一分配标记(client, admin, roster_act):
    d = _roster(client, admin, roster_act["act"].id)
    by_name = {p["name"]: p for p in d["positions"]}
    assert set(by_name) == {"引导", "检录", "安检",
                            Position.PLACEHOLDER_NAME, "舞台组"}
    assert by_name["引导"]["enrolled"] == 2 and by_name["引导"]["quota"] == 2
    assert by_name["引导"]["needs_assign"] is False
    assert by_name["引导"]["time_text"] and by_name["引导"]["days"]
    assert by_name["舞台组"]["needs_assign"] is True
    assert by_name[Position.PLACEHOLDER_NAME]["is_placeholder"] is True
    # 占位岗位的人数按整场数（分到真实岗位的人仍占这一场的名额）
    assert by_name[Position.PLACEHOLDER_NAME]["enrolled"] == 1
    # 统一分配那一场要能单独认出来：一键分配、导入排班只对它有意义
    assign_shifts = [g for g in d["shifts"] if g["needs_assign"]]
    assert len(assign_shifts) == 1 and assign_shifts[0]["capacity"] == 5
    assert len(d["shifts"]) == 4


def test_普通岗位的候补提示不提场次(client, admin, db, roster_act):
    """一岗一场，对管理者来说它就是一个岗位。"""
    guide = db.get(Shift, roster_act["guide"].shift_id)
    guide.list_locked = True
    db.commit()
    d = _roster(client, admin, roster_act["act"].id)
    hit = next(g for g in d["shifts"] if g["id"] == guide.id)
    assert "名单已锁定" in hit["auto_promote_stop"]
    guide.list_locked = False
    for p in guide.periods:
        p.start_time = now() - timedelta(hours=2)
        p.end_time = now() - timedelta(hours=1)
    db.commit()
    d = _roster(client, admin, roster_act["act"].id)
    hit = next(g for g in d["shifts"] if g["id"] == guide.id)
    assert hit["auto_promote_stop"].startswith("岗位已开始")


def test_不共管这个活动的人看不到名单(client, initiator, roster_act):
    r = client.get(f"/api/staff/activities/{roster_act['act'].id}/roster",
                   headers=initiator)
    assert r.status_code == 403


# ======================= 跨岗位调岗 =======================

def test_跨岗位调岗换场次并通知本人(client, admin, db, roster_act):
    enr = roster_act["enr_member"]
    r = _move(client, admin, enr.id, roster_act["secure"].id)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["position_name"] == "安检"
    db.expire_all()
    row = db.get(Enrollment, enr.id)
    assert row.position_id == roster_act["secure"].id
    assert row.shift_id == roster_act["secure"].shift_id      # 换了场次
    assert row.assign_forced is False and row.assigned_by
    # 本人要收到通知：时间地点都可能变了
    msg = (db.query(Notification)
           .filter(Notification.user_id == row.user_id,
                   Notification.template_key == "position_moved").one())
    assert "安检" in msg.content and "引导" in msg.content
    assert "{" not in msg.content
    log = (db.query(AuditLog)
           .filter(AuditLog.target_id == enr.id,
                   AuditLog.action == "assign_manual").one())
    assert "「引导」→「安检」" in log.summary


def test_调岗腾出的位子按候补顺序递补(client, admin, db, roster_act):
    r = _move(client, admin, roster_act["enr_member"].id, roster_act["secure"].id)
    assert r.status_code == 200, r.text
    assert r.json()["promoted"] == 1
    db.expire_all()
    # 候补队首进确认窗，不是直接塞进名单：来不来仍由本人定
    assert db.get(Enrollment, roster_act["enr_waiting"].id).status == "pending_confirm"


def test_目标岗位名额不够要勾强制(client, admin, db, roster_act):
    """检录岗名额 1，先占满。"""
    _enroll(db, roster_act["act"], roster_act["check"], roster_act["sids"][3],
            status="enrolled")
    db.commit()
    enr = roster_act["enr_member"]
    r = _move(client, admin, enr.id, roster_act["check"].id)
    assert r.status_code == 409 and "名额已满" in r.json()["detail"]
    assert "强制" in r.json()["detail"]
    db.expire_all()
    assert db.get(Enrollment, enr.id).position_id == roster_act["guide"].id

    r = _move(client, admin, enr.id, roster_act["check"].id, force=True)
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Enrollment, enr.id)
    assert row.position_id == roster_act["check"].id
    assert row.assign_forced is True            # 名单上标「强制」，留痕也记
    log = (db.query(AuditLog)
           .filter(AuditLog.target_id == enr.id,
                   AuditLog.action == "force_assign").one())
    assert "名额已满" in log.summary


def test_时段冲突先提示确认后照调(client, admin, db, roster_act):
    """检录与引导时段压在一起：安检岗的人调去检录，撞上他报的引导。"""
    enr = _enroll(db, roster_act["act"], roster_act["secure"],
                  roster_act["sids"][0])     # 组长同时报了安检
    db.commit()
    r = _move(client, admin, enr.id, roster_act["check"].id)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["code"] == "time_conflict"
    assert "引导" in detail["message"] and detail["conflicts"]
    db.expire_all()
    assert db.get(Enrollment, enr.id).position_id == roster_act["secure"].id

    r = _move(client, admin, enr.id, roster_act["check"].id,
              confirm_conflict=True)
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Enrollment, enr.id)
    assert row.position_id == roster_act["check"].id
    assert row.assign_forced is True
    log = (db.query(AuditLog)
           .filter(AuditLog.target_id == enr.id,
                   AuditLog.action == "force_assign").one())
    assert "冲突" in log.summary


def test_组长调岗后卸任(client, admin, db, roster_act):
    r = _move(client, admin, roster_act["enr_leader"].id, roster_act["secure"].id)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Enrollment, roster_act["enr_leader"].id).is_leader is False
    assert not (db.query(Enrollment)
                .filter(Enrollment.position_id == roster_act["guide"].id,
                        Enrollment.is_leader.is_(True)).all())


def test_已在原岗位签到过的人调走要确认(client, admin, db, roster_act):
    enr = roster_act["enr_member"]
    _checkin(db, roster_act["act"], roster_act["guide"],
             db.get(Enrollment, enr.id).user_id)
    r = _move(client, admin, enr.id, roster_act["secure"].id)
    assert r.status_code == 409 and "签到" in r.json()["detail"]
    r = _move(client, admin, enr.id, roster_act["secure"].id, force=True)
    assert r.status_code == 200, r.text


def test_可以调进统一分配的待分配里(client, admin, db, roster_act):
    """那一批还没排岗位，人先放进「待分配」，之后一键分配时一起排。"""
    enr = roster_act["enr_secure"]
    r = _move(client, admin, enr.id, roster_act["holder"].id)
    assert r.status_code == 200, r.text
    db.expire_all()
    row = db.get(Enrollment, enr.id)
    assert row.position_id == roster_act["holder"].id
    assert row.shift_id == roster_act["holder"].shift_id


def test_统一分配场次里的人调到真实岗位(client, admin, db, roster_act):
    """同一场次内换岗位：名额、冲突都在这一场里算，走一键分配那条路。"""
    enr = roster_act["enr_unplaced"]
    r = _move(client, admin, enr.id, roster_act["stage"].id)
    assert r.status_code == 200, r.text
    assert r.json()["applied"] == 1
    db.expire_all()
    row = db.get(Enrollment, enr.id)
    assert row.position_id == roster_act["stage"].id
    assert row.shift_id == roster_act["stage"].shift_id    # 场次没变


def test_调岗只认本活动的岗位(client, admin, roster_act, open_group):
    _other, g = open_group(2)
    r = _move(client, admin, roster_act["enr_member"].id, g.test_position_id)
    assert r.status_code == 400 and "不属于该活动" in r.json()["detail"]


def test_只有正式报名才能调岗(client, admin, roster_act):
    r = _move(client, admin, roster_act["enr_waiting"].id, roster_act["secure"].id)
    assert r.status_code == 409 and "正式报名" in r.json()["detail"]


def test_在目标岗位已有记录不能调过去(client, admin, db, roster_act):
    enr = _enroll(db, roster_act["act"], roster_act["secure"],
                  roster_act["sids"][1])       # 引导岗那位也报了安检
    db.commit()
    r = _move(client, admin, roster_act["enr_member"].id, roster_act["secure"].id)
    assert r.status_code == 409 and "已有记录" in r.json()["detail"]
    assert enr.id                              # 原来那条还在


# ======================= 按岗位发通知 =======================

def _notify(client, admin, act_id: str, **body):
    return client.post(f"/api/staff/activities/{act_id}/notify", headers=admin,
                       json={"title": "集合提醒", "content": "提前十分钟到",
                             **body})


def _notified(db, roster_act) -> set[str]:
    """这个活动的几位同学里，谁收到了那条通知。

    库是整轮用例共用的，只认这几个人名下的：按标题全库查会把别的用例
    发的同名通知也数进来。
    """
    ids = {student_row(db, sid).id for sid in roster_act["sids"]}
    return {db.get(User, n.user_id).student_id for n in db.query(Notification)
            .filter(Notification.template_key == "custom",
                    Notification.title == "集合提醒",
                    Notification.user_id.in_(ids)).all()}


def test_按岗位发通知只发给那几个岗位的人(client, admin, db, roster_act):
    r = _notify(client, admin, roster_act["act"].id, scope="positions",
                position_ids=[roster_act["guide"].id])
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 2               # 引导岗的组长 + 队员
    assert _notified(db, roster_act) == set(roster_act["sids"][:2])


def test_按岗位发通知跳过已到岗的人(client, admin, db, roster_act):
    _checkin(db, roster_act["act"], roster_act["guide"],
             db.get(Enrollment, roster_act["enr_leader"].id).user_id)
    r = _notify(client, admin, roster_act["act"].id, scope="not_checked_in",
                position_ids=[roster_act["guide"].id])
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 1
    assert _notified(db, roster_act) == {roster_act["sids"][1]}


def test_发通知选了岗位才认范围(client, admin, roster_act):
    r = _notify(client, admin, roster_act["act"].id, scope="positions")
    assert r.status_code == 400 and "请选择岗位" in r.json()["detail"]
    r = _notify(client, admin, roster_act["act"].id, scope="positions",
                position_ids=["p_不存在"])
    assert r.status_code == 400 and "不属于该活动" in r.json()["detail"]


def test_发给全部志愿者不受岗位影响(client, admin, roster_act):
    r = _notify(client, admin, roster_act["act"].id)
    assert r.status_code == 200, r.text
    assert r.json()["count"] == 4               # 三个岗位 + 待分配里的人


# ======================= 补招按岗位 =======================

def test_补招按岗位落在它自己的场次(client, admin, db, roster_act, take_student):
    sid = take_student()
    r = client.post(f"/api/staff/shifts/{roster_act['secure'].shift_id}"
                    f"/add-walkin", headers=admin,
                    json={"student_id": sid,
                          "position_id": roster_act["secure"].id})
    assert r.status_code == 200, r.text
    row = db.get(Enrollment, r.json()["enrollment_id"])
    assert row.position_id == roster_act["secure"].id
    assert row.shift_id == roster_act["secure"].shift_id


def test_补招到统一分配时不选岗位就进待分配(client, admin, db, roster_act,
                                            take_student):
    sid = take_student()
    r = client.post(f"/api/staff/shifts/{roster_act['holder'].shift_id}"
                    f"/add-walkin", headers=admin, json={"student_id": sid})
    assert r.status_code == 200, r.text
    assert db.get(Enrollment, r.json()["enrollment_id"]).position_id \
        == roster_act["holder"].id
