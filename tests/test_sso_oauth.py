"""统一身份认证：state 一次性、首次登录自动建档。

真实 CAS 在测试里调不到（开放平台变量为空 → sso 是 mock，接口直接 409），
所以这里验 state 的存取语义、建档规则，以及回调接口在拿到身份后的行为。
"""
from __future__ import annotations

import pytest

from backend import school_id
from backend.database import SessionLocal
from backend.integrations import sso
from backend.locks import issue_sso_state, redeem_sso_state
from backend.models import Notification, Session, User

TEST_SID = "2025201717"      # 用户本人的学号，禁止用别的账号做联调


def _drop_users(*sids: str) -> None:
    """删掉用例建出来的档案。

    用例共用一个库（seed 只在会话开始时跑一次），凭空多出来的用户会被别处的
    人数统计算进去，表现为毫不相干的用例随机失败。用独立会话，避开
    `db` fixture 的身份映射缓存。
    """
    s = SessionLocal()
    try:
        ids = [u.id for u in s.query(User)
               .filter(User.student_id.in_(sids)).all()]
        if ids:
            # 先删挂在人身上的东西：sessions / notifications 都有指向 users 的
            # 外键，直接删人会撞约束，而失败又会把脏数据留给下一个用例——
            # 一个用例的清理失败会连累一串看起来毫不相干的用例。
            for table in (Session, Notification):
                s.query(table).filter(table.user_id.in_(ids)).delete(
                    synchronize_session=False)
            s.query(User).filter(User.id.in_(ids)).delete(
                synchronize_session=False)
        s.commit()
    finally:
        s.close()


# —— state ——

def test_state_一次一用():
    state = issue_sso_state("/my", 60)
    assert redeem_sso_state(state) == "/my"
    # 用过就没了：重放同一条回调不该再换出一个会话
    assert redeem_sso_state(state) is None


def test_没指定去向的_state_是合法的():
    """空串是「没指定去哪儿」，不是「无效」。

    两者混为一谈的话，从活动广场直接点登录的人会被当成伪造的回调拒掉。
    """
    state = issue_sso_state("", 60)
    assert redeem_sso_state(state) == ""
    assert redeem_sso_state(state) is None


def test_伪造的_state_核销不掉():
    assert redeem_sso_state("我自己编的") is None
    assert redeem_sso_state("") is None


# —— 从学校返回里取字段 ——
#
# 2026-09-21 真机实测的 profiles 响应：
#   {"loginName": …, "name": …, "gender": "男", "categoryName": "学生",
#    "accounts": [{"loginName": …, "childCategoryName": "本科生",
#                  "orgName": "信息学院", "enterGrade": "2025",
#                  "isMainOrg": true}]}

def test_学校给的年级学段学院直接采用():
    ident = sso._identity({
        "loginName": TEST_SID, "name": "马霄宇", "gender": "男",
        "categoryName": "学生",
        "accounts": [{"loginName": TEST_SID, "childCategoryName": "硕士研究生",
                      "orgName": "信息学院", "enterGrade": "2025",
                      "isMainOrg": True}]})
    assert ident["student_id"] == TEST_SID
    # 学号第 5-6 位是 20（本科），但学校说是硕士——以学校为准
    assert ident["stage"] == "硕士研究生"
    assert ident["grade"] == "2025"
    assert ident["college"] == "信息学院"
    assert ident["gender"] == "男"
    assert ident["user_type"] == "student"


def test_教职工的身份类型():
    ident = sso._identity({"loginName": "20200001", "name": "某老师",
                           "categoryName": "教职工"})
    assert ident["user_type"] == "teacher"


def test_学校给了没收录的学段就退回推导():
    """学段是招募范围的筛选项：写个 STAGES 里没有的值进去，这个人会静默
    匹配不上任何活动，而且事后查不出原因。所以宁可退回按学号推。"""
    ident = sso._identity({
        "loginName": TEST_SID,
        "accounts": [{"loginName": TEST_SID, "childCategoryName": "留学生"}]})
    assert ident["stage"] == "本科生"      # 学号兜底出来的，不是「留学生」


def test_多账号挑登录名匹配的那条():
    """一个人可能既是学生又是教职工（isMainOrg 指向单位那份）。盲取第 0 条
    会把学院填成另一个单位的。"""
    ident = sso._identity({
        "loginName": TEST_SID, "categoryName": "学生",
        "accounts": [
            {"loginName": "20200001", "categoryName": "教职工",
             "orgName": "信息技术中心", "isMainOrg": True},
            {"loginName": TEST_SID, "categoryName": "学生",
             "orgName": "信息学院", "enterGrade": "2025"}]})
    assert ident["college"] == "信息学院"
    assert ident["user_type"] == "student"


def test_学校什么都没给就退回按学号推导():
    ident = sso._identity({"loginName": TEST_SID, "name": "测试同学"})
    assert ident["grade"] == "2025" and ident["stage"] == "本科生"
    assert ident["college"] == "" and ident["gender"] == ""


def test_没有学号就报错():
    with pytest.raises(ValueError):
        sso._identity({"name": "无名氏"})


# —— 建档 ——
#
# _provision 收的是上面 _identity 翻译好的字典。只给 student_id/name 时走的
# 正是「学校没给、按学号推导」那条兜底路径——它现在降级成兜底了，但仍然要
# 能独立跑通，所以下面几条钉住它。

def ident_of(sid: str, **extra) -> dict:
    return {"student_id": sid, "name": "测试同学", **extra}


def test_首次登录自动建档(db):
    from backend.routers.auth import _provision
    try:
        user = _provision(db, ident_of(TEST_SID))
        assert user.user_type == "student"
        assert user.grade == "2025"
        assert user.stage == "本科生"
        assert user.roles == ["student"]
    finally:
        _drop_users(TEST_SID)


def test_再次登录复用同一份档案(db):
    from backend.routers.auth import _provision
    try:
        first = _provision(db, ident_of(TEST_SID))
        again = _provision(db, ident_of(TEST_SID))
        assert first.id == again.id
        assert db.query(User).filter(User.student_id == TEST_SID).count() == 1
    finally:
        _drop_users(TEST_SID)


def test_教师账号首次登录即是活动发起方(db):
    """老师自动是活动发起方，能直接发起活动；管理类权限仍由管理员授予。"""
    from backend.routers.auth import _provision
    try:
        user = _provision(db, ident_of("20200001", name="某老师"))
        assert user.user_type == "teacher"
        assert user.roles == ["initiator"]
        assert school_id.parse("20200001")[0] == "teacher"
    finally:
        _drop_users("20200001")


def test_建档不填手机号微信():
    """这两项由本人到「个人资料」补全。

    缺手机号/微信本来就会拦住报名并弹完善表单，这条路径早就有了；
    在这里编一个假号码反而会让人以为自己填过了。
    """
    from backend.routers.auth import _provision
    db = SessionLocal()
    try:
        user = _provision(db, ident_of(TEST_SID))
        assert user.phone == "" and user.wechat == ""
    finally:
        db.close()
        _drop_users(TEST_SID)


# —— 再次登录时的刷新 ——
#
# 升学年、转专业、改名都该在下次登录时同步过来；但学校侧没有的那几项
# （手机号/微信/身份证号/校区）是本人自己填的，碰一下就等于清空。

def test_再次登录刷新学校给的字段(db):
    from backend.routers.auth import _provision, _sync_from_school
    try:
        user = _provision(db, ident_of(TEST_SID, name="旧名字"))
        _sync_from_school(db, user, ident_of(TEST_SID, name="新名字",
                                             college="信息学院",
                                             stage="硕士研究生"))
        assert user.name == "新名字"
        assert user.college == "信息学院"
        assert user.stage == "硕士研究生"
    finally:
        _drop_users(TEST_SID)


def test_刷新不碰学生自己填的项(db):
    """拿空值刷新一下就等于把人填好的东西清空了——而且下次报名会被
    「资料未完善」拦住，人还不知道为什么。

    身份证号同理（它也不在 _SCHOOL_FIELDS 里），只是测试环境没有
    RUCVA_ID_CARD_KEY，写非空值会直接抛错，这里就不碰它了。
    """
    from backend.routers.auth import _provision, _sync_from_school
    try:
        user = _provision(db, ident_of(TEST_SID))
        user.phone, user.wechat, user.campus = "13800000000", "wx_id", "通州校区"
        db.commit()
        _sync_from_school(db, user, ident_of(TEST_SID))    # 学校没给这几项
        assert (user.phone, user.wechat, user.campus) == \
            ("13800000000", "wx_id", "通州校区")
    finally:
        _drop_users(TEST_SID)


def test_学校返回空值不清掉已有的学院(db):
    from backend.routers.auth import _provision, _sync_from_school
    try:
        user = _provision(db, ident_of(TEST_SID, college="信息学院"))
        _sync_from_school(db, user, ident_of(TEST_SID))    # 这次学校没给学院
        assert user.college == "信息学院"
    finally:
        _drop_users(TEST_SID)


# —— 回调接口 ——

def test_未接通时回调直接拒绝(client):
    """测试环境 sso 是 mock，这个口子必须关着。"""
    r = client.post("/api/auth/sso-callback", json={"code": "c", "state": "s"})
    assert r.status_code == 409


def test_回调建号并开会话(client, monkeypatch):
    monkeypatch.setattr(sso, "mode", lambda: "oauth2")
    monkeypatch.setattr(sso, "handle_callback",
                        lambda code, state: {"student_id": TEST_SID,
                                             "name": "测试同学", "next": "/my"})
    try:
        r = client.post("/api/auth/sso-callback",
                        json={"code": "c", "state": "s"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["next"] == "/my"
        assert body["user"]["student_id"] == TEST_SID
        # 发出来的会话真能用
        me = client.get("/api/auth/me",
                        headers={"Authorization": f"Bearer {body['token']}"})
        assert me.status_code == 200
        assert me.json()["user"]["student_id"] == TEST_SID
    finally:
        _drop_users(TEST_SID)


def test_state_无效时拒绝且不换令牌(client, monkeypatch):
    """伪造的回调不该拿 code 去换令牌，也不该建出任何档案。"""
    monkeypatch.setattr(sso, "mode", lambda: "oauth2")

    def reject(code, state):
        raise PermissionError("登录已过期或来源不可信，请回到首页重新登录")

    monkeypatch.setattr(sso, "handle_callback", reject)
    r = client.post("/api/auth/sso-callback", json={"code": "c", "state": "bad"})
    assert r.status_code == 400
    assert "重新登录" in r.json()["detail"]
    assert db_has(TEST_SID) is False


def test_取不到用户时是_502_不是_500(client, monkeypatch):
    """上游失败要如实报成上游的问题，别让人以为是平台崩了。"""
    monkeypatch.setattr(sso, "mode", lambda: "oauth2")

    def broken(code, state):
        raise RuntimeError("未换到 access_token")

    monkeypatch.setattr(sso, "handle_callback", broken)
    r = client.post("/api/auth/sso-callback", json={"code": "c", "state": "s"})
    assert r.status_code == 502


def db_has(sid: str) -> bool:
    s = SessionLocal()
    try:
        return s.query(User).filter(User.student_id == sid).first() is not None
    finally:
        s.close()


# —— 跳转地址 ——

def test_mock_下不生成跳转地址(client):
    """演示模式下前端走口令表单，不该有人在页面加载时就去建 state。"""
    assert client.get("/api/auth/sso-login-url").status_code == 409


def test_sso_mode_不回跳转地址(client):
    """跳转地址要点击时才取（state 有有效期），这里只回方式与是否配好。"""
    body = client.get("/api/auth/sso-mode").json()
    assert "login_url" not in body
    # 只有 oauth2 一条真实路径：集成设置里能改协议的话，就会有人改成 cas
    # 然后卡在「票据校验尚未实现」上——那个选项已经拿掉了。
    assert body["mode"] in {"demo", "unavailable", "oauth2"}
