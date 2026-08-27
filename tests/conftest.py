"""测试夹具：一套演示数据 + 若干登录态客户端。

需要 PostgreSQL 与 Redis：
    docker compose --profile test run --rm tests

**测试会清空所连的库**，所以默认连的是独立的 rucva_test（不存在则自动建），
Redis 也用另一个 db 号。可用 RUCVA_TEST_DATABASE_URL / RUCVA_TEST_REDIS_URL 覆盖。
"""
from __future__ import annotations

import os

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

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend import seed  # noqa: E402
from backend.database import SessionLocal  # noqa: E402
from backend.main import app  # noqa: E402

DEMO_PASSWORD = os.environ["RUCVA_DEMO_PASSWORD"]

ADMIN = "2025001"        # 王砚秋，管理员
TEACHER = "2025002"      # 李慕清，老师（只管自己的活动）
QINGXIE = "2025003"      # 张远航，青协


@pytest.fixture(scope="session", autouse=True)
def seeded():
    seed.main()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def db():
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def login(client: TestClient, student_id: str) -> dict:
    r = client.post("/api/auth/demo-login",
                    json={"student_id": student_id, "password": DEMO_PASSWORD})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest.fixture
def admin(client):
    return login(client, ADMIN)


@pytest.fixture
def teacher(client):
    return login(client, TEACHER)


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
                     grade="2024", phone=f"13900{n:06d}"[:11],
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
