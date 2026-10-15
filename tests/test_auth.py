"""登录、会话与令牌传递方式。"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest

from backend import locks
from backend.models import Session, User, now
from conftest import ADMIN, DEMO_PASSWORD, dl_url, login


def test_login_requires_password(client):
    """没有口令拿不到令牌——这是公网上唯一的入口。"""
    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN,
                                                  "password": ""})
    assert r.status_code == 401

    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN,
                                                  "password": "wrong"})
    assert r.status_code == 401

    r = client.post("/api/auth/demo-login", json={"student_id": ADMIN})
    assert r.status_code == 422      # 少字段直接被模型拦下


def test_login_succeeds_with_password(client):
    r = client.post("/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200
    assert "admin" in r.json()["user"]["roles"]


def test_expired_session_rejected(client, db):
    headers = login(client, ADMIN)
    token = headers["Authorization"].removeprefix("Bearer ")

    row = db.get(Session, token)
    assert row.expires_at > now()                     # 默认还没过期
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()

    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_会话令牌不能走地址栏(client, admin):
    """令牌走 URL 就会漏进访问日志、浏览器历史与 Referer，一处都不认。"""
    token = admin["Authorization"].removeprefix("Bearer ")

    assert client.get(f"/api/auth/me?token={token}").status_code == 401

    acts = client.get("/api/staff/activities", headers=admin).json()["items"]
    r = client.get(f"/api/staff/activities/{acts[0]['id']}/export.xlsx"
                   f"?token={token}")
    assert r.status_code == 401


def test_logout_only_kills_current_session(client):
    """在手机上退出，不该把电脑上的登录一起顶掉。"""
    phone = login(client, ADMIN)
    laptop = login(client, ADMIN)

    assert client.post("/api/auth/logout", headers=phone).status_code == 200
    assert client.get("/api/auth/me", headers=phone).status_code == 401
    assert client.get("/api/auth/me", headers=laptop).status_code == 200


# ======================= 校区 =======================
#
# 学校接口里没有校区（2026-09-22 与学校确认），只能本人到「个人资料」选。
# 它同时是招募范围的筛选项，所以是必填——留空的人会在「限校区」的活动上被
# 静默挡掉，还会显示「校区需为…（您是 未填写）」。

def test_校区不在册就拒(client, db, take_student):
    from backend.models import User
    sid = take_student()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000002", "wechat": "wx_c",
                         "campus": "火星校区", "no_mainland_id": True})
    assert r.status_code == 400 and r.json()["detail"] == "请选择所在校区"
    u = db.query(User).filter(User.student_id == sid).one()
    assert u.campus == "中关村校区"      # 被拒的请求不该改掉任何东西


def test_没选校区就不算资料完善(client, db, take_student):
    """校区是 2026-09-22 之后才加的必填项，此前建的档案都是空的。

    这些人下次报名会被拦下并弹出表单——正是要的效果：一次性补齐，
    而不是让他们在某个「限校区」的活动上对着拦截提示发愣。
    """
    from backend.models import User
    sid = take_student()
    db.query(User).filter(User.student_id == sid).one().campus = ""
    db.commit()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000003", "wechat": "wx_c",
                         "no_mainland_id": True})
    assert r.status_code == 400 and r.json()["detail"] == "请选择所在校区"
    me = client.get("/api/auth/me", headers=t).json()["user"]
    assert me["profile_complete"] is False


# ======================= 证件号豁免 =======================

def test_港澳台留学生可免填身份证号(client, db, take_student):
    """勾选免填后身份证号留空即可，资料照样算完善。"""
    sid = take_student()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000001", "wechat": "wx_hk",
                         "campus": "通州校区", "no_mainland_id": True})
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["profile_complete"] is True
    assert user["no_mainland_id"] is True

    u = db.query(User).filter(User.student_id == sid).one()
    assert u.id_card == ""          # 免填就不该留下半个号


@pytest.mark.parametrize("id_card, reason", [
    ("", "请填写身份证号"),                        # 首次填写不能留空
    ("110101200001011111", "身份证号不合法"),      # 末位校验码不对
])
def test_首次填写时身份证号必填且校验末位(client, db, take_student, id_card,
                                          reason):
    from backend.models import User
    sid = take_student()
    db.query(User).filter(User.student_id == sid).one().id_card = ""
    db.commit()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000004", "wechat": "wx_z",
                         "campus": "中关村校区", "id_card": id_card})
    assert r.status_code == 400 and r.json()["detail"].startswith(reason)
    me = client.get("/api/auth/me", headers=t).json()["user"]
    assert me["profile_complete"] is False


def test_修改资料时留空不会清掉已填的身份证号(client, db, take_student):
    """前端只显示脱敏值，留空提交意味着「不改这一项」。"""
    from backend.models import User
    sid = take_student()
    before = db.query(User).filter(User.student_id == sid).one().id_card
    assert before
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000009", "wechat": "wx_new",
                         "campus": "中关村校区", "id_card": ""})
    assert r.status_code == 200
    assert r.json()["user"]["id_card_masked"].endswith(before[-3:])
    db.expire_all()
    assert db.query(User).filter(User.student_id == sid).one().id_card == before


def test_下载票据有效期内可重复兑换且认准那个网址(client, admin, db,
                                          enrolled_activity):
    """导出是 <a> 直链，带不了请求头。票据代替会话令牌：绑定这一个网址、
    一分钟有效。同一个地址可能被请求两次（微信先拦一下，用户点「在浏览器中
    打开」后浏览器才真正发请求），有效期内两次都认。"""
    act, _shift, _u = enrolled_activity
    path = f"/api/staff/activities/{act.id}/export.xlsx"

    r = client.post("/api/auth/download-ticket", headers=admin, json={"path": path})
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]

    assert client.get(f"{path}?ticket={ticket}").status_code == 200
    # 同一张票再兑换一次，照样进得去
    assert client.get(f"{path}?ticket={ticket}").status_code == 200
    # 换一张票去别的网址，对不上
    other = client.post("/api/auth/download-ticket", headers=admin,
                        json={"path": path}).json()["ticket"]
    hours = f"/api/staff/activities/{act.id}/hours.xlsx"
    assert client.get(f"{hours}?ticket={other}").status_code == 401
    # 对照：给那个网址开的票就进得去——上面的 401 是票对不上，不是网址本身不通
    assert client.get(dl_url(client, admin, hours)).status_code == 200


def test_下载票据过了有效期就作废(client, admin, db, enrolled_activity):
    """有效期是票据本身的 TTL，不靠兑换时删掉：一分钟一到，票自己没了。"""
    act, _shift, _u = enrolled_activity
    path = f"/api/staff/activities/{act.id}/export.xlsx"
    user = db.query(User).filter(User.student_id == ADMIN).one()
    ticket = locks.issue_ticket(user.id, path, 1)

    assert client.get(f"{path}?ticket={ticket}").status_code == 200
    time.sleep(1.2)
    assert client.get(f"{path}?ticket={ticket}").status_code == 401


def test_过期会话会被定时任务清掉(client, db):
    from backend.scheduled import purge_expired_sessions

    tok = login(client, ADMIN)["Authorization"].split()[1]
    row = db.get(Session, tok)
    row.expires_at = now() - timedelta(minutes=1)
    db.commit()

    assert purge_expired_sessions(db) >= 1
    db.expire_all()          # 删除走的是批量语句，本会话的缓存不会自己知道
    assert db.get(Session, tok) is None
