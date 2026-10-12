"""Alembic 运行环境：连 YVRUC_DATABASE_URL，拿模型的 metadata 做比对。

backend.migrate 会把已经持有迁移锁的连接放进 config.attributes["connection"]，
这时就用那一条连接；直接用 alembic 命令行时自己开一条。
"""
from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.types import TypeDecorator

from backend import config as app_config
from backend import models  # noqa: F401  注册全部模型
from backend.database import Base

target_metadata = Base.metadata


def render_item(type_, obj, autogen_context):
    """自定义列类型（如加密字符串）在库里就是它的底层类型，迁移里照底层类型写。

    迁移文件要能脱离当时的模型代码单独运行：写成 backend.crypto.EncryptedString，
    以后那个类改名或挪走，旧迁移就跑不起来了。
    """
    if type_ == "type" and isinstance(obj, TypeDecorator):
        return f"sa.{obj.impl!r}"
    return False


def _configure(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata,
                      compare_type=True, compare_server_default=True,
                      render_item=render_item)


def run_migrations_offline() -> None:
    context.configure(url=app_config.DATABASE_URL, target_metadata=target_metadata,
                      literal_binds=True, compare_type=True,
                      render_item=render_item)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = create_engine(app_config.DATABASE_URL, future=True)
    try:
        with engine.connect() as conn:
            _configure(conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
