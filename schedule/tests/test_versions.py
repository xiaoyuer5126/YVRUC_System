"""历史版本。需要一个真的 Postgres——这一套全是数据库行为:JSONB 快照、
外键级联、条数裁剪、时间窗口合并,用假仓库测都测不出真东西。

    SCHEDULE_TEST_DATABASE_URL=postgresql://tool_schedule:...@127.0.0.1:5433/rucommunity

没设就跳过。
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

    # 和 config.sqlalchemy_database_url 同一套归一化:裸 postgresql:// 会被
    # SQLAlchemy 派给 psycopg2,而这个项目装的是 psycopg3。
    url = DB_URL
    for prefix, repl in (("postgres://", "postgresql+psycopg://"),
                         ("postgresql://", "postgresql+psycopg://")):
        if url.startswith(prefix) and not url.startswith("postgresql+"):
            url = url.replace(prefix, repl, 1)
            break
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


def _identity(nickname="小明", user_id=None):
    from app.auth import Identity

    return Identity(user_id or uuid.uuid4(), nickname, f"{nickname}@ruc.edu.cn", "user")


@pytest.fixture
def project(db):
    from app import store

    me = _identity()
    row = store.create(db, me.user_id, "历史测试", None, me.nickname)
    yield row, me
    db.delete(row)
    db.commit()


def _save(db, row, me, action="", **marks):
    """改一点点再存。marks 用来让每次的 data 不一样，方便断言存对了哪一版。"""
    from app import store

    data = dict(row.data or {})
    data.update(marks)
    return store.save_data(db, row, data, None, identity=me, action=action)


# ————————————————————————————————————————— 存什么

def test_重要节点每次单独存一版(db, project):
    from app import store

    row, me = project
    for i in range(3):
        _save(db, row, me, action="应用排班", mark=i)

    versions = store.list_versions(db, row.id)
    assert len(versions) == 3
    assert [v.action for v in versions] == ["应用排班"] * 3
    # 最新的排最前
    assert versions[0].data["mark"] == 2


def test_手动微调在窗口内合并成一版(db, project):
    """勾个锁、调个岗都是一次 PUT。全存下来的话 50 版可能只覆盖十分钟的操作，
    根本回不到「昨天那一版」——那这个功能就白做了。
    """
    from app import store

    row, me = project
    for i in range(5):
        _save(db, row, me, mark=i)

    versions = store.list_versions(db, row.id)
    assert len(versions) == 1, "窗口内的连续微调该攒成一版"
    assert versions[0].data["mark"] == 4, "留的是最后一次的内容"
    assert versions[0].saved_at > versions[0].created_at, "窗口起点不该跟着往后挪"


def test_窗口锚在起点_不会跟着最后一次编辑一直往后滑(db, project):
    """拿最后一次编辑当锚点的话，一个人连着改一小时也只会留下一版，
    而那一版里什么都恢复不到。
    """
    from app import store
    from app.models import ScheduleProjectVersion

    row, me = project
    _save(db, row, me, mark=0)

    # 必须按项目过滤：整张表里还有别的用例留下的版本。
    latest = db.query(ScheduleProjectVersion).filter_by(project_id=row.id).one()
    # 把窗口起点推到 4 分钟前（窗口是 3 分钟），saved_at 保持刚刚
    latest.created_at = datetime.now(UTC) - timedelta(minutes=4)
    db.commit()

    _save(db, row, me, mark=1)
    assert len(store.list_versions(db, row.id)) == 2, "超出窗口该开新的一版"


def test_重要节点不参与合并(db, project):
    from app import store

    row, me = project
    _save(db, row, me, mark=0)
    _save(db, row, me, action="清空岗位", mark=1)
    _save(db, row, me, mark=2)

    versions = store.list_versions(db, row.id)
    assert [v.action for v in versions] == ["", "清空岗位", ""], \
        "重要节点两边的微调不该跨过它合并"


def test_换个人就不合并(db, project):
    """两个人在同一个窗口里各改各的，攒成一版就说不清是谁改的了。

    要走到这一步必须先**接管编辑权**——同一时刻只有一个人能改（见 test_collab）。
    这里顺带证明两件事是搭得上的：接管之后的改动记在接管者名下，
    而不是被并进前一个人那一版里。
    """
    from app import store

    row, me = project
    other = _identity("小红")
    _save(db, row, me, mark=0)
    store.claim_lock(db, row, other, force=True)
    _save(db, row, other, mark=1)

    versions = store.list_versions(db, row.id)
    assert len(versions) == 2
    assert {v.actor_nickname for v in versions} == {"小明", "小红"}


# ————————————————————————————————————————— 存多少

def test_最多留五十版_超出的从最旧的删起(db, project):
    from app import store

    row, me = project
    for i in range(store.MAX_VERSIONS + 8):
        _save(db, row, me, action=f"第{i}次", mark=i)

    versions = store.list_versions(db, row.id)
    assert len(versions) == store.MAX_VERSIONS
    marks = [v.data["mark"] for v in versions]
    assert marks[0] == store.MAX_VERSIONS + 7, "最新的还在"
    assert min(marks) == 8, "最旧的那 8 版被删了"


# ————————————————————————————————————————— 删项目要跟着删

def test_删项目时历史一起没(db):
    """快照里同样有姓名、身份证号、联系方式。留下一份「项目已删但历史还在」
    的名单，等于「删是真删」这句话不成立。
    """
    from app import store
    from app.models import ScheduleProjectVersion

    me = _identity()
    row = store.create(db, me.user_id, "待删", None, me.nickname)
    pid = row.id
    for i in range(3):
        _save(db, row, me, action="应用排班", mark=i)
    assert db.query(ScheduleProjectVersion).filter_by(project_id=pid).count() == 3

    store.remove(db, row)
    assert db.query(ScheduleProjectVersion).filter_by(project_id=pid).count() == 0


def test_到期清理也带走历史(db):
    from app import store
    from app.models import ScheduleProjectVersion

    me = _identity()
    row = store.create(db, me.user_id, "过期", None, me.nickname)
    pid = row.id
    _save(db, row, me, action="应用排班", mark=0)
    row.expires_at = datetime.now(UTC) - timedelta(days=1)
    db.commit()

    store.purge_expired(db)
    assert db.query(ScheduleProjectVersion).filter_by(project_id=pid).count() == 0


# ————————————————————————————————————————— 恢复

def test_恢复是往前写一版_不是把版本号退回去(db, project):
    """版本号是乐观并发的依据。让它倒退等于把并发保护废掉：另一个标签页拿着
    更大的号，之后的写入会莫名其妙地通过或失败。
    """
    from app import store

    row, me = project
    _save(db, row, me, action="应用排班", mark="旧")
    old = store.list_versions(db, row.id)[0]
    _save(db, row, me, action="清空岗位", mark="新")
    before = row.version

    restored = store.restore_version(db, row, old, me)
    assert restored.data["mark"] == "旧", "内容回到旧版"
    assert restored.version > before, "版本号只会前进"


def test_恢复本身也进历史_所以恢复错了还能恢复回来(db, project):
    from app import store

    row, me = project
    _save(db, row, me, action="应用排班", mark="甲")
    first = store.list_versions(db, row.id)[0]
    _save(db, row, me, action="应用排班", mark="乙")

    store.restore_version(db, row, first, me)
    versions = store.list_versions(db, row.id)
    assert versions[0].action.startswith("恢复到")
    assert versions[0].data["mark"] == "甲"

    # 再恢复回「乙」那一版
    back = next(v for v in versions if v.data.get("mark") == "乙")
    store.restore_version(db, row, back, me)
    assert row.data["mark"] == "乙"


def test_取版本必须带上项目id(db, project):
    """只按 version_id 查的话，拿别人项目的版本 id 就能读到内容，
    项目那道 owner 过滤就白做了。
    """
    from fastapi import HTTPException

    from app import store

    row, me = project
    _save(db, row, me, action="应用排班", mark=0)
    mine = store.list_versions(db, row.id)[0]

    other = store.create(db, uuid.uuid4(), "别人的", None, "小红")
    try:
        with pytest.raises(HTTPException) as caught:
            store.get_version(db, other.id, mine.id)
        assert caught.value.status_code == 404
    finally:
        db.delete(other)
        db.commit()
