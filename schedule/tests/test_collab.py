"""协作:成员、邀请链接、编辑占用。需要真 Postgres(唯一约束、外键级联)。

这一套的每一条都是**权限边界**或**并发**,两类都是「测不出来就等于没做」的东西。
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest

DB_URL = os.environ.get("SCHEDULE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DB_URL, reason="没有配 SCHEDULE_TEST_DATABASE_URL")


@pytest.fixture
def db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.db import Base

    url = DB_URL
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix) and not url.startswith("postgresql+"):
            url = url.replace(prefix, "postgresql+psycopg://", 1)
            break
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _identity(nickname="小明"):
    from app.auth import Identity

    return Identity(uuid.uuid4(), nickname, f"{nickname}@ruc.edu.cn", "user")


@pytest.fixture
def owned(db):
    from app import store

    alice = _identity("小明")
    row = store.create(db, alice.user_id, "协作测试", None, alice.nickname)
    yield row, alice
    db.delete(row)
    db.commit()


# ————————————————————————————————————————— 谁看得见

def test_没被拉进来的人看不见_而且回404不回403(db, owned):
    """404 而不是 403：区分开的话，拿 UUID 逐个试就能问出「这个项目存在
    但我看不到」，等于把别人有哪些项目泄露出去了。
    """
    from fastapi import HTTPException

    from app import store

    row, _alice = owned
    bob = _identity("小红")
    with pytest.raises(HTTPException) as caught:
        store.get_owned(db, bob.user_id, row.id)
    assert caught.value.status_code == 404
    assert row.id not in {p.id for p in store.list_projects(db, bob.user_id)}


def test_拉进来之后就看得见_而且权限和创建者一致(db, owned):
    from app import store

    row, alice = owned
    bob = _identity("小红")
    invite = store.create_invite(db, row, alice)
    store.accept_invite(db, invite.token, bob)

    assert store.get_owned(db, bob.user_id, row.id).id == row.id
    assert row.id in {p.id for p in store.list_projects(db, bob.user_id)}
    # 能写。删除是唯一的例外，见 test_协作者删不掉项目_只有创建者能删。
    store.save_data(db, row, {"version": 2}, None, identity=bob, action="应用排班")
    assert row.data["version"] == 2


def test_移出之后立刻看不见(db, owned):
    from fastapi import HTTPException

    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.accept_invite(db, store.create_invite(db, row, alice).token, bob)
    store.remove_member(db, row.id, bob.user_id)

    with pytest.raises(HTTPException):
        store.get_owned(db, bob.user_id, row.id)


# ————————————————————————————————————————— 邀请链接

def test_失效的链接一律同一句话(db, owned):
    """不区分「不存在 / 过期了 / 撤销了」——区分开的话，拿 token 逐个试
    就能问出「这个 token 存在过」。
    """
    from fastapi import HTTPException

    from app import store

    row, alice = owned
    bob = _identity("小红")

    gone = store.create_invite(db, row, alice)
    store.revoke_invite(db, row.id, gone.id)

    stale = store.create_invite(db, row, alice)
    stale.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()

    messages = set()
    for token in (gone.token, stale.token, "根本不存在的token"):
        with pytest.raises(HTTPException) as caught:
            store.accept_invite(db, token, bob)
        assert caught.value.status_code == 404
        messages.add(caught.value.detail)
    assert len(messages) == 1, f"三种失效原因说的话不一样：{messages}"


def test_同一条链接可以多次使用_并且计数(db, owned):
    from app import store

    row, alice = owned
    invite = store.create_invite(db, row, alice)
    for name in ("小红", "小刚"):
        store.accept_invite(db, invite.token, _identity(name))

    db.refresh(invite)
    assert invite.used_count == 2
    assert len(store.list_members(db, row.id)) == 2


def test_重复点同一条链接不会变成两个成员(db, owned):
    from app import store

    row, alice = owned
    bob = _identity("小红")
    token = store.create_invite(db, row, alice).token
    store.accept_invite(db, token, bob)
    store.accept_invite(db, token, bob)
    assert len(store.list_members(db, row.id)) == 1


def test_创建者点自己的链接不会把自己加成协作者(db, owned):
    from app import store

    row, alice = owned
    store.accept_invite(db, store.create_invite(db, row, alice).token, alice)
    assert store.list_members(db, row.id) == []


def test_删项目时成员和邀请一起没(db):
    from app import store
    from app.models import ScheduleProjectInvite, ScheduleProjectMember

    alice = _identity("小明")
    row = store.create(db, alice.user_id, "待删", None, alice.nickname)
    pid = row.id
    store.accept_invite(db, store.create_invite(db, row, alice).token, _identity("小红"))
    assert db.query(ScheduleProjectMember).filter_by(project_id=pid).count() == 1

    store.remove(db, row)
    assert db.query(ScheduleProjectMember).filter_by(project_id=pid).count() == 0
    assert db.query(ScheduleProjectInvite).filter_by(project_id=pid).count() == 0


# ————————————————————————————————————————— 编辑占用

def test_一个人占着的时候别人写不进去(db, owned):
    """整份状态 PUT 的模型下，两个人同时改必然互相覆盖。与其让人写完半天
    才被 409 打回，不如一开始就说清「现在轮不到你」。
    """
    from fastapi import HTTPException

    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.accept_invite(db, store.create_invite(db, row, alice).token, bob)

    store.save_data(db, row, {"v": 1}, None, identity=alice)
    with pytest.raises(HTTPException) as caught:
        store.save_data(db, row, {"v": 2}, None, identity=bob)
    assert caught.value.status_code == 409
    assert "小明" in caught.value.detail, "得说清是谁占着"


def test_占用会自己过期(db, owned):
    """浏览器直接关掉、网断了、人走了，这些情况下没人会来解锁——
    一把解不开的锁比没有锁更糟。
    """
    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.save_data(db, row, {"v": 1}, None, identity=alice)

    row.editing_at = datetime.now(UTC) - store.LOCK_TTL - timedelta(seconds=1)
    db.commit()
    assert store.lock_holder(row) is None

    store.save_data(db, row, {"v": 2}, None, identity=bob)
    assert row.data["v"] == 2


def test_可以接管(db, owned):
    """占用五分钟才过期，而人可能只是开着标签页去吃饭了。没有这个口子，
    另一个人只能干等——那种等待没有任何用户价值。
    """
    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.save_data(db, row, {"v": 1}, None, identity=alice)

    store.claim_lock(db, row, bob, force=True)
    assert store.lock_holder(row)[0] == bob.user_id
    store.save_data(db, row, {"v": 2}, None, identity=bob)
    assert row.data["v"] == 2


def test_只有占着的人能主动让出(db, owned):
    """不然点一下就能把别人踢掉，锁等于没有——接管有它自己的口子（force），
    而那条路是显式的、会留痕的。
    """
    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.save_data(db, row, {"v": 1}, None, identity=alice)

    store.release_lock(db, row, bob)
    assert store.lock_holder(row) is not None, "别人放不掉"

    store.release_lock(db, row, alice)
    assert store.lock_holder(row) is None


def test_自己连着改不会把自己锁在外面(db, owned):
    from app import store

    row, alice = owned
    for i in range(3):
        store.save_data(db, row, {"v": i}, None, identity=alice)
    assert row.data["v"] == 2


def test_协作者删不掉项目_只有创建者能删(db, owned):
    """唯一一档不平权的操作。删除不可逆，而且会把历史版本一起 CASCADE 掉——
    历史本来是给误操作兜底的，删项目恰恰把那张网也撤了。
    """
    from fastapi import HTTPException

    from app import store

    row, alice = owned
    bob = _identity("小红")
    store.accept_invite(db, store.create_invite(db, row, alice).token, bob)

    with pytest.raises(HTTPException) as caught:
        store.require_owner(row, bob, "删除这个项目")
    # 403 而不是 404：他本来就看得见这个项目，没有存在性需要藏，
    # 回 404 只会让人以为项目没了。
    assert caught.value.status_code == 403
    assert "创建者" in caught.value.detail

    # 别的都还能做
    store.save_data(db, row, {"v": 1}, None, identity=bob)
    assert row.data["v"] == 1

    # 创建者自己可以
    store.require_owner(row, alice, "删除这个项目")


def test_dev_照样能删_他本来就能跨用户读写且已落审计(db, owned):
    from app import store
    from app.auth import Identity

    row, _alice = owned
    dev = Identity(uuid.uuid4(), "运维", "dev@ruc.edu.cn", "dev")
    assert dev.is_dev, "这条用例的前提是 dev 角色判定生效"
    store.require_owner(row, dev, "删除这个项目")
