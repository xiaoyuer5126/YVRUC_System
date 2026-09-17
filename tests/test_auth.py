"""登录、会话与令牌传递方式。"""
from __future__ import annotations

from datetime import timedelta

from backend.models import Session, User, now
from conftest import ADMIN, DEMO_PASSWORD, login


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
    db.expunge_all()                                  # 绕开身份映射缓存
    assert db.get(Session, token) is None             # 过期会话顺手清掉


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


# ======================= 证件号豁免 =======================

def test_港澳台留学生可免填身份证号(client, db, take_student):
    """勾选免填后身份证号留空即可，资料照样算完善。"""
    sid = take_student()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000001", "wechat": "wx_hk",
                         "no_mainland_id": True})
    assert r.status_code == 200
    user = r.json()["user"]
    assert user["profile_complete"] is True
    assert user["no_mainland_id"] is True

    u = db.query(User).filter(User.student_id == sid).one()
    assert u.id_card == ""          # 免填就不该留下半个号


def test_首次填写时身份证号必填且校验末位(client, db, take_student):
    from backend.models import User
    sid = take_student()
    db.query(User).filter(User.student_id == sid).one().id_card = ""
    db.commit()
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000004", "wechat": "wx_z",
                         "id_card": "110101200001011111"})
    assert r.status_code == 400


def test_修改资料时留空不会清掉已填的身份证号(client, db, take_student):
    """前端只显示脱敏值，留空提交意味着「不改这一项」。"""
    from backend.models import User
    sid = take_student()
    before = db.query(User).filter(User.student_id == sid).one().id_card
    assert before
    t = login(client, sid)
    r = client.put("/api/me/profile", headers=t,
                   json={"phone": "13800000009", "wechat": "wx_new",
                         "id_card": ""})
    assert r.status_code == 200
    assert r.json()["user"]["id_card_masked"].endswith(before[-3:])
    db.expire_all()
    assert db.query(User).filter(User.student_id == sid).one().id_card == before


def test_下载票据只能用一次且认准那个网址(client, admin, db, enrolled_activity):
    """导出是 <a> 直链，带不了请求头。票据代替会话令牌：
    绑定这一个网址、用一次即作废，捡到也换不出别的东西。"""
    act, _shift, _u = enrolled_activity
    path = f"/api/staff/activities/{act.id}/export.xlsx"

    r = client.post("/api/auth/download-ticket", headers=admin, json={"path": path})
    assert r.status_code == 200, r.text
    ticket = r.json()["ticket"]

    assert client.get(f"{path}?ticket={ticket}").status_code == 200
    # 第二次就作废了
    assert client.get(f"{path}?ticket={ticket}").status_code == 401
    # 换一张票去别的网址，对不上
    other = client.post("/api/auth/download-ticket", headers=admin,
                        json={"path": path}).json()["ticket"]
    assert client.get(
        f"/api/staff/activities/{act.id}/hours.xlsx?ticket={other}").status_code == 401


