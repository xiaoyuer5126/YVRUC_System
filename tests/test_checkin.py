"""签到闭环：建码、扫码签到、再扫签退、非本场次拒绝。"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta

import pytest

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


def _past_checkout_gap(db, g):
    """把签到时间往回拨过保护窗：现实里这段时间是干活干掉的。

    不拨的话第二扫会被当成手滑挡回来（见 test_签完马上再扫一遍不会被签退）。
    """
    rec = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id).one()
    rec.checkin_at = now() - timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES + 1)
    db.commit()


def test_scan_twice_is_checkin_then_checkout(client, db, live_group):
    """固定码扫一次签到、隔够时间再扫一次签退。

    固定码没被动态码取代：现场还有打印张贴的用法（码旁边写着是哪一场、
    哪个岗位）。它比动态码短一位，两类码在形状上分得开。
    """
    _act, g, h, token = live_group
    assert len(token) == 6 != CHECKIN_CODE_LEN

    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["state"] == "active" and info["enrolled"] is True

    first = client.post(f"/api/checkin/task/{token}", headers=h).json()
    assert first["action"] == "checkin"

    _past_checkout_gap(db, g)
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
def short_codes(monkeypatch):
    """把动态码的寿命调到秒级，用例真等它过期，而不是去删 Redis 里的键——
    键名是实现细节，「一分钟到了」该由过期本身来发生。"""
    def _set(ttl: int, refresh: int):
        monkeypatch.setattr(config, "CHECKIN_CODE_TTL_SECONDS", ttl)
        monkeypatch.setattr(config, "CHECKIN_CODE_REFRESH_SECONDS", refresh)
    return _set


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
    _past_checkout_gap(db, live_group[1])
    assert client.post(f"/api/checkin/task/{code}", headers=h).json()["action"] == "checkout"


def test_动态码一过期就作废(client, admin, live_group, short_codes):
    act, _g, h, _token = live_group
    short_codes(ttl=1, refresh=1)
    tid = task_id_of(client, admin, act)
    code = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                      headers=admin).json()["code"]
    assert client.get(f"/api/checkin/task/{code}", headers=h).status_code == 200

    time.sleep(1.2)                           # 码的寿命过去了
    assert client.get(f"/api/checkin/task/{code}", headers=h).status_code == 404
    assert client.post(f"/api/checkin/task/{code}", headers=h).status_code == 404
    # 画码也得拒：这个口子不用登录，收下任意一串字符就等于让人拿平台的
    # 域名生成一张指向任意路径的码，扫的人无从分辨
    assert client.get(f"/api/checkin/qr.svg?code={code}").status_code == 404


def test_换码不会把正在扫的人挡在外面(client, admin, live_group, short_codes):
    """换码换的是「下次取哪张」，不撤上一张：正在排队扫码的人不该因为
    管理员那一刻恰好取了新码就失败。"""
    act, _g, h, _token = live_group
    short_codes(ttl=10, refresh=1)            # 1 秒换一张，每张活 10 秒
    tid = task_id_of(client, admin, act)
    first = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                       headers=admin).json()["code"]

    time.sleep(1.2)                           # 到了换码的点
    second = client.get(f"/api/staff/checkin-tasks/{tid}/code",
                        headers=admin).json()["code"]
    assert second != first
    assert client.get(f"/api/checkin/task/{first}", headers=h).status_code == 200
    assert client.get(f"/api/checkin/task/{second}", headers=h).status_code == 200


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
    """学生点签到 → 扫到码 → 按钮变「签退」，这一跳靠的就是这几个字段。

    还得当场说得出「还要等几分钟」才能签退：不给这个时间，前端只能放人
    去扫一遍码、被服务端挡回来才知道——而那时候他已经举着手机对着二维码
    转了半天，还以为是自己没扫上。
    """
    _act, g, h, token = live_group
    assert row_of(client, h, g)["checked_in"] is False

    client.post(f"/api/checkin/task/{token}", headers=h)

    row = row_of(client, h, g)
    assert row["checked_in"] is True and row["checkout_at"] is None
    gap = (datetime.fromisoformat(row["checkin"]["checkout_from"])
           - datetime.fromisoformat(row["checkin_at"]))
    assert gap == timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES)

    _past_checkout_gap(db, g)
    client.post(f"/api/checkin/task/{token}", headers=h)

    row = row_of(client, h, g)
    assert row["checkout_at"] is not None        # 签退完按钮就该消失
    # 签退之后这个时间就没意义了，不该再挂着
    assert "checkout_from" not in row["checkin"]


# ======================= 定位签到 =======================
#
# 现场根本没人能出示二维码的岗位（散在校门口、看台、停车场），管理者站在
# 岗位上量一个坐标，志愿者到场点一下，平台比距离。比扫码弱——手机定位能
# 伪造——所以只该用在那类岗位上，规则见 routers/checkin.py。

LAT, LNG = 39.9603, 116.3130          # 人大东门一带，随便取的


def mark_geo(client, admin, act, lat=LAT, lng=LNG, radius=200, accuracy=8):
    """照前端的路径量坐标：先列码表拿 task_id，再 PUT 锚点。"""
    tid = task_id_of(client, admin, act)
    r = client.put(f"/api/staff/checkin-tasks/{tid}/geo", headers=admin,
                   json={"lat": lat, "lng": lng, "accuracy": accuracy,
                         "radius_m": radius})
    assert r.status_code == 200, r.text
    return tid


def geo_post(client, h, enr_id, lat=LAT, lng=LNG, accuracy=10):
    return client.post(f"/api/checkin/enrollments/{enr_id}", headers=h,
                       json={"lat": lat, "lng": lng, "accuracy": accuracy})


def test_没量坐标的场次不能走定位(client, admin, live_group):
    """这条入口不带任何凭据，要是能随便用，扫码那道防线就从侧门没了。"""
    _act, g, h, _token = live_group
    enr = row_of(client, h, g)
    r = geo_post(client, h, enr["id"])
    assert r.status_code == 409 and "扫码签到" in r.json()["detail"]


def test_量了坐标就能定位签到(client, admin, live_group):
    act, g, h, _token = live_group
    tid = mark_geo(client, admin, act)

    assert row_of(client, h, g)["checkin"]["method"] == "geo"
    d = geo_post(client, h, row_of(client, h, g)["id"]).json()
    assert d["action"] == "checkin" and d["distance_m"] < 50

    # 看板上的「方式」要认出这是定位签到的，不能一律写成扫码
    rec = client.get(f"/api/staff/checkin-tasks/{tid}/board",
                     headers=admin).json()["records"][0]
    assert rec["method"] == "geo" and rec["checked_in"]


def test_离签到点远了签不了(client, admin, live_group):
    """纬度差 0.01 度 ≈ 1.1 公里，远超 200 米。"""
    act, g, h, _token = live_group
    mark_geo(client, admin, act)
    r = geo_post(client, h, row_of(client, h, g)["id"], lat=LAT + 0.01)
    assert r.status_code == 409 and "超出签到范围" in r.json()["detail"]


def test_手机定位误差比半径还大就签不了(client, admin, live_group):
    """误差 ±500 米、范围才 200 米：这样的定位证明不了他在现场，
    放过去等于把「到场」变成「报一个大概位置」。"""
    act, g, h, _token = live_group
    mark_geo(client, admin, act)
    r = geo_post(client, h, row_of(client, h, g)["id"], accuracy=500)
    assert r.status_code == 409 and "误差" in r.json()["detail"]


def test_半径之内的误差照签(client, admin, live_group):
    """误差 150 米、范围 200 米，站得也准——一栋楼的定位就是这么晃。"""
    act, g, h, _token = live_group
    mark_geo(client, admin, act)
    d = geo_post(client, h, row_of(client, h, g)["id"], accuracy=150).json()
    assert d["action"] == "checkin"


def test_改成定位之后老码就作废(client, admin, live_group):
    """二维码可能早就打印出去了。码要是还能签，定位那道判定就是摆设——
    谁都会挑松的那条路走。"""
    act, g, h, token = live_group
    assert client.get(f"/api/checkin/qr.svg?token={token}").status_code == 200
    mark_geo(client, admin, act)

    assert client.get(f"/api/checkin/qr.svg?token={token}").status_code == 404
    r = client.post(f"/api/checkin/task/{token}", headers=h)
    assert r.status_code == 409 and "定位签到" in r.json()["detail"]
    # 扫开旧码进来的落地页也要说清楚，而不是等他点按钮再报错
    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["method"] == "geo"


def test_定位签到也守十分钟那道窗(client, admin, live_group):
    """签到规则只写一份（_apply_checkin）就是为了这个：两条入口各写一遍，
    早晚有一侧漏掉保护窗，那侧就成了「当场签退」的后门。"""
    act, g, h, _token = live_group
    mark_geo(client, admin, act)
    enr_id = row_of(client, h, g)["id"]
    assert geo_post(client, h, enr_id).json()["action"] == "checkin"

    again = geo_post(client, h, enr_id)
    assert again.status_code == 409 and "刚签到" in again.json()["detail"]


def test_改回扫码就得扫码(client, admin, live_group):
    act, g, h, token = live_group
    tid = mark_geo(client, admin, act)
    assert client.delete(f"/api/staff/checkin-tasks/{tid}/geo",
                         headers=admin).status_code == 200

    assert row_of(client, h, g)["checkin"]["method"] == "scan"
    assert geo_post(client, h, row_of(client, h, g)["id"]).status_code == 409
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).json()["action"] == "checkin"


def test_坐标取不到时浏览器给的零点会被拒(client, admin, live_group):
    """navigator.geolocation 取不到定位时给的是 (0,0)（几内亚湾）。
    照单全收就成了一个谁都不在范围内的签到点，现场还查不出原因。"""
    act, _g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    r = client.put(f"/api/staff/checkin-tasks/{tid}/geo", headers=admin,
                   json={"lat": 0, "lng": 0, "radius_m": 200})
    assert r.status_code == 400 and "定位" in r.json()["detail"]


def test_半径要填在合理区间(client, admin, live_group):
    act, _g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    url = f"/api/staff/checkin-tasks/{tid}/geo"
    for bad in (5, 99999):
        r = client.put(url, headers=admin,
                       json={"lat": LAT, "lng": LNG, "radius_m": bad})
        assert r.status_code == 400 and "范围" in r.json()["detail"]


def test_不是管理者量不了坐标(client, admin, live_group, take_student):
    """能量坐标就等于能决定「人在哪儿算到场」，只能本活动的管理者来。"""
    act, g, h, _token = live_group
    tid = task_id_of(client, admin, act)
    r = client.put(f"/api/staff/checkin-tasks/{tid}/geo",
                   headers=login(client, take_student()),
                   json={"lat": LAT, "lng": LNG, "radius_m": 200})
    assert r.status_code == 403


def test_只能给自己的报名记录定位签到(client, admin, live_group, take_student):
    """这条入口认的是报名 id。不认人，就等于谁都能替别人签到。"""
    act, g, h, _token = live_group
    mark_geo(client, admin, act)
    enr_id = row_of(client, h, g)["id"]
    other = login(client, take_student())        # 没报这场名的人
    assert geo_post(client, other, enr_id).status_code == 404
    assert row_of(client, h, g)["checked_in"] is False   # 本人没被代签上


# ======================= 地图配置 =======================

def test_地图配置只给管理者(client, admin, live_group, take_student):
    """管理端画签到点用的配置，只给管理者——学生端根本没有要看地图的地方。
    瓦片由本站转发，这里不再下发任何 tk（见 test_geo_tiles.py）。"""
    d = client.get("/api/staff/map-config", headers=admin)
    assert d.status_code == 200 and "tiles" in d.json() and "tk" not in d.json()
    assert client.get("/api/staff/map-config",
                      headers=login(client, take_student())).status_code == 403


def test_坐标的来源会写进审计(client, admin, db, live_group):
    """现场量的 vs 图上点的，在记录里必须分得出来：前者是「人到过现场」的证据，
    后者不是。将来复盘或追责，这一句话就是全部区别。"""
    from backend.models import AuditLog

    act, _g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    r = client.put(f"/api/staff/checkin-tasks/{tid}/geo", headers=admin,
                   json={"lat": LAT, "lng": LNG, "radius_m": 200, "source": "map"})
    assert r.status_code == 200, r.text

    row = (db.query(AuditLog).filter(AuditLog.target_id == tid)
           .order_by(AuditLog.created_at.desc()).first())
    assert "地图上点" in row.summary

    # 不传 source = 现场量的（老前端、以及「使用我的当前位置」那条路）
    client.put(f"/api/staff/checkin-tasks/{tid}/geo", headers=admin,
               json={"lat": LAT, "lng": LNG, "radius_m": 200})
    row = (db.query(AuditLog).filter(AuditLog.target_id == tid)
           .order_by(AuditLog.created_at.desc()).first())
    assert "现场量" in row.summary


def test_校区预设点也留痕(client, admin, db, live_group):
    """按校区快捷选点比地图点选更粗——那只是校区中心，多半不是岗位位置。
    审计里要写明白，别让它看起来像现场量的。"""
    from backend.models import AuditLog

    act, _g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    client.put(f"/api/staff/checkin-tasks/{tid}/geo", headers=admin,
               json={"lat": LAT, "lng": LNG, "radius_m": 200, "source": "campus"})
    row = (db.query(AuditLog).filter(AuditLog.target_id == tid)
           .order_by(AuditLog.created_at.desc()).first())
    assert "校区预设点" in row.summary and "未经现场核对" in row.summary


def test_地图配置带上校区预设点(client, admin):
    """坐标放后端：改一个校区的位置不该要重新构建前端。"""
    d = client.get("/api/staff/map-config", headers=admin).json()
    names = [c["name"] for c in d["campuses"]]
    assert names == ["中关村校区", "通州校区"]
    assert all(isinstance(c["lat"], float) and isinstance(c["lng"], float)
               for c in d["campuses"])


# ======================= 地名搜索 =======================

def test_地名搜索要登录且要是管理者(client, live_group, take_student):
    """搜索走的是服务端 tk（配额算我们的），不能谁都能调。"""
    assert client.get("/api/staff/geo/search?q=明德楼").status_code == 401
    assert client.get("/api/staff/geo/search?q=明德楼",
                      headers=login(client, take_student())).status_code == 403


def test_地名搜索至少两个字(client, admin, live_group):
    """一个字能搜出一堆无关的，白费配额也白让人挑。"""
    r = client.get("/api/staff/geo/search?q=楼", headers=admin)
    assert r.status_code == 400 and "两个字" in r.json()["detail"]


def test_没配服务端tk时说清楚是没配(client, admin, live_group, monkeypatch):
    """「没配」和「没搜到」是两回事：不说清楚，人会一直换关键词试。

    测试环境故意不注入这类密钥（见 docker-compose.yml 的注释），所以这里
    本来就是没配的状态——正好拿它当断言。
    """
    from backend import config
    monkeypatch.setattr(config, "TIANDITU_TK_SERVER", "")
    d = client.get("/api/staff/geo/search?q=明德楼", headers=admin).json()
    assert d == {"configured": False, "items": []}


def test_分岗位的应到岗是分到的人数不是名额(client, admin, db, live_group):
    """名额 5、只排了 1 个人时，卡片不该写「0/5 到岗」——那是名额没招满，
    不是缺勤。分母要按真分到这个岗位的人数算，名额另说。"""
    act, g, h, token = live_group

    d = client.get(f"/api/staff/shifts/{g.id}/board", headers=admin).json()
    card = d["positions"][0]
    assert card["quota"] == 5          # 名额还是 5，但那是招募的事
    assert card["expected"] == 1       # 分到这个岗位的就 1 个人
    assert card["checked"] == 0

    client.post(f"/api/checkin/task/{token}", headers=h)
    card = client.get(f"/api/staff/shifts/{g.id}/board",
                      headers=admin).json()["positions"][0]
    assert card["checked"] == 1 and card["expected"] == 1


def test_请假的人不算进岗位的应到岗(client, admin, db, live_group):
    """场次总数一直是这么算的（请假的人不进分母），岗位卡片跟着一致。"""
    act, g, h, token = live_group
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id).one()
    enr.excused = True
    db.commit()

    d = client.get(f"/api/staff/shifts/{g.id}/board", headers=admin).json()
    assert d["expected"] == 0 and d["excused"] == 1
    assert d["positions"][0]["expected"] == 0


def test_同一场次同时只能有一张开着的码(client, admin, db, live_group):
    """两个都开着，学生扫哪张、签到记录落到哪张任务下都说不清，
    看板上的「已签」还会各算各的。

    **整场的码算「所有岗位」**：整场开着时不给某个岗位再开一张，反过来也一样。
    """
    act, g, _h, _token = live_group        # live_group 已经建了一个统一码
    pos = db.query(Position).filter(Position.shift_id == g.id).first()

    # 整场开着 → 整场的、分岗位的都建不了
    for body in ({"mode": "shift"},
                 {"mode": "position", "position_id": pos.id}):
        r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                        headers=admin, json=body)
        assert r.status_code == 409, r.text
        assert "已经有开启的签到码" in r.json()["detail"]

    # 关掉它，就能建分岗位的
    tid = task_id_of(client, admin, act)
    assert client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                        json={"active": False}).status_code == 200
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_id": pos.id})
    assert r.status_code == 200, r.text

    # 分岗位的开着时，整场的也建不了（整场 = 所有岗位）
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                    headers=admin, json={"mode": "shift"})
    assert r.status_code == 409, r.text


def test_重新开启也要查冲突(client, admin, db, live_group):
    """关掉一张、再打开另一张，很容易凑出两张同时开着的——这条路上原来没有
    任何检查（把已生成的码再点开启，就出现两张）。"""
    act, g, _h, _token = live_group
    live_id = task_id_of(client, admin, act)          # 开着的那张
    # 再建一张同场次同岗位的（先关掉才建得了）
    client.patch(f"/api/staff/checkin-tasks/{live_id}", headers=admin,
                 json={"active": False})
    other = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                        headers=admin, json={"mode": "shift"}).json()["id"]

    # 现在 other 开着，把先前那张重新打开 → 必须挡住
    r = client.patch(f"/api/staff/checkin-tasks/{live_id}", headers=admin,
                     json={"active": True})
    assert r.status_code == 409 and "已经有开启的签到码" in r.json()["detail"]
    still = [t for t in client.get(f"/api/staff/activities/{act.id}/checkin-tasks",
                                   headers=admin).json() if t["active"]]
    assert len(still) == 1, "不能出现两张同时开着的"


def test_分岗位可以一次建好几个(client, admin, db, live_group):
    """一个岗位一张码：选了三个就建三张，时段都一样。"""
    act, g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                 json={"active": False})
    db.add_all([Position(id=uid("p"), shift_id=g.id, name=f"岗位{i}",
                         location="x", quota=3, sort=i) for i in (1, 2)])
    db.commit()
    ids = [p.id for p in db.query(Position).filter(Position.shift_id == g.id).all()]
    d = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_ids": ids}).json()
    assert len(d["tasks"]) == len(ids)

    # 其中任何一个岗位再建一张，还是被挡（同一岗位）
    r = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_ids": [ids[0]]})
    assert r.status_code == 409


def test_改可签时段(client, admin, db, live_group):
    """场次提前或拖堂时改时段用——不必关掉重建（重建会让已扫过的码作废）。"""
    act, g, _h, _token = live_group
    tid = task_id_of(client, admin, act)
    f = (g.start_time - timedelta(hours=2)).isoformat()
    t = (g.end_time + timedelta(hours=2)).isoformat()

    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"valid_from": f, "valid_to": t})
    assert r.status_code == 200, r.text
    assert client.get(f"/api/checkin/task/{_token}", headers=_h).json()["state"] == "active"

    bad = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                       json={"valid_to": f})
    assert bad.status_code == 400 and "晚于" in bad.json()["detail"]


def test_非本场次的人不该被查出姓名(client, admin, db, live_group, take_student):
    """批量补签的提示里回过姓名 = 输个学号就能查出他叫什么。
    学号是他自己填进来的，还给他；名字不该给。"""
    act, _g, _h, _token = live_group
    sid = take_student()
    u = student_row(db, sid)
    d = client.post(f"/api/staff/checkin-tasks/{task_id_of(client, admin, act)}/batch",
                    headers=admin,
                    json={"action": "checkin", "student_ids": [sid]}).json()
    assert d["done"] == 0 and len(d["skipped"]) == 1
    assert sid in d["skipped"][0]
    assert u.name not in d["skipped"][0]


def test_多选岗位里重复的只开一张码(client, admin, two_shift_act):
    _act, am, _train = two_shift_act
    pos_id = client.get(f"/api/staff/shifts/{am.id}/roster",
                        headers=admin).json()["positions"][0]["id"]
    r = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                    json={"mode": "position", "position_ids": [pos_id, pos_id]})
    assert r.status_code == 200 and len(r.json()["tasks"]) == 1


def test_改时段不能留空(client, admin, two_shift_act):
    """截止同时是签退截止：过了它没签退的人按场次结束兜底，「不限」就判不出来。
    开始也一并必填，和生成时总会填上起止一致。以前允许置空，这里改成 400。"""
    _act, am, _train = two_shift_act
    tid = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                      json={"mode": "shift"}).json()["id"]
    for body in ({"valid_to": None}, {"valid_to": ""}, {"valid_from": None},
                 {"valid_from": "  "}):
        r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                         json=body)
        assert r.status_code == 400, body
    row = client.get(f"/api/staff/activities/{_act.id}/checkin-tasks",
                     headers=admin).json()[0]
    assert row["valid_from"] and row["valid_to"]          # 没被改掉
    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"valid_to": "明天"})
    assert r.status_code == 400


# ---------- 签退只在码的可签时段内；过了/关了按场次结束兜底 ----------

def _vol(db, g) -> User:
    """live_group 里那名志愿者。"""
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id).one()
    return db.get(User, enr.user_id)


def _hours_row(client, admin, act, user_id):
    items = client.get(f"/api/staff/activities/{act.id}/hours",
                       headers=admin).json()["items"]
    return next(i for i in items if i["user"]["id"] == user_id)


def _expire(client, admin, tid):
    """用改时段的接口把码的截止拨到刚刚过去。"""
    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"valid_from": (now() - timedelta(hours=2)).isoformat(),
                           "valid_to": (now() - timedelta(minutes=1)).isoformat()})
    assert r.status_code == 200, r.text


def test_码还开着时没签退时长待定(client, admin, db, live_group):
    act, g, h, token = live_group
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).json()["action"] == "checkin"
    me = _vol(db, g).id
    row = _hours_row(client, admin, act, me)
    assert row["pending"] is True and row["auto_checkout"] is False


def test_码过期后没签退按场次结束记并待核(client, admin, db, live_group):
    """截止早于场次结束也是这样：码一过期，人就签不了退了，按场次结束记。"""
    act, g, h, token = live_group
    client.post(f"/api/checkin/task/{token}", headers=h)
    me = _vol(db, g).id
    _expire(client, admin, task_id_of(client, admin, act))

    row = _hours_row(client, admin, act, me)
    assert row["auto_checkout"] is True and row["pending"] is False
    # 明细的时段终点就是场次结束
    assert row["parts"][0]["time"].endswith(f"{g.end_time:%H:%M}")

    # 待核的不进「确认全部」
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm",
                    json={}, headers=admin)
    assert r.status_code == 400 and "待核" in r.json()["detail"]


def test_码过期后不能自己签退(client, admin, db, live_group):
    act, g, h, token = live_group
    client.post(f"/api/checkin/task/{token}", headers=h)
    _past_checkout_gap(db, g)
    tid = task_id_of(client, admin, act)
    _expire(client, admin, tid)

    r = client.post(f"/api/checkin/task/{token}", headers=h)
    assert r.status_code == 409 and "已截止" in r.json()["detail"]
    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    assert info["state"] == "expired" and info["checked_out"] is False
    # 页面上的签退截止就是码的截止，不再是「场次结束 + 宽限」
    row = client.get(f"/api/staff/activities/{act.id}/checkin-tasks",
                     headers=admin).json()[0]
    assert info["checkout_deadline"] == row["valid_to"]

    # 管理者手动签退不受码的时段限制
    vol = _vol(db, g)
    d = client.post(f"/api/staff/checkin-tasks/{tid}/batch", headers=admin,
                    json={"action": "checkout",
                          "student_ids": [vol.student_id]}).json()
    assert d["done"] == 1
    row = _hours_row(client, admin, act, vol.id)
    assert row["pending"] is False and row["auto_checkout"] is False


def test_码没过期时的签退截止就是码的截止(client, admin, live_group):
    act, g, h, token = live_group
    info = client.get(f"/api/checkin/task/{token}", headers=h).json()
    pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
    assert info["checkout_deadline"] == (g.end_time + pad).isoformat(
        timespec="minutes")


def test_场次中途关码也按场次结束兜底_重开又回到待定(client, admin, db, live_group):
    act, g, h, token = live_group
    client.post(f"/api/checkin/task/{token}", headers=h)
    _past_checkout_gap(db, g)
    me = _vol(db, g).id
    tid = task_id_of(client, admin, act)

    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"active": False})
    assert r.status_code == 200
    row = _hours_row(client, admin, act, me)
    assert row["auto_checkout"] is True and row["pending"] is False
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).status_code == 409

    # 关了还能再开（没过截止）：人又能自己签退了，时长回到待定
    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"active": True})
    assert r.status_code == 200
    row = _hours_row(client, admin, act, me)
    assert row["pending"] is True and row["auto_checkout"] is False
    assert client.post(f"/api/checkin/task/{token}",
                       headers=h).json()["action"] == "checkout"


def test_签到那张码关了但同场另开了新码仍算待定(client, admin, db, live_group):
    """签退按「人 + 场次」找记录，扫新码照样能签退——这时不该先兜底。"""
    act, g, h, token = live_group
    client.post(f"/api/checkin/task/{token}", headers=h)
    me = _vol(db, g).id
    tid = task_id_of(client, admin, act)
    client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                 json={"active": False})
    new = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks", headers=admin,
                      json={"mode": "shift"})
    assert new.status_code == 200, new.text
    row = _hours_row(client, admin, act, me)
    assert row["pending"] is True and row["auto_checkout"] is False

    _past_checkout_gap(db, g)
    assert client.post(f"/api/checkin/task/{new.json()['token']}",
                       headers=h).json()["action"] == "checkout"
