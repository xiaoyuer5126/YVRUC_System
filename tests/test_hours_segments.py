"""时长明细（hours_segments）：一段一行，一笔的合计恒等于各段之和。

三条不变量（见 models.py）：
  · 每笔的 hours == 该笔各段之和（原始值）：由一个 before_flush 钩子在一处重算
    （_sync_hours_from_segments），写段的路径再多也不会漏掉一处加法；
  · 段的增删改都发生在父记录的行锁里：管理端手动加减与本人选报酬方式同时打，
    不会拼出谁都没写过的组合；
  · 删记录不留孤儿段：relationship 的 delete-orphan 加上外键的 ON DELETE CASCADE。

结算口径：一笔之内，志愿时长与劳务各自向上取整到 0.5，两者之和是结算合计
（可能略大于原始合计，见 HoursRecord.split）。
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from backend.main import app
from backend.models import (Activity, ActivityManager, CheckinRecord, CheckinTask,
                            Enrollment, HoursRecord, HoursSegment, Position, Shift,
                            User, day_of, new_checkin_token, now, span_text, uid)
from conftest import mk_shift
from conftest import ADMIN, POSITION_TEXT, import_roster, login, student_row

MANUAL_DATE = "2026-10-05"


def _make(db, sid: str, spans: list[tuple[str, float]], *, pay_idx=(),
          enroll: bool = True, checkin: bool = True):
    """一场已结束的活动，一场次一段（spans: [(场次名, 小时数)]）。

    签到签退就用场次的起止，段上的小时数精确等于 spans 里写的那个数。pay_idx
    里的场次岗位开「可选劳务」，本人才能给那一段选劳务。
    """
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"分段测试 {uid('t')}", intro="x",
                   location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="finished", reg_start=now() - timedelta(days=10),
                   reg_end=now() - timedelta(days=5), vb_names=["分段测试志愿服务"],
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=admin.id,
                           added_by=admin.id))
    u = student_row(db, sid)
    day = now().replace(hour=8, minute=0, second=0, microsecond=0) - timedelta(days=2)
    out = []
    for i, (name, hours) in enumerate(spans):
        start = day + timedelta(hours=6 * i)
        g = mk_shift(id=uid("g"), activity_id=act.id, name=name, start_time=start,
                  end_time=start + timedelta(hours=hours), mode="pick", sort=i)
        db.add(g)
        db.flush()
        pos = Position(id=uid("p"), shift_id=g.id, name=f"{name}岗", quota=5,
                       sort=0, pay_optional=i in pay_idx, **POSITION_TEXT)
        db.add(pos)
        db.flush()
        if enroll:
            db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                              user_id=u.id, position_id=pos.id, status="enrolled",
                              joined_at=now()))
        if checkin:
            task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                               period_id=g.periods[0].id,
                               mode="shift", token=new_checkin_token(),
                               created_by=admin.id,
                               valid_from=g.start_time, valid_to=g.end_time)
            db.add(task)
            db.flush()
            db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                                 shift_id=g.id, period_id=g.periods[0].id,
                                 position_id=pos.id, user_id=u.id,
                                 method="manual", checkin_at=g.start_time,
                                 checkout_at=g.end_time))
        out.append((g, pos))
    db.commit()
    return act, u, out


def _record_activity(db):
    """一场补记录活动（不公开招募）：时长靠导入名单，没有场次与签到。"""
    admin = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"补记录测试 {uid('t')}", intro="x",
                   organizer="文学院青年志愿者协会", recruit_mode="record",
                   location="人文楼报告厅", status="finished",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   held_periods=[{
                       "start_time": (now().replace(hour=14, minute=0, second=0,
                                                    microsecond=0)
                                      - timedelta(days=9)).isoformat(timespec="minutes"),
                       "end_time": (now().replace(hour=17, minute=0, second=0,
                                                  microsecond=0)
                                    - timedelta(days=9)).isoformat(timespec="minutes")}],
                   created_by=admin.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id, user_id=admin.id,
                           added_by=admin.id))
    db.commit()
    return act


def _rec(db, act: Activity, u: User) -> HoursRecord:
    """这一笔（接口在别的会话里写的，先 expire 再读）。"""
    db.expire_all()
    return (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id,
                    HoursRecord.user_id == u.id).one())


def _invariant(db, act: Activity, u: User) -> HoursRecord:
    """合计等于各段之和（原始值，两位小数）。"""
    rec = _rec(db, act, u)
    assert rec.hours == round(sum(s.hours for s in rec.segments), 2), \
        [(s.kind, s.hours) for s in rec.segments]
    return rec


def _confirm_all(client, admin, act, *, approve=False):
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    headers=admin, json={})
    assert r.status_code == 200, r.text
    if approve:
        r = client.post(f"/api/staff/activities/{act.id}/hours/approve",
                        headers=admin, json={})
        assert r.status_code == 200, r.text


def _pick(client, headers, rec_id: str, *choices):
    """本人给各段选报酬方式：choices 是 (场次, 岗位, reward)。"""
    return client.post(f"/api/me/hours/{rec_id}/confirm", headers=headers, json={
        "parts": [{"shift_id": g.id, "position_id": p.id, "reward": r}
                  for g, p, r in choices]})


def _manual(client, admin, act, sid: str, hours: float, note: str = "场外搬运",
            date: str = MANUAL_DATE):
    return client.post(f"/api/staff/activities/{act.id}/hours/manual",
                       headers=admin,
                       json={"items": [{"student_id": sid, "hours": hours,
                                        "note": note}], "date": date})


# ======================= ① 三种段 =======================

def test_三种段都能建_服务日期与时段对(client, admin, db, take_student):
    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 2)])
    g, pos = shifts[0]
    _confirm_all(client, admin, act)

    rec = _invariant(db, act, u)
    seg = rec.segments[0]
    assert seg.kind == "shift" and seg.seq == 1 and seg.hours == 2.0
    # 服务日期是场次开始那天，时段沿用「09-20 08:00-10:00」这一套
    assert seg.date == day_of(g.start_time)
    assert seg.time_text == g.span_text() == span_text(g.start_time, g.end_time)
    assert seg.label == "上午场" and seg.position_name == "上午场岗"
    assert (seg.shift_id, seg.position_id) == (g.id, pos.id)
    assert seg.reward == "" and seg.activity_id == act.id and seg.user_id == u.id

    assert _manual(client, admin, act, sid, 1.5, "场外搬运").status_code == 200
    rec = _invariant(db, act, u)
    manual = rec.segments[1]
    assert manual.kind == "manual" and manual.seq == 2 and manual.hours == 1.5
    assert manual.date == datetime(2026, 10, 5) and manual.time_text == "10-05"
    assert manual.label == "场外搬运" and manual.position_name == ""
    assert manual.shift_id is None and manual.position_id is None

    club = _record_activity(db)
    r = import_roster(client, admin, club.id, f"{sid} 3 场地布置")
    assert r.status_code == 200, r.text
    rec2 = _invariant(db, club, u)
    imp = rec2.segments[0]
    assert imp.kind == "import" and imp.hours == 3.0 and imp.label == "场地布置"
    assert imp.date == day_of(club.held_rows()[0].start_time)
    assert imp.time_text == club.held_text()


def test_接口里的明细键名与顺序(client, admin, db, take_student):
    """前端不动：键名还是 time / position 这一套，外加新的 date。"""
    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 2), ("下午场", 3)])
    g1, _p1 = shifts[0]
    _confirm_all(client, admin, act)
    _manual(client, admin, act, sid, 1, "场外搬运")
    _manual(client, admin, act, sid, -0.5, "中途离场")

    d = client.get(f"/api/staff/activities/{act.id}/hours",
                   headers=admin).json()
    row = next(x for x in d["items"] if x["user"]["id"] == u.id)
    parts = row["parts"]
    assert [p["kind"] for p in parts] == ["shift", "shift", "manual", "manual"]
    assert [p["label"] for p in parts] == ["上午场", "下午场", "场外搬运", "中途离场"]
    keys = {"id", "label", "time", "position", "hours", "kind", "shift_id",
            "period_id", "position_id", "date"}
    for p in parts:
        assert set(p) == keys, p            # 没选劳务就只有这套键，没有 reward
    assert [p["date"] for p in parts] == [f"{g1.start_time:%Y-%m-%d}"] * 2 \
        + [MANUAL_DATE] * 2
    assert parts[0]["time"] == g1.span_text() and parts[0]["position"] == "上午场岗"
    assert parts[2]["time"] == "10-05" and parts[2]["position"] == ""
    assert parts[2]["id"] and parts[2]["hours"] == 1.0

    # 导出备注还是「时段 岗位/备注；…」那一套，手动那几段也拼在里面
    rec = _invariant(db, act, u)
    assert rec.parts_text() == (f"{g1.span_text()} 上午场岗；"
                                f"{shifts[1][0].span_text()} 下午场岗；"
                                f"10-05 场外搬运；10-05 中途离场")


# ======================= ② 合计 == 各段之和 =======================

def test_合计恒等于各段之和_走四条真实流程(client, admin, db, take_student):
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 4)])

    _confirm_all(client, admin, act)                       # 确认全部
    assert _invariant(db, act, u).hours == 4.0

    assert _manual(client, admin, act, sid, 2, "场外搬运").status_code == 200
    assert _invariant(db, act, u).hours == 6.0             # 手动加

    rec = _invariant(db, act, u)
    seg = rec.segments[-1]
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rec.id}/{seg.id}",
        headers=admin).status_code == 200                 # 手动删
    assert _invariant(db, act, u).hours == 4.0

    assert _manual(client, admin, act, sid, -1.5, "提前离场").status_code == 200
    assert _invariant(db, act, u).hours == 2.5             # 手动扣

    club = _record_activity(db)
    r = import_roster(client, admin, club.id, f"{sid} 3 场地布置")
    assert r.status_code == 200, r.text
    assert _invariant(db, club, u).hours == 3.0            # 名单导入
    rec2 = _invariant(db, club, u)
    assert client.patch(f"/api/staff/activities/{club.id}/record-roster/{rec2.id}",
                        headers=admin,
                        json={"hours": 2.5}).status_code == 200
    assert _invariant(db, club, u).hours == 2.5            # 名单里改数

    # 接口给本人看到的合计也是结算口径：这几段都在 0.5 的倍数上，与原始合计同值
    mine = client.get("/api/me/hours", headers=login(client, sid)).json()
    assert {r["activity_id"]: r["hours"] for r in mine["records"]} == {
        act.id: 2.5, club.id: 2.5}


def test_确认全部不覆盖管理者核定过的差额(client, admin, db, take_student):
    """管理者点名给的数与签到算出来的不一样时，差额记成一段调整，反复确认不叠加。"""
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 4)])
    _confirm_all(client, admin, act)

    body = {"items": [{"student_id": sid, "hours": 3}]}
    for _ in range(3):                                     # 幂等：点几次都还是 3
        r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                        headers=admin, json=body)
        assert r.status_code == 200, r.text
        rec = _invariant(db, act, u)
        assert rec.hours == 3.0
        assert [s.kind for s in rec.segments] == ["shift", "override"]
        assert rec.segments[1].hours == -1.0

    # 「确认全部」不把这次改判推回去：改判是人判的（同已采纳的申诉）
    _confirm_all(client, admin, act)
    assert _invariant(db, act, u).hours == 3.0


# ======================= ③ 明细顺序 =======================

def test_明细顺序就是写入顺序(client, admin, db, take_student):
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 2)])
    _confirm_all(client, admin, act)
    _manual(client, admin, act, sid, 1, "第一段补录")
    _manual(client, admin, act, sid, 0.5, "第二段补录")
    _confirm_all(client, admin, act)          # 重算：签到段就地改写，顺序不动

    rec = _invariant(db, act, u)
    assert [(s.seq, s.kind, s.label) for s in rec.segments] == [
        (1, "shift", "上午场"), (2, "manual", "第一段补录"), (3, "manual", "第二段补录")]
    row = next(x for x in client.get(f"/api/staff/activities/{act.id}/hours",
                                     headers=admin).json()["items"]
               if x["user"]["id"] == u.id)
    assert [p["label"] for p in row["parts"]] == ["上午场", "第一段补录", "第二段补录"]

    # 新的一段接在最大值后面：删掉中间那段再加，顺序还是写入顺序
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rec.id}/"
        f"{rec.segments[1].id}", headers=admin).status_code == 200
    _manual(client, admin, act, sid, 0.25, "第三段补录")
    rec = _invariant(db, act, u)
    assert [s.seq for s in rec.segments] == sorted(s.seq for s in rec.segments)
    assert [s.label for s in rec.segments] == ["上午场", "第二段补录", "第三段补录"]


# ======================= ④ 不留孤儿段 =======================

def test_删记录不留孤儿段(client, admin, db, take_student):
    # 名单里整条删：段跟着走（relationship 的 delete-orphan）
    sid = take_student()
    club = _record_activity(db)
    r = import_roster(client, admin, club.id, f"{sid} 3 场地布置")
    assert r.status_code == 200, r.text
    rec_id = _rec(db, club, student_row(db, sid)).id
    assert db.query(HoursSegment).filter(
        HoursSegment.record_id == rec_id).count() == 1
    assert client.delete(f"/api/staff/activities/{club.id}/record-roster/{rec_id}",
                         headers=admin).status_code == 200
    db.expire_all()
    assert db.query(HoursSegment).filter(
        HoursSegment.record_id == rec_id).count() == 0
    assert db.query(HoursSegment).filter(
        HoursSegment.activity_id == club.id).count() == 0

    # 删空的那一笔连记录一起消失，也不剩段
    sid2 = take_student()
    act, u, _shifts = _make(db, sid2, [("上午场", 2)])
    _manual(client, admin, act, sid2, 1.5, "场外搬运")
    rec = _invariant(db, act, u)
    assert client.delete(
        f"/api/staff/activities/{act.id}/hours/manual/{rec.id}/"
        f"{rec.segments[0].id}", headers=admin).status_code == 200
    db.expire_all()
    assert db.query(HoursRecord).filter(
        HoursRecord.activity_id == act.id).count() == 0
    assert db.query(HoursSegment).filter(
        HoursSegment.activity_id == act.id).count() == 0

    # 绕过 ORM 直接删记录行（外键的 ON DELETE CASCADE 兜底）
    sid3 = take_student()
    act3, u3, _shifts3 = _make(db, sid3, [("上午场", 2)])
    _confirm_all(client, admin, act3)
    rec3_id = _invariant(db, act3, u3).id
    db.execute(text("DELETE FROM hours_records WHERE id = :i"), {"i": rec3_id})
    db.commit()
    db.expire_all()
    assert db.query(HoursSegment).filter(
        HoursSegment.record_id == rec3_id).count() == 0


def test_一人一场一笔是数据库保证(client, admin, db, take_student):
    """「一人一场一笔」写在唯一约束上：并发下两条写入路径也只会落一条。"""
    names = {c["name"] for c in inspect(db.get_bind())
             .get_unique_constraints("hours_records")}
    assert "ux_hours_record_user_activity" in names, names

    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 2)])
    _confirm_all(client, admin, act)
    assert _invariant(db, act, u).hours == 2.0
    db.add(HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


# ======================= ⑤ 并发 =======================

def test_两条写入路径并发不会拼出中间态(client, admin, db, take_student):
    """本人选劳务与管理端手动加同时打：一个先一个后，结果都落在同一个终态。

    段的增删改都在活动行与父记录的行锁里（hours.lock_records / with_for_update），
    后到的那个等锁，不会拿着旧快照盖掉先到的。先选后加：手动加清掉劳务、撤销
    核准；先加后选：核准已撤销，选不了。两种顺序账上都是 5 小时志愿时长。
    """
    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 4)], pay_idx=(0,))
    g, pos = shifts[0]
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec_id = _invariant(db, act, u).id

    gate = threading.Barrier(2)
    out: dict[str, object] = {}

    def pick():
        with TestClient(app) as c:
            gate.wait()
            out["pick"] = _pick(c, stu, rec_id, (g, pos, "pay"))

    def manual():
        with TestClient(app) as c:
            gate.wait()
            out["manual"] = _manual(c, admin, act, sid, 1, "场外搬运")

    ths = [threading.Thread(target=pick), threading.Thread(target=manual)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()

    assert out["pick"].status_code in (200, 409), out["pick"].text
    assert out["manual"].status_code == 200, out["manual"].text

    rec = _invariant(db, act, u)          # 合计与段对得上，没有半截状态
    assert rec.hours == 5.0
    assert [s.kind for s in rec.segments] == ["shift", "manual"]
    assert [s.reward for s in rec.segments] == ["", ""]
    assert rec.pay_hours() == 0 and rec.counted_hours() == 5.0
    assert rec.confirmed_at is None
    assert db.get(Activity, act.id).hours_approved_at is None


@pytest.mark.parametrize("path", ["confirm", "manual"])
def test_并发给同一人记第一笔不撞唯一约束(client, admin, db, take_student, path):
    """「确认全部」点两下、两位管理者同时手动加：记录行还不存在时锁不住它，
    靠活动行锁排队，后到的看得到先来的那一笔，不会 500。"""
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 2)])
    gate = threading.Barrier(2)
    out: list = []

    def hit():
        with TestClient(app) as c:
            gate.wait()
            if path == "confirm":
                out.append(c.post(f"/api/staff/activities/{act.id}/hours/confirm",
                                  headers=admin, json={}))
            else:
                out.append(_manual(c, admin, act, sid, 1, "场外搬运"))

    ths = [threading.Thread(target=hit) for _ in range(2)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    assert [r.status_code for r in out] == [200, 200], [r.text for r in out]
    db.expire_all()
    assert db.query(HoursRecord).filter(HoursRecord.activity_id == act.id,
                                        HoursRecord.user_id == u.id).count() == 1
    # 确认两次是同一个 2 小时；手动加两次是 1 + 1
    assert _invariant(db, act, u).hours == 2.0


# ======================= ⑥ 结算取整 =======================

def test_两半各自向上取整到05(client, admin, db, take_student):
    # 2.17 + 2.17 都选劳务：劳务 4.5，志愿时长 0
    sid = take_student()
    act, u, shifts = _make(db, sid, [("第一场", 2.17), ("第二场", 2.17)],
                           pay_idx=(0, 1))
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec = _invariant(db, act, u)
    assert rec.hours == 4.34                       # 段上留原始值
    assert _pick(client, stu, rec.id,
                 (shifts[0][0], shifts[0][1], "pay"),
                 (shifts[1][0], shifts[1][1], "pay")).status_code == 200
    rec = _invariant(db, act, u)
    assert (rec.counted_hours(), rec.pay_hours()) == (0.0, 4.5)
    assert rec.settled_hours() == 4.5 > rec.hours
    assert rec.reward_label() == "劳务"
    d = client.get("/api/me/hours", headers=stu).json()
    assert d["total"] == 0.0 and d["records"][0]["hours"] == 4.5
    assert d["records"][0]["reward_label"] == "劳务"

    # 1.2 + 1.2 + 2.6（最后一段劳务）：志愿 2.5、劳务 3.0，结算 5.5
    sid2 = take_student()
    act2, u2, shifts2 = _make(db, sid2, [("第一场", 1.2), ("第二场", 1.2),
                                         ("第三场", 2.6)], pay_idx=(2,))
    _confirm_all(client, admin, act2, approve=True)
    stu2 = login(client, sid2)
    rec2 = _invariant(db, act2, u2)
    assert rec2.hours == 5.0
    assert _pick(client, stu2, rec2.id,
                 (shifts2[0][0], shifts2[0][1], "hours"),
                 (shifts2[1][0], shifts2[1][1], "hours"),
                 (shifts2[2][0], shifts2[2][1], "pay")).status_code == 200
    rec2 = _invariant(db, act2, u2)
    assert (rec2.counted_hours(), rec2.pay_hours()) == (2.5, 3.0)
    assert rec2.settled_hours() == 5.5 == 2.5 + 3.0
    assert rec2.reward_label() == "志愿时长 2.5h + 劳务 3h"
    d2 = client.get("/api/me/hours", headers=stu2).json()
    assert d2["records"][0]["hours"] == 5.5 and d2["total"] == 2.5
    # 备注里拼的还是各段的原始时段，不写取整后的数
    assert rec2.parts_text(hours_only=True).count("第一场岗") == 1
    assert "第三场岗" not in rec2.parts_text(hours_only=True)

    # 手动段可以带小数：两位原样留着，取整只发生在结算那一步
    sid3 = take_student()
    act3, u3, _s3 = _make(db, sid3, [("上午场", 2.0)])
    _confirm_all(client, admin, act3, approve=True)
    _manual(client, admin, act3, sid3, 2.17, "场外搬运")
    rec3 = _invariant(db, act3, u3)
    assert rec3.hours == 4.17 and rec3.settled_hours() == 4.5
    assert rec3.counted_hours() == 4.5 and rec3.pay_hours() == 0.0


# ======================= ⑦⑧ 手动加时的校验 =======================

def test_手动加时只给已确认报名的人(client, admin, db, take_student):
    sid = take_student()
    act, _u, _shifts = _make(db, sid, [("上午场", 2)], enroll=False)
    outsider = student_row(db, take_student())     # 这场活动里没有他的报名
    r = _manual(client, admin, act, outsider.student_id, 1)
    assert r.status_code == 400, r.text
    assert "没有报名这场活动" in r.json()["detail"]
    assert outsider.student_id in r.json()["detail"]     # 指出来是谁
    assert "补记录活动" in r.json()["detail"]              # 指一条能走的路
    db.expire_all()
    assert db.query(HoursRecord).filter(
        HoursRecord.activity_id == act.id).count() == 0


def test_老师类型不记时长(client, admin, db, take_student):
    sid = take_student()
    act, _u, _shifts = _make(db, sid, [("上午场", 2)])
    teacher = student_row(db, take_student())
    teacher.user_type = "teacher"
    db.commit()
    r = _manual(client, admin, act, teacher.student_id, 1)
    assert r.status_code == 400, r.text
    assert r.json()["detail"] == "老师类型用户不参与志愿，不记时长"


def test_手动加时的服务日期必填且要能解析(client, admin, db, take_student):
    sid = take_student()
    act, _u, _shifts = _make(db, sid, [("上午场", 2)])
    body = {"items": [{"student_id": sid, "hours": 1, "note": "场外搬运"}]}
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual",
                    headers=admin, json=body)
    assert r.status_code == 400 and "缺少服务日期" in r.json()["detail"], r.text
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual",
                    headers=admin, json={**body, "date": "十月五日"})
    assert r.status_code == 400 and "服务日期格式不对" in r.json()["detail"], r.text
    # 未来的日期也收：补录一场已经定下来的服务是常事
    r = client.post(f"/api/staff/activities/{act.id}/hours/manual",
                    headers=admin, json={**body, "date": "2099-01-02"})
    assert r.status_code == 200, r.text
    db.expire_all()
    rec = (db.query(HoursRecord).filter(HoursRecord.activity_id == act.id)
           .one())
    assert rec.segments[0].date == datetime(2099, 1, 2)
    assert rec.segments[0].time_text == "01-02"
    assert rec.hours == 1.0


# ======================= ⑨ 只删得掉手动段 =======================

def test_删除只认手动那几段(client, admin, db, take_student):
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 2)])
    _confirm_all(client, admin, act)
    _manual(client, admin, act, sid, 1.5, "场外搬运")
    rec = _invariant(db, act, u)
    shift_seg, manual_seg = rec.segments[0], rec.segments[1]

    r = client.delete(f"/api/staff/activities/{act.id}/hours/manual/"
                      f"{rec.id}/{shift_seg.id}", headers=admin)
    assert r.status_code == 404, r.text          # 签到段是账的依据，删不得
    assert "不是手动添加的" in r.json()["detail"]
    assert client.delete(f"/api/staff/activities/{act.id}/hours/manual/"
                         f"{rec.id}/none", headers=admin).status_code == 404

    rec = _invariant(db, act, u)
    assert rec.hours == 3.5 and len(rec.segments) == 2
    assert client.delete(f"/api/staff/activities/{act.id}/hours/manual/"
                         f"{rec.id}/{manual_seg.id}", headers=admin).status_code == 200
    rec = _invariant(db, act, u)
    assert rec.hours == 2.0 and [s.kind for s in rec.segments] == ["shift"]


# ======================= ⑨ 核准后改数 =======================

def _override(client, admin, act, sid: str, hours: float):
    return client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                       headers=admin,
                       json={"items": [{"student_id": sid, "hours": hours}]})


def test_劳务之后再扣_志愿时长不会扣成负数(client, admin, db, take_student):
    """4 小时选了劳务，管理者再扣 2 小时：劳务清掉、全回志愿时长再扣，记 2 小时。"""
    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 4)], pay_idx=(0,))
    g, pos = shifts[0]
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec_id = _invariant(db, act, u).id
    assert _pick(client, stu, rec_id, (g, pos, "pay")).status_code == 200

    r = _manual(client, admin, act, sid, -2, "中途离场")
    assert r.status_code == 200, r.text
    assert r.json()["reapproval_required"] is True
    rec = _invariant(db, act, u)
    assert (rec.counted_hours(), rec.pay_hours()) == (2.0, 0.0)
    assert rec.confirmed_at is None
    assert db.get(Activity, act.id).hours_approved_at is None

    # 重新核准后本人再把签到那段选成劳务：扣减从劳务里扣，两半都不为负
    client.post(f"/api/staff/activities/{act.id}/hours/approve", headers=admin,
                json={})
    assert _pick(client, stu, rec_id, (g, pos, "pay")).status_code == 200
    rec = _invariant(db, act, u)
    assert (rec.counted_hours(), rec.pay_hours()) == (0.0, 2.0)
    d = client.get("/api/me/hours", headers=stu).json()
    assert d["total"] == 0 and d["records"][0]["hours"] == 2.0
    ov = client.get(f"/api/staff/activities/{act.id}/overview", headers=admin).json()
    assert ov["hours"]["total"] == 0


def test_核定的数就是结算合计(client, admin, db, take_student):
    """劳务 2.2 + 志愿 1.2 显示 4；核定成 5 就是 5，不是按原始值补差额得 5.5。"""
    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 2.2), ("下午场", 1.2)],
                           pay_idx=(0,))
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec_id = _invariant(db, act, u).id
    assert _pick(client, stu, rec_id, (shifts[0][0], shifts[0][1], "pay")
                 ).status_code == 200
    assert _invariant(db, act, u).settled_hours() == 4.0

    assert _override(client, admin, act, sid, 5).status_code == 200
    rec = _invariant(db, act, u)
    assert rec.settled_hours() == 5.0 and rec.pay_hours() == 0
    row = next(x for x in client.get(f"/api/staff/activities/{act.id}/hours",
                                     headers=admin).json()["items"]
               if x["user"]["id"] == u.id)
    assert row["hours"] == 5.0

    # 不是 0.5 整数倍的数按结算口径向上取整
    assert _override(client, admin, act, sid, 4.2).status_code == 200
    assert _invariant(db, act, u).settled_hours() == 4.5


def test_核定改回原数_调整段去掉(client, admin, db, take_student):
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 4)])
    _confirm_all(client, admin, act)
    assert _override(client, admin, act, sid, 5).status_code == 200
    rec = _invariant(db, act, u)
    assert [(s.kind, s.hours) for s in rec.segments] == [("shift", 4.0),
                                                        ("override", 1.0)]
    assert _override(client, admin, act, sid, 4).status_code == 200
    rec = _invariant(db, act, u)
    assert [(s.kind, s.hours) for s in rec.segments] == [("shift", 4.0)]
    assert rec.settled_hours() == 4.0


def test_核定与改判的调整段删不掉(client, admin, db, take_student):
    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 4)])
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec_id = _invariant(db, act, u).id
    assert client.post(f"/api/me/hours/{rec_id}/appeal", headers=stu,
                       json={"claimed_hours": 5, "reason": "签退晚扫了一小时"}
                       ).status_code == 200
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    assert client.post(f"/api/staff/hours-appeals/{a['id']}/resolve",
                       headers=admin, json={"accept": True}).status_code == 200
    assert _override(client, admin, act, sid, 6).status_code == 200

    rec = _invariant(db, act, u)
    kinds = {s.kind: s for s in rec.segments}
    assert set(kinds) == {"shift", "appeal", "override"}
    for kind, word in (("appeal", "申诉改判"), ("override", "核定")):
        r = client.delete(f"/api/staff/activities/{act.id}/hours/manual/"
                          f"{rec.id}/{kinds[kind].id}", headers=admin)
        assert r.status_code == 409 and word in r.json()["detail"], r.text
    assert _invariant(db, act, u).settled_hours() == 6.0
    # 管理端明细里这两段不带删除入口（前端按 kind == "manual" 给按钮）
    row = next(x for x in client.get(f"/api/staff/activities/{act.id}/hours",
                                     headers=admin).json()["items"]
               if x["user"]["id"] == u.id)
    assert [p["kind"] for p in row["parts"]] == ["shift", "appeal", "override"]


def test_申诉改判不撤销核准_清掉劳务并请本人重选(client, admin, db, take_student):
    from backend.models import Notification

    sid = take_student()
    act, u, shifts = _make(db, sid, [("上午场", 4)], pay_idx=(0,))
    g, pos = shifts[0]
    _confirm_all(client, admin, act, approve=True)
    stu = login(client, sid)
    rec_id = _invariant(db, act, u).id
    assert client.post(f"/api/me/hours/{rec_id}/appeal", headers=stu,
                       json={"claimed_hours": 5, "reason": "签退晚扫了一小时"}
                       ).status_code == 200
    a = client.get(f"/api/staff/activities/{act.id}/hours-appeals",
                   headers=admin).json()[0]
    r = client.post(f"/api/staff/hours-appeals/{a['id']}/resolve", headers=admin,
                    json={"accept": True})
    assert r.status_code == 200 and r.json()["hours"] == 5.0
    assert db.get(Activity, act.id).hours_approved_at is not None
    rec = _invariant(db, act, u)
    assert rec.confirmed_at is None and rec.pay_hours() == 0
    note = (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("时长申诉已采纳%")).one())
    assert "重新确认" in note.content and "报酬方式" in note.content
    # 改判后本人照常能选劳务：差额记在志愿时长那一半
    assert _pick(client, stu, rec_id, (g, pos, "pay")).status_code == 200
    rec = _invariant(db, act, u)
    assert (rec.counted_hours(), rec.pay_hours()) == (1.0, 4.0)


def test_核准后手动加时_撤销核准且重新核准通知到他(client, admin, db, take_student):
    from backend.models import Notification

    sid = take_student()
    act, u, _shifts = _make(db, sid, [("上午场", 4)])
    _confirm_all(client, admin, act, approve=True)
    r = _manual(client, admin, act, sid, 1, "场外搬运")
    assert r.status_code == 200 and r.json()["reapproval_required"] is True
    assert db.get(Activity, act.id).hours_approved_at is None
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve", headers=admin,
                    json={})
    assert r.status_code == 200 and r.json()["count"] == 1
    assert (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.like("请确认志愿时长%")).count()) == 2

    # 删掉那段同样撤销核准
    rec = _invariant(db, act, u)
    seg = next(s for s in rec.segments if s.kind == "manual")
    r = client.delete(f"/api/staff/activities/{act.id}/hours/manual/{rec.id}/{seg.id}",
                      headers=admin)
    assert r.status_code == 200 and r.json()["reapproval_required"] is True
    db.expire_all()
    assert db.get(Activity, act.id).hours_approved_at is None
