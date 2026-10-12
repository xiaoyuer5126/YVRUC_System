"""数据库迁移：把库升到代码里最新的那一版。

    python -m backend.migrate

部署时由 compose 的 migrate 服务在后端启动前跑一次；后端自己不改表，只在启动时
核对库的版本（见 database.check_schema）。

升级前先拿一把 PostgreSQL 会话级咨询锁：迁移和 seed 碰巧同时跑、或者有人在
后端起着的时候手工又跑了一遍，后来的那个会等前一个跑完，再发现已经是最新、
什么也不做。没有这把锁，两边会同时去建同一张表，其中一个半路报错。
"""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text

from .database import engine

logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parent / "alembic.ini"

# 迁移锁的键。场次锁等业务锁用的是两参数形式（见 locks.py），这里用单参数
# bigint 形式，两者在 PostgreSQL 里是不同的键空间，不会撞上。
MIGRATION_LOCK_KEY = 5_965_828_201


def alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def head_revision() -> str:
    """代码里最新的迁移版本号。"""
    heads = ScriptDirectory.from_config(alembic_config()).get_heads()
    if len(heads) != 1:
        # 两个人各自基于同一版写了迁移、都合进来了，就会出现两个 head。
        # 这时升级顺序没有定义，要先写一条合并迁移（alembic merge）。
        raise RuntimeError(f"迁移出现了 {len(heads)} 个分支 {heads}，"
                           "先用 alembic -c backend/alembic.ini merge heads 合成一条")
    return heads[0]


def current_revision(bind: Engine = engine) -> str | None:
    """库现在的迁移版本号；没有版本表（空库、或不是用迁移建的库）时为 None。"""
    with bind.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()


def upgrade(bind: Engine = engine) -> None:
    cfg = alembic_config()
    with bind.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        conn.commit()
        try:
            before = MigrationContext.configure(conn).get_current_revision()
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "head")
            conn.commit()
            after = MigrationContext.configure(conn).get_current_revision()
        finally:
            # 迁移半路出错时事务已经作废，先回滚才能再发语句；不回滚，解锁这一句
            # 也报错，真正的错误反倒被盖住
            conn.rollback()
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()
    if before == after:
        logger.info("数据库已是最新版本 %s", after)
    else:
        logger.info("数据库已从 %s 升级到 %s", before or "空库", after)


def main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s [%(name)s] %(message)s")
    upgrade()


if __name__ == "__main__":
    main()
