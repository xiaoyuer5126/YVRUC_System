"""登录、会话 cookie、跨站请求防护与令牌传递方式。"""
from __future__ import annotations

import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend import locks
from backend.deps import session_key
from backend.models import Session, User, now
from backend.session_cookie import SESSION_COOKIE
from conftest import ADMIN, CSRF, DEMO_PASSWORD, dl_url, login, session_token


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
    token = session_token(headers)

    row = db.get(Session, session_key(token))
    assert row.expires_at > now()                     # 默认还没过期
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()

    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_会话3天有效且用一次顺延一次(client, db):
    """滑动有效期：新登录 3 天后到期；用过之后从使用那一刻再算 3 天；
    一小时内反复用不重复写库。"""
    headers = login(client, ADMIN)
    token = session_token(headers)
    row = db.get(Session, session_key(token))
    left = row.expires_at - now()
    assert timedelta(days=3) - timedelta(minutes=1) < left <= timedelta(days=3)

    # 刚续过（离满 3 天不到一小时）：不动
    fresh = now() + timedelta(days=3) - timedelta(minutes=30)
    row.expires_at = fresh
    db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    db.expire_all()
    assert db.get(Session, session_key(token)).expires_at == fresh

    # 上次续期已过两天：用一次就续回整 3 天
    row = db.get(Session, session_key(token))
    row.expires_at = now() + timedelta(days=1)
    db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 200
    db.expire_all()
    left = db.get(Session, session_key(token)).expires_at - now()
    assert left > timedelta(days=3) - timedelta(minutes=1)

    # 3 天没用：到期即失效，记录一并删掉
    row = db.get(Session, session_key(token))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401
    db.expire_all()
    assert db.get(Session, session_key(token)) is None


def test_会话令牌不能走地址栏(client, admin):
    """令牌走 URL 就会漏进访问日志、浏览器历史与 Referer，一处都不认。"""
    token = session_token(admin)

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


# ======================= 会话 cookie 与跨站请求防护 =======================

def _set_cookie(r) -> str:
    return "; ".join(v for k, v in r.headers.multi_items()
                     if k.lower() == "set-cookie" and v.startswith(SESSION_COOKIE + "="))


def test_登录下发HttpOnly_cookie且响应体里没有令牌(client):
    """令牌只在 HttpOnly cookie 里：页面脚本读不到，注入的脚本也偷不走。"""
    r = client.post("/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    assert "token" not in r.json()
    cookie = _set_cookie(r).lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert "path=/api" in cookie and "max-age=259200" in cookie   # 与会话 3 天一致
    # 浏览器存下的 cookie 自己就能用，不必前端再塞任何东西
    me = client.get("/api/auth/me")
    assert me.status_code == 200 and me.json()["user"]["student_id"] == ADMIN
    client.cookies.delete(SESSION_COOKIE)


def test_https请求下发的cookie带Secure(client):
    r = client.post("https://testserver/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    assert "secure" in _set_cookie(r).lower()
    client.cookies.delete(SESSION_COOKIE)


def test_站点是https时cookie一律带Secure(client, monkeypatch):
    """反代漏传 X-Forwarded-Proto 时后端看到的是 http；站点本身是 https，
    令牌 cookie 照样要带 Secure，不然浏览器会在 http 请求上明文发出它。"""
    from backend import config
    monkeypatch.setattr(config, "SITE_URL", "https://v.example.edu.cn")
    r = client.post("http://testserver/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    assert "secure" in _set_cookie(r).lower()
    r = client.post("http://testserver/api/auth/logout")
    assert "secure" in _set_cookie(r).lower()
    client.cookies.delete(SESSION_COOKIE)


def test_本机http调试时cookie不带Secure(client, monkeypatch):
    """站点地址是 http 或没配：浏览器不存 Secure cookie，带了就登不进去。"""
    from backend import config
    monkeypatch.setattr(config, "SITE_URL", "")
    r = client.post("http://testserver/api/auth/demo-login",
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    assert "secure" not in _set_cookie(r).lower()
    client.cookies.delete(SESSION_COOKIE)


def test_会话表里不存令牌原文(client, db, admin):
    """库和备份落到别人手里，拿表里的值当 cookie 也登不进去。"""
    token = session_token(admin)
    db.expire_all()
    assert db.get(Session, token) is None
    row = db.get(Session, session_key(token))
    assert row is not None
    stolen = {"Cookie": f"{SESSION_COOKIE}={row.token}"}
    assert client.get("/api/auth/me", headers=stolen).status_code == 401
    assert client.get("/api/auth/me", headers=admin).status_code == 200


def test_同一浏览器重新登录会作废旧会话(client, db):
    """公用电脑上换人登录：上一位的会话不能留在库里继续有效。"""
    first = login(client, ADMIN)
    r = client.post("/api/auth/demo-login", headers=first,
                    json={"student_id": ADMIN, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    second = r.cookies[SESSION_COOKIE]
    client.cookies.delete(SESSION_COOKIE)
    db.expire_all()
    assert db.get(Session, session_key(session_token(first))) is None
    assert client.get("/api/auth/me", headers=first).status_code == 401
    assert client.get("/api/auth/me", headers={
        "Cookie": f"{SESSION_COOKIE}={second}"}).status_code == 200


def test_Authorization头不再认(client, admin):
    """令牌只从 cookie 读：拿到令牌的脚本也没法换个头冒充登录。"""
    bearer = {"Authorization": f"Bearer {session_token(admin)}"}
    assert client.get("/api/auth/me", headers=bearer).status_code == 401


def test_写请求缺X_YVRUC头一律拒绝(client, admin):
    """cookie 会被浏览器自动带上，别的网站诱导发来的写请求加不上自定义头。"""
    cookie = {"Cookie": admin["Cookie"]}
    acts = client.get("/api/staff/activities", headers=admin).json()["items"]
    with TestClient(client.app) as bare:          # 不带默认防跨站头的客户端
        # 读请求不要这个头
        assert bare.get("/api/auth/me", headers=cookie).status_code == 200
        # 写请求缺头：带着有效 cookie 也进不去，四种方法都一样
        for method, path in (("POST", "/api/auth/download-ticket"),
                             ("PUT", "/api/me/profile"),
                             ("PATCH", f"/api/staff/activities/{acts[0]['id']}"),
                             ("DELETE", f"/api/staff/activities/{acts[0]['id']}")):
            r = bare.request(method, path, headers=cookie, json={})
            assert r.status_code == 403, (method, path, r.text)
        # 登录接口同样要这个头：挡住跨站替人登录
        r = bare.post("/api/auth/demo-login",
                      json={"student_id": ADMIN, "password": DEMO_PASSWORD})
        assert r.status_code == 403
        # 对照：带上头就照常
        r = bare.post("/api/auth/download-ticket", headers={**cookie, **CSRF},
                      json={"path": "/api/staff/roles.xlsx"})
        assert r.status_code == 200, r.text


def test_退出登录清cookie且旧cookie失效(client, db):
    headers = login(client, ADMIN)
    token = session_token(headers)
    r = client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200
    cleared = _set_cookie(r).lower()
    assert 'max-age=0' in cleared and "path=/api" in cleared
    db.expire_all()
    assert db.get(Session, session_key(token)) is None
    assert client.get("/api/auth/me", headers=headers).status_code == 401


def test_会话过期后退出照样清cookie(client, db):
    """过期了点退出不该回 401 把人当「登录过期」再踢一遍，cookie 也得清掉。"""
    headers = login(client, ADMIN)
    row = db.get(Session, session_key(session_token(headers)))
    row.expires_at = now() - timedelta(seconds=1)
    db.commit()
    r = client.post("/api/auth/logout", headers=headers)
    assert r.status_code == 200
    assert "max-age=0" in _set_cookie(r).lower()
    # 根本没登录过的也一样
    r = client.post("/api/auth/logout")
    assert r.status_code == 200


def test_会话续期时同步刷新cookie(client, db):
    """服务端顺延了 3 天，cookie 也要跟着顺延，不然用着用着被浏览器丢掉。"""
    headers = login(client, ADMIN)
    token = session_token(headers)
    # 刚登录：不续期，不重发 cookie
    assert _set_cookie(client.get("/api/auth/me", headers=headers)) == ""
    row = db.get(Session, session_key(token))
    row.expires_at = now() + timedelta(days=1)
    db.commit()
    r = client.get("/api/auth/me", headers=headers)
    assert r.status_code == 200
    cookie = _set_cookie(r).lower()
    assert f"{SESSION_COOKIE}={token}".lower() in cookie and "max-age=259200" in cookie
    client.cookies.delete(SESSION_COOKIE)


def test_直链下载不凭cookie放行(client, admin):
    """<a> 直链带不了 X-Identity，凭 cookie 放行就按全部身份判权；别的网站放个
    导出链接，用户一点也会真导出一份。没票、没 X-YVRUC 头的一律 401。"""
    path = "/api/staff/roles.xlsx"
    with TestClient(client.app) as bare:          # 不带默认防跨站头的客户端
        assert bare.get(path, headers={"Cookie": admin["Cookie"]}).status_code == 401
        # 前端自己 fetch 的（带 X-YVRUC）照常认 cookie
        assert bare.get(path, headers=admin).status_code == 200
        # 票据照常能用，换到没有 cookie 的浏览器里也一样（微信转系统浏览器）
        assert bare.get(dl_url(client, admin, path)).status_code == 200


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

    tok = session_token(login(client, ADMIN))
    row = db.get(Session, session_key(tok))
    row.expires_at = now() - timedelta(minutes=1)
    db.commit()

    assert purge_expired_sessions(db) >= 1
    db.expire_all()          # 删除走的是批量语句，本会话的缓存不会自己知道
    assert db.get(Session, session_key(tok)) is None
