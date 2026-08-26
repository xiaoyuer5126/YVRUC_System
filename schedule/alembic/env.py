from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text

from app.config import DB_SCHEMA, get_settings
from app.db import Base
from app import models  # noqa: F401  确保表定义被注册进 metadata

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.sqlalchemy_database_url)
target_metadata = Base.metadata


def _ensure_schema(connectable) -> None:
    """确认 schema 在。不在的话尽力建，建不了就说一句能照做的话。

    ⚠️ **不能用 `CREATE SCHEMA IF NOT EXISTS` 一句了事。** PostgreSQL 会先检查
    「当前角色对这个数据库有没有 CREATE 权限」，再判断 schema 存不存在——顺序
    就是这样，`IF NOT EXISTS` 不会让它跳过权限检查。而 `tool_schedule` 这个角色
    *故意*没有库级 CREATE 权限（它只拥有自己那一个 schema），于是即使 schema
    早就建好、owner 还是它自己，那句话照样报
    `permission denied for database <库名>`，容器起不来。

    ⚠️ 而且这件事**必须用一条独立的连接**做。SQLAlchemy 2.0 的连接会在第一条
    语句上自动开事务；如果在迁移那条连接上先查一次再直接返回，事务就一直开着，
    alembic 的 `begin_transaction()` 会认为外层已经在管事务而不再提交，
    连接关闭时全部 DDL 被回滚——**迁移日志照常打印「Running upgrade」，
    但一张表都不会建出来**，安静得可怕。
    """
    with connectable.connect() as conn:
        exists = conn.scalar(
            text("select 1 from pg_namespace where nspname = :name"), {"name": DB_SCHEMA}
        )
        if exists:
            return
        try:
            conn.execute(text(f'CREATE SCHEMA "{DB_SCHEMA}"'))
            conn.commit()
        except Exception as exc:  # noqa: BLE001 只为把错误换成一句能照做的话
            conn.rollback()
            raise RuntimeError(
                f'schema "{DB_SCHEMA}" 不存在，当前角色也没有权限创建它。\n'
                "这一步本该由 deploy/postgres-init/ 里的脚本在数据库首次初始化时完成；\n"
                "已经初始化过的实例要手工建一次，见 docs/DEPLOYMENT.md 15.3：\n"
                f"  CREATE SCHEMA {DB_SCHEMA} AUTHORIZATION {DB_SCHEMA};"
            ) from exc


def run_migrations_offline() -> None:
    context.configure(
        url=settings.sqlalchemy_database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        version_table_schema=DB_SCHEMA,
        include_schemas=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    # 先用一条独立连接确认 schema，再开迁移那条——理由见 _ensure_schema。
    _ensure_schema(connectable)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # alembic_version 也要落在自己的 schema 里，否则两个服务会在
            # public.alembic_version 上打架——社区的版本号会被这里覆盖掉。
            version_table_schema=DB_SCHEMA,
            include_schemas=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
