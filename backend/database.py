"""数据库会话。

连 YVRUC_DATABASE_URL 指向的 PostgreSQL（compose 里默认是 postgres 容器，也可以是外部的库）。
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


def check_schema(bind=None) -> None:
    """启动时核对库的迁移版本。不是代码里最新的那一版就别起来。

    表结构只由迁移改（python -m backend.migrate，部署时 compose 的 migrate 服务
    先跑它）。版本对不上时硬起来，新加的列在库里没有，要跑到某个页面才 500，
    前端把 500 渲染成空列表，看着像数据丢了。所以在启动那一秒把话说明白；
    容器会反复重启，日志里一直有这一条，不会被漏看。
    """
    from . import migrate
    head = migrate.head_revision()
    current = migrate.current_revision(bind or engine)
    if current != head:
        raise RuntimeError(
            f"数据库版本 {current or '（没有版本记录）'} 不是代码要求的 {head}，先升级再启动：\n"
            "  docker compose run --rm migrate\n"
            "（不用 compose 时：python -m backend.migrate。库比代码还新，说明部署的是"
            "旧代码，换回对应版本的代码。）")


def db_session():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
