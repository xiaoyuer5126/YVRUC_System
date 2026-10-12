"""数据库迁移。

表结构只由迁移建、只由迁移改。这几条用例钉住：
- 迁移跑完的结构就是模型声明的结构（改了模型忘写迁移，这里红）；
- 空库升到最新，和直接按模型建表，得到的表、列、索引、约束一模一样；
- 迁移只有一个 head；
- 库版本不对时后端拒绝启动；
- 几个进程同时跑迁移，只有一个真去建表，其余等它跑完；
- 空库升到最新就有校青协的 12 个部门，库里已有部门时迁移不动它们。
"""
from __future__ import annotations

import os
import threading
from contextlib import contextmanager

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from backend import database, migrate
from backend.database import Base, engine
from conftest import ADMIN, login

TEST_URL = make_url(os.environ["YVRUC_DATABASE_URL"])


@contextmanager
def scratch_db(suffix: str):
    """临时建一个空库，用完删掉。名字带上测试库名，免得和别的任务撞上。"""
    name = f"{TEST_URL.database}_{suffix}"
    admin = create_engine(TEST_URL.set(database="postgres"),
                          isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    eng = create_engine(TEST_URL.set(database=name), future=True)
    try:
        yield eng
    finally:
        eng.dispose()
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


def structure(eng) -> dict[str, list]:
    """从系统目录读出 public 下的完整结构，迁移自己的版本表除外。"""
    skip = "alembic_version"
    with eng.connect() as conn:
        columns = conn.execute(text("""
            SELECT c.relname, a.attname, format_type(a.atttypid, a.atttypmod),
                   a.attnotnull, pg_get_expr(d.adbin, d.adrelid)
            FROM pg_attribute a
            JOIN pg_class c ON c.oid = a.attrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            LEFT JOIN pg_attrdef d ON d.adrelid = a.attrelid AND d.adnum = a.attnum
            WHERE n.nspname = 'public' AND c.relkind = 'r' AND c.relname <> :skip
              AND a.attnum > 0 AND NOT a.attisdropped
            ORDER BY 1, 2"""), {"skip": skip}).all()
        indexes = conn.execute(text("""
            SELECT tablename, indexname, indexdef FROM pg_indexes
            WHERE schemaname = 'public' AND tablename <> :skip
            ORDER BY 1, 2"""), {"skip": skip}).all()
        constraints = conn.execute(text("""
            SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
            FROM pg_constraint k
            JOIN pg_class c ON c.oid = k.conrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'public' AND c.relname <> :skip
            ORDER BY 1, 2"""), {"skip": skip}).all()
    return {"columns": [tuple(r) for r in columns],
            "indexes": [tuple(r) for r in indexes],
            "constraints": [tuple(r) for r in constraints]}


def test_迁移后的库与模型一致():
    """等价于 alembic check：改了模型却没写迁移，这里会列出差在哪。"""
    with engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True,
                                                     "compare_server_default": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == []


def test_空库升级与按模型建表结构完全一致():
    with scratch_db("mig") as migrated, scratch_db("ca") as created:
        migrate.upgrade(migrated)
        Base.metadata.create_all(created)
        got, want = structure(migrated), structure(created)
    assert want["columns"], "按模型建表却什么都没读到，比对就失去意义了"
    assert any("WHERE" in r[2] for r in want["indexes"]), "部分唯一索引也要比到"
    assert got == want


def test_迁移只有一个head且测试库在最新版本():
    assert migrate.head_revision() == migrate.current_revision()


def test_库版本不对时拒绝启动():
    head = migrate.head_revision()
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'stale'"))
    try:
        with pytest.raises(RuntimeError, match="python -m backend.migrate"):
            database.check_schema()
    finally:
        with engine.begin() as conn:
            conn.execute(text("UPDATE alembic_version SET version_num = :v"),
                         {"v": head})
    database.check_schema()


def test_没有版本记录的库拒绝启动():
    with scratch_db("bare") as bare:
        Base.metadata.create_all(bare)
        with pytest.raises(RuntimeError, match="没有版本记录"):
            database.check_schema(bare)


def test_并发跑迁移只建一次表():
    with scratch_db("race") as eng:
        errors: list[BaseException] = []

        def run():
            try:
                migrate.upgrade(eng)
            except BaseException as e:  # noqa: BLE001  线程里的异常要带回主线程判
                errors.append(e)

        threads = [threading.Thread(target=run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert migrate.current_revision(eng) == migrate.head_revision()


DEPARTMENTS = ["对外联络部", "办公室", "新媒体技术部", "组织部", "支教项目部",
               "红十字项目部", "环保项目部", "青春健康项目部", "社区服务部",
               "特殊关爱项目部", "校园服务部", "首都服务部"]
LIAISON_NOTE = "【二级志愿团体请选择此部门审批】"


def departments(eng) -> list[tuple[str, str]]:
    with eng.connect() as conn:
        return [tuple(r) for r in conn.execute(text(
            "SELECT name, note FROM departments ORDER BY sort"))]


def upgrade_to(eng, revision: str) -> None:
    cfg = migrate.alembic_config()
    with eng.begin() as conn:
        cfg.attributes["connection"] = conn
        command.upgrade(cfg, revision)


def test_空库升到最新就有12个部门():
    with scratch_db("dept") as eng:
        migrate.upgrade(eng)
        got = departments(eng)
    assert [name for name, _ in got] == DEPARTMENTS
    assert got[0][1] == LIAISON_NOTE
    assert all(note == "" for _, note in got[1:])


def test_库里已有部门时迁移不铺部门():
    with scratch_db("dept_kept") as eng:
        upgrade_to(eng, "0002")
        with eng.begin() as conn:
            conn.execute(text("INSERT INTO departments (id, name, note, sort) "
                              "VALUES ('dep_manual', '手工建的部', '', 0)"))
        migrate.upgrade(eng)
        assert departments(eng) == [("手工建的部", "")]


def test_部门列表接口给出迁移铺好的部门(client):
    rows = client.get("/api/staff/departments",
                      headers=login(client, ADMIN)).json()
    by_name = {d["name"]: d for d in rows}
    assert [d["name"] for d in rows if d["name"] in DEPARTMENTS] == DEPARTMENTS
    assert by_name["对外联络部"]["note"] == LIAISON_NOTE
