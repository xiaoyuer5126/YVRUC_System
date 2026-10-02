"""测试夹具：一套演示数据 + 若干登录态客户端。

需要 PostgreSQL 与 Redis：
    docker compose --profile test run --rm tests

**测试会清空所连的库**，所以默认连的是独立的 rucva_test（不存在则自动建），
Redis 也用另一个 db 号。可用 RUCVA_TEST_DATABASE_URL / RUCVA_TEST_REDIS_URL 覆盖。
"""
from __future__ import annotations

import itertools
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

TEST_DATABASE_URL = os.environ.get(
    "RUCVA_TEST_DATABASE_URL",
    "postgresql+psycopg://rucva:rucva_dev@127.0.0.1:5433/rucva_test")


def _ensure_database(url: str) -> None:
    """测试库不存在就建一个。少一步「先手工建库」的隐性前提。"""
    target = make_url(url)
    admin = create_engine(target.set(database="postgres"),
                          isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as conn:
            exists = conn.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database}).scalar()
            if not exists:
                conn.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin.dispose()


_ensure_database(TEST_DATABASE_URL)
os.environ["RUCVA_DATABASE_URL"] = TEST_DATABASE_URL
os.environ["RUCVA_REDIS_URL"] = os.environ.get(
    "RUCVA_TEST_REDIS_URL", "redis://127.0.0.1:6380/1")
os.environ.setdefault("RUCVA_DEMO_PASSWORD", "test-pass")
# 身份证号是加密存的，用例里用一把固定密钥（生产的那把在 .env 里）
os.environ.setdefault("RUCVA_ID_CARD_KEY",
                      "OaIvZ3wSxT9mQ1rKcB7hLdN5fGyU2pVe4XjA6snMtE0=")
# 二维码只认自家域名。用例里的「对外域名」就是这一个，别的一律被拒。
os.environ.setdefault("RUCVA_PUBLIC_BASE_URL", "https://v.ruc.edu.cn")
# 关掉后台定时任务：每建一个 TestClient 都会触发一次立即扫描，它在另一个
# 线程里跟用例抢同一批数据（解冻、递补、状态流转），断言会随机多算一份。
# 定时任务本身在 test_scheduled.py 里直接调函数来验，不靠这个循环。
os.environ["RUCVA_DISABLE_SWEEPER"] = "1"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import seed  # noqa: E402
from backend.database import SessionLocal  # noqa: E402

# 先把测试库按模型重建，**再**导入 app。顺序不能颠倒：导入 backend.main 会跑
# create_app() → init_db()，那里会拿模型跟库对一遍，对不上直接拒绝启动
# （见 database.check_schema）。上一次跑剩下的旧结构正好是「对不上」的那种，
# 先导 app 就会在这里撞上那句「先修库再启动」。
seed.main()

from backend.main import app  # noqa: E402

DEMO_PASSWORD = os.environ["RUCVA_DEMO_PASSWORD"]

ADMIN = "2025001"        # 王砚秋，管理员
INITIATOR = "2025002"    # 李慕清，活动发起方（只管自己的活动）
QINGXIE = "2025003"      # 张远航，青协


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    """用例直连数据库的会话。

    ⚠️ 接口是在**另一个会话**里写库的，本会话的身份映射不会自动感知。
    调完接口再读同一个对象，必须先 `db.expire_all()`（或 `db.refresh(obj)`），
    否则读到的是调用前的旧值——测试会以「产品没写进去」的样子失败，
    而实际上是会话缓存在骗你。
    """
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def dl_url(client: TestClient, headers: dict, path: str) -> str:
    """下载直链：先换一张一次性票据，再拼进地址。

    浏览器里 <a> 发不出 Authorization 头，用例也照这条路走，
    才验得到真实的下载流程。
    """
    r = client.post("/api/auth/download-ticket", headers=headers,
                    json={"path": path.split("?")[0]})
    assert r.status_code == 200, r.text
    return f"{path}{'&' if '?' in path else '?'}ticket={r.json()['ticket']}"


def login(client: TestClient, student_id: str) -> dict:
    r = client.post("/api/auth/demo-login",
                    json={"student_id": student_id, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


# 岗位的服务地点、要求原文、培训内容都是必填（志愿者到岗前只能看到这些）
POSITION_TEXT = {"location": "北门", "service_task": "无特殊要求",
                 "training_note": "现场由负责人统一讲解"}


@pytest.fixture(scope="session")
def open_group_days():
    """open_group 默认场次日期的发生器，整个会话共用一个。

    放在用例级的话每个用例都从同一天数起，不同用例的场次照样会撞在一起。
    """
    return itertools.count(30)


@pytest.fixture
def open_group(db, open_group_days):
    """一个报名中的活动 + 一个空场次，名额由用例自己定。

    每次调用把场次往后挪一天（整个会话内递增）：场次不能重叠，否则同一个
    学生在别处占的时段会把这里的报名挡掉。需要重叠的用例自己指定 day_offset。
    reg_end 默认给得很远，需要「报名已截止」的用例自己改。
    """
    from datetime import timedelta
    from backend.models import (Activity, ActivityManager, Position, Shift,
                                User, now, uid)
    seq = open_group_days

    def _make(capacity: int, *, mode: str = "pick", waitlist_quota: int = 3,
              day_offset: int | None = None, start_in_hours: float = 4,
              requirement_tags: dict | None = None, needs_leader: bool = False):
        start = timedelta(days=day_offset if day_offset is not None
                          else next(seq), hours=start_in_hours)
        admin = db.query(User).filter(User.student_id == ADMIN).one()
        act = Activity(id=uid("act"), title=f"测试活动 {uid('t')}",
                       intro="用例生成", location="测试地点",
                       contacts=[{"name": "测试", "phone": "13900000000"}],
                       contact_group_qr="x", status="published",
                       reg_start=now() - timedelta(days=1),
                       reg_end=now() + timedelta(days=365),
                       created_by=admin.id)
        db.add(act)
        db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                               user_id=admin.id, added_by=admin.id))
        g = Shift(id=uid("g"), activity_id=act.id, name="测试场次",
                  start_time=now() + start,
                  end_time=now() + start + timedelta(hours=3),
                  mode=mode, sort=0)
        db.add(g)
        db.flush()
        # 名额一律在岗位上。assign 模式的场次带的是「待分配」占位岗位——
        # 报名的人都落在它上面，跟线上建场次时自动生成的那个是一回事。
        p = Position(id=uid("p"), shift_id=g.id,
                     name="待分配" if mode == "assign" else "测试岗位",
                     is_placeholder=(mode == "assign"),
                     quota=capacity, waitlist_quota=waitlist_quota,
                     requirement_tags=requirement_tags or {},
                     needs_leader=needs_leader,
                     sort=-1 if mode == "assign" else 0, **POSITION_TEXT)
        db.add(p)
        db.commit()
        g.test_position_id = p.id
        return act, g
    return _make



@pytest.fixture
def admin(client):
    return login(client, ADMIN)


@pytest.fixture
def initiator(client):
    """另一个有管理身份、但不管这个活动的人。用来验越权。"""
    return login(client, INITIATOR)


@pytest.fixture(scope="session")
def student_seq():
    """用例专用学生的序号发生器。

    不复用演示数据里的学生：他们身上已经挂着演示报名，一个用例占掉的时段
    会让下一个用例的报名撞冲突，测出来的失败与被测逻辑无关。
    """
    return iter(range(1, 10_000))


@pytest.fixture
def take_student(student_seq):
    """现建一个资料齐全的干净学生，返回学号。"""
    from backend.models import User, uid
    from backend.seed import fake_id_card

    def _take() -> str:
        n = next(student_seq)
        sid = f"9{n:06d}"
        db = SessionLocal()
        try:
            u = User(id=uid("u"), student_id=sid, name=f"测试用户{n}",
                     gender="女" if n % 2 else "男", college="明理书院",
                     grade="2024", campus="中关村校区",
                     phone=f"13900{n:06d}"[:11],
                     email=f"{sid}@test.local", wechat=f"wx_{sid}",
                     id_card=fake_id_card("20050101", n),
                     user_type="student", roles=["student"])
            db.add(u)
            db.commit()
        finally:
            db.close()
        return sid
    return _take


def student_row(db, student_id: str):
    from backend.models import User
    return db.query(User).filter(User.student_id == student_id).one()


def make_enrolled_activity(db, take_student):
    """一个已发布的活动 + 一个场次 + 一名已确认志愿者。

    通知类用例只关心"有人能收到"，不必每次自己搭一遍岗位树。
    要第二个活动（比如验证批次不串活动）时直接再调一次。
    """
    from datetime import timedelta
    from backend.models import (Activity, ActivityManager, Enrollment,
                                Position, Shift, now, uid)

    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"通知测试活动 {uid('t')}",
                   intro="x", location="世纪馆",
                   contacts=[{"name": "测试", "phone": "13900000000"}],
                   contact_group_qr="x", status="published",
                   reg_start=now() - timedelta(days=1),
                   reg_end=now() + timedelta(days=2), created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    shift = Shift(id=uid("s"), activity_id=act.id, name="上午场",
                  start_time=now() + timedelta(days=3),
                  end_time=now() + timedelta(days=3, hours=4),
                  mode="pick", sort=0)
    db.add(shift)
    db.flush()
    pos = Position(id=uid("p"), shift_id=shift.id, name="引导", quota=5,
                   waitlist_quota=2, sort=0, **POSITION_TEXT)
    db.add(pos)
    db.flush()

    u = student_row(db, take_student())
    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=shift.id,
                      user_id=u.id, position_id=pos.id, status="enrolled",
                      joined_at=now()))
    db.commit()
    return act, shift, u


@pytest.fixture
def enrolled_activity(db, take_student):
    return make_enrolled_activity(db, take_student)


def upload_proposal(client, headers, activity_id, name="立项书.pdf"):
    """传一份立项书。提交对外联络部审批必须带它（种子里只有这个部门有部长，
    走审批的用例大多交到这里）。"""
    r = client.post(f"/api/staff/activities/{activity_id}/project-files/proposal",
                    headers=headers,
                    files={"file": (name, b"%PDF-1.4 proposal", "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def upload_stamped(client, headers, activity_id):
    """传齐时长审核要的盖章材料：对外活动提交时长审核，盖章立项书和盖章结项书
    缺一份都不行。返回最后一次上传的响应。"""
    for slot in ("stamped", "closing"):
        r = client.post(f"/api/staff/activities/{activity_id}/project-files/{slot}",
                        headers=headers,
                        files={"file": (f"{slot}.pdf", b"%PDF stamped", "application/pdf")})
        if r.status_code != 200:
            return r
    return r


def approve(client, headers, activity_id, auto_publish=True):
    """走一遍审批：提交给一个有部长的部门 → 通过。要发布活动就得先过这关。
    默认「通过即发布」，回来活动就在报名中了。

    挑「有部长的」而不是第一个：部长可以被改到别的部门去，用例之间共用
    一个库，写死下标会被别的用例改出来的状态坑掉。
    """
    depts = [d for d in client.get("/api/staff/departments", headers=headers).json()
             if d["has_head"]]
    assert depts, "没有任何部门设了部长，无法走审批"
    upload_proposal(client, headers, activity_id)
    r = client.post(f"/api/staff/activities/{activity_id}/submit", headers=headers,
                    json={"department_id": depts[0]["id"],
                          "auto_publish": auto_publish})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{activity_id}/decide", headers=headers,
                    json={"approve": True})
    assert r.status_code == 200, r.text


@contextmanager
def editing(client: TestClient, headers: dict, activity_id: str):
    """已发布的活动要改结构，必须先进编辑态。

    with editing(client, admin, act.id):
        client.patch(f"/api/staff/positions/{p.id}", ...)
    """
    r = client.post(f"/api/staff/activities/{activity_id}/edit-lock",
                    headers=headers)
    assert r.status_code == 200, r.text
    url = f"/api/staff/activities/{activity_id}/publish-changes"
    try:
        yield
    except BaseException:
        # 块里已经失败了：尽量解冻，但别让这里的结果盖住原来那个错误
        client.post(url, headers=headers)
        raise
    # 块里没出错，保存发布就必须成功——不然改动其实没发出去，用例却照样通过
    r = client.post(url, headers=headers)
    assert r.status_code == 200, r.text


# ======================= 对外联络部（值班 / 立项书） =======================

LIAISON_HEAD = "2025004"    # 周砚，对外联络部部长
LIAISON_CORE = "2025005"    # 林骁，对外联络部骨干
LIAISON_MEMBER = "2025006"  # 苏昀，对外联络部成员（学习权限）


def liaison_dept(db):
    from backend.models import Department
    return db.query(Department).filter(Department.name == "对外联络部").one()


@pytest.fixture
def liaison(db):
    """对外联络部。值班人是全库共享的状态：用例结束（无论成败）一律撤下，
    否则后面所有「通知发给部长」的用例都会莫名收不到。"""
    dept = liaison_dept(db)
    dept.duty_user_id = None
    db.commit()
    yield dept
    db.rollback()
    db.expire_all()
    dept = liaison_dept(db)
    dept.duty_user_id = None
    db.commit()


@pytest.fixture
def extra_core(db, take_student, liaison):
    """再来一位对外联络部骨干（交接班要有第二个人）。用完调离本部门。"""
    from backend.models import User
    sid = take_student()
    u = db.query(User).filter(User.student_id == sid).one()
    u.roles = ["qingxie", "student"]
    u.department_id = liaison.id
    u.qingxie_post = "core"
    db.commit()
    yield u
    db.rollback()
    db.expire_all()
    u = db.query(User).filter(User.student_id == sid).one()
    u.department_id = None
    u.qingxie_post = "member"
    db.commit()


def inbox(db, student_id: str, title_prefix: str) -> list:
    """某人库里标题以 title_prefix 开头的通知（接口在另一个会话里写的，先 expire）。"""
    from backend.models import Notification
    db.expire_all()
    u = student_row(db, student_id)
    return (db.query(Notification)
            .filter(Notification.user_id == u.id,
                    Notification.title.startswith(title_prefix)).all())


def is_approval_todo(notes: list) -> bool:
    """审批待办的通知：发到了、不要签收、点开进审批页（审批人只在那儿办得了）。"""
    from backend.integrations import msgcenter
    return bool(notes) and all(
        not n.need_ack and msgcenter.page_for(n.template_key, n.data) == "/approvals"
        for n in notes)


def make_draft(client, headers, *, proposal: bool = True) -> str:
    """一个结构完整、可以提交审批的草稿；proposal=True 顺手传好立项书。"""
    from datetime import timedelta
    from backend.models import now, uid
    r = client.post("/api/staff/activities", headers=headers,
                    json={"contact_group_qr": "x", "title": f"对外活动 {uid('t')}",
                          "organizer": "文学院青协", "intro": "x", "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=5)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=headers,
                    json={"name": "上午场", "mode": "pick",
                          "start_time": (now() + timedelta(days=9)).isoformat(),
                          "end_time": (now() + timedelta(days=9, hours=3)).isoformat()})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/shifts/{r.json()['id']}/positions", headers=headers,
                    json={"name": "引导", "quota": 3, **POSITION_TEXT})
    assert r.status_code == 200, r.text
    if proposal:
        upload_proposal(client, headers, aid)
    return aid
