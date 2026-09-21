"""二维码：签到码、服装领/还码、活动码，编的都得是**完整网址**。

编相对路径（/#/checkin/AB12CD）时码本身是好的，但手机相机不会跳转，只把
这行字显示出来——签到码与服装码此前就是这样，扫了进不了网站。
拿 qr_svg(期望网址) 跟接口输出比：一模一样才算编对了。
"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import config
from backend.models import (Activity, ActivityManager, Position, Shift, User,
                            now, uid)
from backend.qrcodes import qr_svg
from conftest import ADMIN, POSITION_TEXT

# TestClient 的默认 base_url；不传 base 时后端按请求头兜底出来的就是它
FALLBACK = "http://testserver"
PUBLIC = "https://v.ruc.edu.cn"


@pytest.fixture
def qr_activity(client, admin, db):
    """一个已发布的活动 + 一个场次，用来取三种 token。"""
    admin_user = db.query(User).filter(User.student_id == ADMIN).one()
    act = Activity(id=uid("act"), title=f"二维码测试 {uid('t')}",
                   intro="x", location="世纪馆", organizer="校青协",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   status="published", reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=2), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    g = Shift(id=uid("g"), activity_id=act.id, name="当日",
              start_time=now() + timedelta(days=3),
              end_time=now() + timedelta(days=3, hours=4), mode="pick", sort=0)
    db.add(g)
    db.flush()
    db.add(Position(id=uid("p"), shift_id=g.id, name="引导", quota=5,
                    attire="issued", attire_note="北门物资桌领取", sort=0, **POSITION_TEXT))
    db.commit()
    return act, g


def test_签到码编的是完整网址(client, admin, qr_activity):
    _act, g = qr_activity
    token = client.post(f"/api/staff/shifts/{g.id}/checkin-tasks",
                        json={"mode": "shift"}, headers=admin).json()["token"]

    r = client.get(f"/api/checkin/qr.svg?token={token}&base={PUBLIC}")
    assert r.status_code == 200
    assert r.text == qr_svg(f"{PUBLIC}/#/checkin/{token}")

    # 前端漏传 base 也不能退化成相对路径
    r = client.get(f"/api/checkin/qr.svg?token={token}")
    assert r.text == qr_svg(f"{FALLBACK}/#/checkin/{token}")


def test_服装领还码编的是完整网址(client, admin, qr_activity):
    act, _g = qr_activity
    tasks = client.post(f"/api/staff/activities/{act.id}/attire-tasks",
                        json={}, headers=admin).json()
    take, back = tasks["take"]["token"], tasks["return"]["token"]
    assert take != back                       # 领取与归还是两张不同的码

    for token in (take, back):
        r = client.get(f"/api/attire/qr.svg?token={token}&base={PUBLIC}")
        assert r.status_code == 200
        assert r.text == qr_svg(f"{PUBLIC}/#/attire/{token}")
        r = client.get(f"/api/attire/qr.svg?token={token}")
        assert r.text == qr_svg(f"{FALLBACK}/#/attire/{token}")


def test_活动码编的是完整网址(client, qr_activity):
    act, _g = qr_activity
    r = client.get(f"/api/activities/{act.id}/qr.svg?base={PUBLIC}")
    assert r.status_code == 200
    assert r.text == qr_svg(f"{PUBLIC}/#/activity/{act.id}")
    r = client.get(f"/api/activities/{act.id}/qr.svg")
    assert r.text == qr_svg(f"{FALLBACK}/#/activity/{act.id}")


def test_base_末尾多个斜杠也不会拼出双斜杠(client, qr_activity):
    act, _g = qr_activity
    r = client.get(f"/api/activities/{act.id}/qr.svg?base={PUBLIC}/")
    assert r.text == qr_svg(f"{PUBLIC}/#/activity/{act.id}")


def test_二维码不接受别人家的域名(client, admin, qr_activity):
    """这个接口不需要登录。收下任意 base，就等于让任何人拿平台的地址
    生成一张指向自家站点的码，贴出去扫的人无从分辨。"""
    act, _g = qr_activity
    r = client.get(f"/api/activities/{act.id}/qr.svg?base=https://evil.example")
    assert r.status_code == 400
    # 配置里的对外域名与本次请求自己的域名都照常放行
    assert client.get(
        f"/api/activities/{act.id}/qr.svg?base={PUBLIC}").status_code == 200
    assert client.get(
        f"/api/activities/{act.id}/qr.svg?base={FALLBACK}").status_code == 200


# —— 域名清单的解析 ——
#
# 两种写法都真实出现过：.env 里写成了 JSON 数组，而解析只按逗号切。切出来的
# 碎片 urlparse 后 netloc 是空串，白名单**静默变成空的**——二维码照发不误，
# 只是配置里的域名再也不被放行。这种错不报任何异常，只能靠测试钉住。

def test_域名清单两种写法都认():
    from urllib.parse import urlparse
    for raw in ('["https://a.cn","https://b.cn"]', "https://a.cn,https://b.cn"):
        netlocs = {urlparse(u).netloc for u in config._url_list(raw)}
        assert netlocs == {"a.cn", "b.cn"}, f"{raw} 解析成了 {netlocs}"


def test_域名清单去掉末尾斜杠与空白():
    assert config._url_list(" https://a.cn/ , https://b.cn ") == \
        ["https://a.cn", "https://b.cn"]


def test_空清单不报错():
    assert config._url_list("") == []
    assert config._url_list("   ") == []
    assert config._url_list("[]") == []


def test_半截_JSON_不会把整条配置吃掉():
    """写法坏掉时退回按逗号切，至少还留下点东西可查——总好过静默清空。"""
    assert config._url_list('["https://a.cn"')
