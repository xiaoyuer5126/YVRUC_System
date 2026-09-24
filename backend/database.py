"""数据库会话与建表。

连 RUCVA_DATABASE_URL 指向的 PostgreSQL（compose 内为 postgres 服务）。
连不上就启动失败：静默降级到别的库只会让人对着一份空数据发懵。
"""
from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True,
                       pool_size=config.DB_POOL_SIZE,
                       max_overflow=config.DB_MAX_OVERFLOW)

SessionLocal = sessionmaker(bind=engine, autoflush=False,
                            expire_on_commit=False, future=True)


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
