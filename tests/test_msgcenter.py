"""消息中心：通道白名单、送达判定、失败不打断业务。

真实推送在测试环境永远走不到（开放平台变量为空 → real_mode() 为假），
所以这里既验「不会误打真实接口」，也用手工 monkeypatch 验真实分支的行为。
"""
from __future__ import annotations

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

    def fake_send(student_id, title, content):
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
        seen.append((slots_free, lock_free, committed))
        return True

    monkeypatch.setattr(locks, "WAIT_TIMEOUT", 0.3)   # 锁被占着时别干等 3 秒
    monkeypatch.setattr(msgcenter, "real_mode", lambda *_: True)
    monkeypatch.setattr(msgcenter, "_send", fake_send)
    sid = take_student()
    assert enroll(client, login(client, sid), act, g).status_code == 200
    assert seen == [(True, True, 1)]    # 推了一次：名额已还、锁已放掉、事务已提交

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
