"""数据库会话与建表。

默认连 Docker 容器里的 PostgreSQL（见 platform/docker-compose.yml）；
引擎创建时做一次可达性探测，失败则降级 SQLite——演示机上没有 Docker
也能把平台跑起来。生产切库只改 RUCVA_DATABASE_URL，代码不动。
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

log = logging.getLogger(__name__)


def _resolve() -> tuple[str, any]:
    """返回 (生效连接串, engine)。Postgres 连不上时降级 SQLite。"""
    if config.DATABASE_URL.startswith("sqlite"):
        return config.DATABASE_URL, create_engine(config.DATABASE_URL, future=True)
    try:
        probe = create_engine(config.DATABASE_URL, connect_args={"connect_timeout": 3},
                              pool_pre_ping=True)
        with probe.connect() as conn:
            conn.execute(text("SELECT 1"))
        return config.DATABASE_URL, probe
    except Exception as exc:  # noqa: BLE001  降级是刻意的兜底
        log.warning("PostgreSQL(%s) 不可达（%s），降级 SQLite 演示库",
                    config.DATABASE_URL, exc.__class__.__name__)
        return config.SQLITE_URL, create_engine(config.SQLITE_URL, future=True)


ACTIVE_URL, engine = _resolve()

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册完毕
    Base.metadata.create_all(engine)


def db_session():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
