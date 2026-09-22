"""签到闭环：建码、扫码签到、再扫签退、非本场次拒绝。"""
from __future__ import annotations

import json
from datetime import timedelta

import pytest
import redis

from backend import config
from backend.locks import CHECKIN_CODE_LEN
from backend.models import (Activity, ActivityManager, CheckinRecord, CheckinTask,
                            Enrollment, Position, Shift, User, now, uid)
from conftest import ADMIN, login, student_row


@pytest.fixture
def live_group(client, db, admin, take_student):
    """一个正在进行的场次 + 一名已确认的志愿者。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"签到测试 {uid('t')}",
                   intro="x", location="集合点",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=1),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="进行中场次",
              start_time=now() - timedelta(minutes=30),
              end_time=now() + timedelta(hours=3),
              mode="pick", sort=0)
    db.add(g)
    db.flush()
    pos = Position(id=uid("p"), shift_id=g.id, name="签到测试岗位",
                   location="集合点", quota=5, sort=0)
    db.add(pos)
    db.flush()

    # 直接落库而不是走报名接口：场次已经开场了，接口会（正确地）拒绝。
    # 现实里这个人是昨天报的名，今天来签到。
    sid = take_student()
    h = login(client, sid)
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=student_row(db, sid).id, position_id=pos.id,
                      status="enrolled", joined_at=now() - timedelta(days=1)))
    db.commit()
    token = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                        json={"mode": "shift"}, headers=admin).json()["token"]
    return act, g, h, token


def test_scan_twice_is_checkin_then_checkout(client, db, live_group):
    _act, g, h, token = live_group

    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["state"] == "active" and info["enrolled"] is True

    first = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert first["action"] == "checkin"

    # 散场时的那第二扫：把签到时间往回拨过保护窗，不然会被当成手滑挡回来
    # （手滑那一路见 test_签完马上再扫一遍不会被签退）
    rec = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id).one()
    rec.checkin_at = now() - timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES + 1)
    db.commit()

    second = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert second["action"] == "checkout"

    third = client.post(f"/api/checkin/task/{token}", headers=h)
    assert third.status_code == 409          # 签完退再扫不该重复记

    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["checked_in"] and info["checked_out"]


def test_签完马上再扫一遍不会被签退(client, db, live_group):
    """手滑扫两遍 = 当场签退、时长归零，而当事人看不出发生了什么。挡回去。"""
    _act, _g, h, token = live_group
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).json()["action"] == "checkin"

    again = client.post(f"/api/checkin/task/{token}", headers=h)
    assert again.status_code == 409 and "刚签到" in again.json()["detail"]

    db.expire_all()                        # 另开一个会话读，别信本会话的缓存
    rec = (db.query(CheckinRecord)
           .filter(CheckinRecord.shift_id == _g.id).one())
    assert rec.checkout_at is None          # 关键：没被当成签退落下记录


def test_隔够时间再扫就是正常签退(client, db, live_group):
    """保护窗只挡「刚刚」，散场回来签退照旧。"""
    _act, g, h, token = live_group
    client.post(f"/api/checkin/task/{token}", headers=h)

    # 把签到时间往回拨到保护窗之外：现实里这段时间是干活干掉的
    rec = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id).one()
    rec.checkin_at = now() - timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES + 1)
    db.commit()

    d = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert d["action"] == "checkout"


def test_outsider_cannot_check_in(client, live_group, take_student):
    _act, _g, _h, token = live_group
    outsider = login(client, take_student())
    r = client.post(f"/api/checkin/task/{token}", headers=outsider)
    assert r.status_code == 403


def test_checkin_needs_login(client, live_group):
    _act, _g, _h, token = live_group
    assert client.post(f"/api/checkin/task/{token}").status_code == 401


# ======================= 多场次：码属于哪一场要看得出来 =======================

@pytest.fixture
def two_shift_act(client, db, admin):
    """一个活动 + 上午场（带岗位）+ 挂在它下面的赛前培训。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"多场次签到 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=1), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    am = Shift(id=uid("g"), activity_id=act.id, name="上午场",
               start_time=now() + timedelta(days=2),
               end_time=now() + timedelta(days=2, hours=4), mode="pick", sort=0)
    db.add(am)
    db.flush()
    db.add(Position(id=uid("p"), shift_id=am.id, name="引导", quota=5, sort=0))
    train = Shift(id=uid("g"), activity_id=act.id, name="赛前培训",
                  kind="training", parent_id=am.id,
                  start_time=now() + timedelta(days=1),
                  end_time=now() + timedelta(days=1, hours=2),
                  mode="pick", hours_mode="checkin", sort=1)
    db.add(train)
    db.commit()
    return act, am, train


def test_默认可签时段比场次起止各放宽半小时(client, admin, db, two_shift_act):
    """卡着场次时间开闸的话，提前到岗的人签不了、拖堂散场的人也签不了。"""
    _act, am, _train = two_shift_act
    tid = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks",
                      json={"mode": "shift"}, headers=admin).json()["id"]
    task = db.get(CheckinTask, tid)
    pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
    assert task.valid_from == am.start_time - pad
    assert task.valid_to == am.end_time + pad


def test_自己传的时段不会被覆盖(client, admin, db, two_shift_act):
    _act, am, _train = two_shift_act
    f = (am.start_time - timedelta(hours=3)).isoformat()
    t = (am.end_time + timedelta(hours=3)).isoformat()
    tid = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                      json={"mode": "shift", "valid_from": f,
                            "valid_to": t}).json()["id"]
    task = db.get(CheckinTask, tid)
    assert task.valid_from == am.start_time - timedelta(hours=3)
    assert task.valid_to == am.end_time + timedelta(hours=3)


def test_码表带上场次让前端分得出是哪一场(client, admin, two_shift_act):
    """列表按活动查，两场的码混在一起；没有 shift_id 就没法分辨，也没法把
    手动补签锁到当前大屏那一场上。"""
    act, am, train = two_shift_act
    client.post(f"/api/staff/shifts/{am.id}/checkin-tasks",
                json={"mode": "shift"}, headers=admin)
    client.post(f"/api/staff/shifts/{train.id}/checkin-tasks",
                json={"mode": "shift"}, headers=admin)

    rows = client.get(f"/api/staff/activities/{act.id}/checkin-tasks",
                      headers=admin).json()
    by_shift = {r["shift_id"]: r for r in rows}
    assert set(by_shift) == {am.id, train.id}
    assert by_shift[am.id]["shift_name"] == "上午场"
    assert by_shift[am.id]["shift_kind"] == "service"
    # 培训场次也能建码：hours_mode 按签到计时，签不了到就没时长
    assert by_shift[train.id]["shift_name"] == "赛前培训"
    assert by_shift[train.id]["shift_kind"] == "training"
    assert by_shift[train.id]["shift_kind_label"] == "培训"


def test_分岗位签到只认本场次的岗位(client, admin, two_shift_act):
    """换场次时前端会清空岗位选择；后端也得拦住跨场次的岗位 id。"""
    _act, am, train = two_shift_act
    pos_id = client.get(f"/api/staff/shifts/{am.id}/roster",
                        headers=admin).json()["positions"][0]["id"]
    r = client.post(f"/api/staff/shifts/{train.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_id": pos_id})
    assert r.status_code == 400 and "本场次" in r.json()["detail"]


# ======================= 动态签到码 =======================
#
# 固定码贴在墙上能从头用到尾，拍张照发进群里，人不到场也能签。动态码把这条
# 路堵上：管理端那块屏每 30 秒换一张，每张在 Redis 里只活 60 秒。

@pytest.fixture
def rds():
    """动态码只活在 Redis 里，用例得能直接抹掉它——「一分钟到了」就是这么发生的。"""
    conn = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)
    yield conn
    conn.close()


def task_id_of(client, admin, act):
    """照前端的路径拿任务 id：先列码表，再去取码。"""
    rows = client.get(f"/api/staff/activities/{act.id}/checkin-tasks",
                      headers=admin).json()
    return rows[0]["id"]


def test_动态码能签到也能签退(client, admin, db, live_group):
    """管理端取的码和学生扫的码必须是同一张——两条路径唯一的约定就在这里。"""
    act, _g, h, _token = live_group
    tid = task_id_of(client, admin, act)
    url = f"/api/staff/checkin-tasks/{tid}/code"
    d = client.get(url, headers=admin).json()
    assert len(d["code"]) == CHECKIN_CODE_LEN
    assert 0 < d["expires_in"] <= config.CHECKIN_CODE_REFRESH_SECONDS

    # 几十个人扫的是同一块屏：有效期内反复取必须拿到同一张，不能一人一码
    assert client.get(url, headers=admin).json()["code"] == d["code"]

    code = d["code"]
    # 画的是签到落地页的网址，不是那串码本身——手机扫完得能直接点开
    svg = client.get(f"/api/checkin/qr.svg?code={code}")
    assert svg.status_code == 200 and svg.headers["content-type"].startswith("image/svg")

    assert client.get(f"/api/checkin/task/{code}", headers=h).json()["enrolled"] is True
    assert client.post(f"/api/checkin/task/{code}", headers=h).json()["action"] == "checkin"
    # 同一个码要能签退，得先把签到时间拨过保护窗（10 分钟的理由见 config）
    _act, g, _h, _token = live_group
    rec = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id).one()
    rec.checkin_at = now() - timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES + 1)
    db.commit()
    assert client.post(f"/api/checkin/task/{code}", headers=h).json()["action"] == "checkout"


def test_动态码一过期就作废(client, admin, live_group, rds):
    act, _g, h, _token = live_group
    tid = task_id_of(client, admin, act)
    code = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                      headers=admin).json()["code"]

    rds.delete(f"rucva:ckcode:{code}")        # 一分钟过去了
    assert client.get(f"/api/checkin/task/{code}", headers=h).status_code == 404
    assert client.post(f"/api/checkin/task/{code}", headers=h).status_code == 404
    # 画码也得拒：这个口子不用登录，收下任意一串字符就等于让人拿平台的
    # 域名生成一张指向任意路径的码，扫的人无从分辨
    assert client.get(f"/api/checkin/qr.svg?code={code}").status_code == 404


def test_换码不会把正在扫的人挡在外面(client, admin, live_group, rds):
    """换码换的是「下次取哪张」，不撤上一张：正在排队扫码的人不该因为
    管理员那一刻恰好取了新码就失败。"""
    act, _g, h, _token = live_group
    tid = task_id_of(client, admin, act)
    first = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                       headers=admin).json()["code"]

    rds.delete(f"rucva:cktask:{tid}")         # 续期键到点，下次调用就换码
    second = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                        headers=admin).json()["code"]
    assert second != first
    assert client.get(f"/api/checkin/task/{first}", headers=h).status_code == 200
    assert client.get(f"/api/checkin/task/{second}", headers=h).status_code == 200


def test_固定码没被动态码取代(client, live_group):
    """现场还有打印张贴的用法（码旁边写着是哪一场、哪个岗位），所以固定码留着。
    它比动态码短一位，两类码在形状上分得开。"""
    _act, _g, h, token = live_group
    assert len(token) == 6
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).json()["action"] == "checkin"


def test_不是管理者取不到动态码(client, admin, live_group, take_student,
                          initiator):
    """取码接口等于把「谁能签这一场」发出去，只有本活动的管理者能拿。"""
    act, _g, _h, _token = live_group
    url = f"/api/staff/checkin-tasks/{task_id_of(client, admin, act)}/code"
    assert client.get(url, headers=login(client, take_student())).status_code == 403
    assert client.get(url, headers=initiator).status_code == 403


# ======================= 「我的报名」上的签到按钮 =======================

def row_of(client, h, g):
    rows = client.get("/api/me/enrollments", headers=h).json()
    return next(r for r in rows if r["shift"]["id"] == g.id)


def test_我的报名只给签到状态不给签到码(client, admin, db, live_group):
    """按钮亮不亮要看这个字段，但它绝不能把码带出去。

    码是本人到现场扫出来的凭据。接口把码发给每个报名的人，「我的报名」里点
    一下就能签，动态码防代签那套就白做了——所以这里只回状态，真正的凭据
    仍留在扫码那一步。
    """
    act, g, h, token = live_group
    tid = task_id_of(client, admin, act)
    code = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                      headers=admin).json()["code"]

    c = row_of(client, h, g)["checkin"]
    assert c["state"] == "active" and c["open"] is True
    assert c["method"] == "scan"

    # 固定码、动态码都不许出现在这一行任何地方——不光是没写进 checkin
    body = json.dumps(row_of(client, h, g))
    assert token not in body and code not in body


def test_过了时段按钮就不亮(client, admin, db, live_group):
    """早到、散场之后都还能点，人只会以为签到坏了。前端靠 open 决定亮不亮。"""
    act, g, h, _token = live_group
    task = db.get(CheckinTask, task_id_of(client, admin, act))

    task.valid_to = now() - timedelta(minutes=5)
    db.commit()
    assert row_of(client, h, g)["checkin"]["open"] is False

    task.valid_to = now() + timedelta(hours=1)
    task.valid_from = now() + timedelta(minutes=5)
    db.commit()
    assert row_of(client, h, g)["checkin"]["open"] is False


def test_签完到按钮就该翻成签退(client, db, live_group):
    """学生点签到 → 扫到码 → 按钮变「签退」，这一跳靠的就是这两个字段。"""
    _act, g, h, token = live_group
    assert row_of(client, h, g)["checked_in"] is False

    client.post(f"/api/checkin/task/{token}", headers=h)

    row = row_of(client, h, g)
    assert row["checked_in"] is True and row["checkout_at"] is None

    rec = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id).one()
    rec.checkin_at = now() - timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES + 1)
    db.commit()
    client.post(f"/api/checkin/task/{token}", headers=h)

    row = row_of(client, h, g)
    assert row["checkout_at"] is not None        # 签退完按钮就该消失
