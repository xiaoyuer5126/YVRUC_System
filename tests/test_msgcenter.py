"""消息中心：通道白名单、送达判定、失败不打断业务。

真实推送在测试环境永远走不到（开放平台变量为空 → real_mode() 为假），
所以这里既验「不会误打真实接口」，也用手工 monkeypatch 验真实分支的行为。
"""
from __future__ import annotations

from backend import config
from backend.integrations import msgcenter
from backend.models import Notification, User


def test_测试环境不碰真实接口():
    """conftest 不注入开放平台变量，这里必须始终是假。

    一旦这道门失守，测试就会拿真实 appid 往学校网关上打——真实的人会收到
    真实的消息。
    """
    assert not msgcenter.real_mode()


def test_通道里永远没有短信():
    """学校未同意本平台发短信，短信还是按条计费的真钱。

    管理员在「集成设置」里填了 1 也只是记条日志——宁可少发一条，
    也不能悄悄发出去。
    """
    assert msgcenter._channels({"channels": "1,4"}) == "4"
    assert msgcenter._channels({"channels": "1"}) == "4"
    assert msgcenter._channels({"channels": "1,2,4"}) == "2,4"
    assert msgcenter._channels({}) == "4"


def test_邮件通道照样放行():
    """挡的只有短信，别把正常通道一起挡了。"""
    assert msgcenter._channels({"channels": "2,4"}) == "2,4"


def test_报文里收件人是真数组不是字符串(db, monkeypatch):
    """参数表把 receivers 标成 string，但网关只认请求示例里那种真数组：
    json.dumps 出来的字符串换回的是 code=11「参数格式有误」。"""
    seen: dict = {}

    def fake_post(path, payload, **kw):
        seen["path"], seen["payload"] = path, payload
        return {"successed": True}

    monkeypatch.setattr(msgcenter.sudy_sign, "post_json", fake_post)
    assert msgcenter._send("2025201717", "标题", "正文") is True
    assert seen["path"] == msgcenter.SEND_PATH
    p = seen["payload"]
    assert p["receivers"] == [{"type": "uc_ux", "address": "(2025201717)"}]
    assert isinstance(p["receivers"], list)
    assert p["channels"] == "4"
    assert p["plannedTime"] == ""
    assert "extras" in p


def test_点开去哪页只有一份规则():
    """站内消息中心与企业微信卡片共用 page_for：模板落点 → data.route → 活动页。"""
    page = msgcenter.page_for
    assert page("signup_success", None) == "/my"
    # 模板落点优先于活动 id：时长的确认/申诉按钮不在活动页上
    assert page("hours_approved", {"activity_id": "a1"}) == "/hours"
    assert page("hours_locked", {"activity_id": "a1"}) == "/hours"
    # route 优先于活动 id：待办的审批也带着活动 id，但通过/驳回只在审批页
    assert page("custom", {"activity_id": "a1", "route": "approvals"}) == "/approvals"
    assert page("custom", {"route": "hours"}) == "/hours"
    assert page("custom", {"route": "feedback", "feedback_id": "f1"}) == "/feedback"
    assert page("activity_changed", {"activity_id": "a1"}) == "/activity/a1"
    # 发给活动管理者的进活动管理页，页签只认名单里的
    assert (page("custom", {"activity_id": "a1", "route": "manage", "tab": "review"})
            == "/manage/a1?tab=review")
    assert page("custom", {"activity_id": "a1", "route": "manage", "tab": "x"}) == "/manage/a1"
    assert page("custom", {"route": "manage"}) == ""
    # 名单外的 route 不猜，退回看活动 id；什么都没有就不跳
    assert page("custom", {"activity_id": "a1", "route": "unknown"}) == "/activity/a1"
    assert page("custom", {"route": "unknown"}) == ""
    assert page("checkin_reminder", {}) == ""


def test_站点根地址只留协议域名和路径():
    """带着 #/、查询串或末尾斜杠的话，拼出来的通知链接点开只会落到首页。"""
    assert config._site_root("https://v.ruc.edu.cn/#/") == "https://v.ruc.edu.cn"
    assert config._site_root(" https://x.cn/app/?a=1 ") == "https://x.cn/app"
    assert config._site_root("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    # 不是 http(s) 网址的当作没配：发个打不开的链接不如不发
    assert config._site_root("v.ruc.edu.cn") == ""
    assert config._site_root("") == ""


def test_企业微信链接_要签收的落在消息中心(monkeypatch):
    """「我已知悉」只在消息中心：要签收的、催签收的卡片都得带人去那儿，
    其余跟站内去同一页。"""
    monkeypatch.setattr(config, "SITE_URL", "https://p.cn")   # 已规范化：不带末尾斜杠
    link = msgcenter._link_for
    assert link("signup_success", None, False) == "https://p.cn/my"
    assert (link("custom", {"activity_id": "a1", "route": "approvals"}, False)
            == "https://p.cn/approvals")
    assert link("custom", {"activity_id": "a1"}, True) == "https://p.cn/messages"
    assert (link("checkin_reminder", {"activity_id": "a1"}, True)
            == "https://p.cn/messages")
    assert (link("ack_reminder", {"activity_id": "a1"}, False)
            == "https://p.cn/messages")
    # 没有落点、或站点根为空：不带链接，免得发出打开是白屏的地址
    assert link("custom", {}, False) == ""
    monkeypatch.setattr(config, "SITE_URL", "")
    assert link("signup_success", None, False) == ""
    assert link("custom", {"activity_id": "a1"}, True) == ""


def test_带链接的通知把链接放extras(db, monkeypatch):
    """卡片点击跳转走 extras.im_linkUrl：正文里写 <a> 也行，但链接词
    在卡片上根本看不出来可点（真机验证），只有这一个可靠的落点。"""
    seen: dict = {}

    def fake_post(path, payload, **kw):
        seen["payload"] = payload
        return {"successed": True}

    monkeypatch.setattr(msgcenter.sudy_sign, "post_json", fake_post)
    url = "https://p.cn/my"
    assert msgcenter._send("2025201717", "标题", "正文", url) is True
    assert seen["payload"]["extras"] == {"im_linkUrl": url}
    assert msgcenter._send("2025201717", "标题", "正文") is True
    assert seen["payload"]["extras"] == {}


def test_成功返回才算送达():
    assert msgcenter._delivered({"successed": True}, "2025201717", "t") is True


def test_收件人写错不算送达():
    """successed=true 只说明请求被受理了。

    只发一个人，所以 wrongAddress / channelErrorInfos 非空就等于没送到——
    记成 delivered 的话，「推送失败」在库里看起来是成功的。
    """
    assert msgcenter._delivered(
        {"successed": True, "wrongAddress": ["(2025201717)"]},
        "2025201717", "t") is False
    assert msgcenter._delivered(
        {"successed": True, "channelErrorInfos": ["企业微信未绑定"]},
        "2025201717", "t") is False


def test_推送失败只标未送达不打断业务(db, take_student, monkeypatch):
    """外部接口挂了，业务必须照常走完——这正是当初的设计契约。"""
    sid = take_student()
    user = db.query(User).filter(User.student_id == sid).one()

    def boom(*_a, **_kw):
        raise RuntimeError("网关不通")

    monkeypatch.setattr(msgcenter, "real_mode", lambda *_: True)
    monkeypatch.setattr(msgcenter, "_send", boom)
    msgcenter.send_user(db, user, "custom", title="标题", content="正文")
    db.commit()

    row = (db.query(Notification)
           .filter(Notification.user_id == user.id,
                   Notification.template_key == "custom").one())
    assert row.delivered is False
    assert row.title == "标题" and row.content == "正文"


def test_Mock_下落库且标记送达(db, take_student):
    sid = take_student()
    user = db.query(User).filter(User.student_id == sid).one()
    msgcenter.send_user(db, user, "custom", title="标题", content="正文")
    db.commit()
    row = (db.query(Notification)
           .filter(Notification.user_id == user.id,
                   Notification.template_key == "custom").one())
    assert row.delivered is True
    # 通道名记的是「走的哪个通道」，不是「哪个模块发的」，所以仍是 wecom
    assert row.channel == "wecom"


def test_真实推送在事务提交且场次锁放掉之后才发(client, open_group, take_student,
                                    monkeypatch):
    """一次推送最长等 8 秒：锁里推送会占着场次锁（或进程名额）干等，排在后面的
    人全跟着等；业务回滚了也不该推出去。"""
    from fastapi import HTTPException

    from backend import config, locks
    from backend.database import SessionLocal
    from backend.models import Enrollment
    from conftest import login
    from test_enroll import enroll

    act, g = open_group(2, waitlist_quota=0)
    seen = []

    def fake_send(student_id, title, content, url=""):
        # 进程名额全都空着，才说明报名请求已经走出 with 块、把名额还了
        got = [locks._SLOTS.acquire(timeout=0) for _ in range(config.LOCK_SLOTS)]
        for ok in got:
            if ok:
                locks._SLOTS.release()
        slots_free = all(got)
        # 数据库锁还攥着的话这里抢不到，等满 WAIT_TIMEOUT 报 503；
        # 另开一个会话看得到这条报名，才说明业务事务已经提交
        fresh = SessionLocal()
        try:
            try:
                with locks.shift_lock(fresh, g.id):
                    lock_free = True
            except HTTPException:
                lock_free = False
            fresh.rollback()      # 放掉刚拿的锁（等锁超时的话事务也已作废）
            committed = (fresh.query(Enrollment)
                         .join(User, Enrollment.user_id == User.id)
                         .filter(User.student_id == student_id,
                                 Enrollment.shift_id == g.id,
                                 Enrollment.status == "enrolled").count())
        finally:
            fresh.close()
        seen.append((slots_free, lock_free, committed, url))
        return True

    monkeypatch.setattr(locks, "WAIT_TIMEOUT", 0.3)   # 锁被占着时别干等 3 秒
    monkeypatch.setattr(msgcenter, "real_mode", lambda *_: True)
    monkeypatch.setattr(msgcenter, "_send", fake_send)
    monkeypatch.setattr(config, "SITE_URL", "https://p.cn")
    sid = take_student()
    assert enroll(client, login(client, sid), act, g).status_code == 200
    # 推了一次：名额已还、锁已放掉、事务已提交，且链接拼成了报名成功该去的页
    assert seen == [(True, True, 1, "https://p.cn/my")]

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.student_id == sid).one()
        row = (db.query(Notification)
               .filter(Notification.user_id == user.id,
                       Notification.template_key == "signup_success").one())
        assert row.delivered is True         # 推成功后回写了送达
    finally:
        db.close()


def test_业务回滚了就不推(db, take_student, monkeypatch):
    sid = take_student()
    user = db.query(User).filter(User.student_id == sid).one()
    sent = []
    monkeypatch.setattr(msgcenter, "real_mode", lambda *_: True)
    monkeypatch.setattr(msgcenter, "_send", lambda *a: sent.append(a) or True)
    msgcenter.send_user(db, user, "custom", title="标题", content="正文")
    db.rollback()
    db.commit()
    assert sent == []
