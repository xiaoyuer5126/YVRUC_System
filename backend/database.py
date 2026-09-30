"""数据库会话与建表。

连 RUCVA_DATABASE_URL 指向的 PostgreSQL（compose 内为 postgres 服务）。
连不上就启动失败：静默降级到别的库只会让人对着一份空数据发懵。
"""
from __future__ import annotations

import logging

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from . import config

logger = logging.getLogger(__name__)

engine = create_engine(config.DATABASE_URL, pool_pre_ping=True, future=True,
                       pool_size=config.DB_POOL_SIZE,
                       max_overflow=config.DB_MAX_OVERFLOW)

SessionLocal = sessionmaker(bind=engine, autoflush=False,
                            expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def _model_metadata(metadata):
    """拿到模型那份 metadata；没传就先确保模型已注册。

    模型没导入时 `Base.metadata` 是**空的**，比对会得出「哪儿都一致」——自检
    反倒成了哑巴。所有入口都走这里，免得依赖调用顺序。
    """
    if metadata is not None:
        return metadata
    from . import models  # noqa: F401
    return Base.metadata


def schema_gaps(metadata=None, inspector=None) -> list[str]:
    """模型声明了、库里却没有的表和列。空列表 = 一致。

    这个项目**没有迁移工具**，`create_all` 只建缺失的表、从不给已有的表加列。
    所以少了这一步，「模型加了列、库里没有」就不在启动时暴露，而是跑到某个
    页面才 500——前端把 500 渲染成空列表，看着像数据丢了。
    """
    metadata = _model_metadata(metadata)
    insp = inspect(engine) if inspector is None else inspector
    gaps: list[str] = []
    for table in metadata.sorted_tables:
        if not insp.has_table(table.name):
            gaps.append(f"{table.name}：整张表都没有")
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        missing = sorted({c.name for c in table.columns} - have)
        if missing:
            gaps.append(f"{table.name}：缺列 {missing}")
    return gaps


def extra_tables(metadata=None, inspector=None) -> list[str]:
    """库里有、模型里没有的表。改名留下的旧表，不影响运行，提醒一句。"""
    metadata = _model_metadata(metadata)
    insp = inspect(engine) if inspector is None else inspector
    known = {t.name for t in metadata.sorted_tables}
    return sorted(set(insp.get_table_names()) - known - {"spatial_ref_sys"})


def check_schema() -> None:
    """启动时对一遍模型与库。不一致就别起来——起也是半死不活。

    比起「跑到哪个页面才 500」，在启动那一秒把话说明白更省事：那 5 列是哪个
    表缺的、怎么补，都写在报错里。库真的落后时容器会反复重启，日志里一直
    有这一条，不会被漏看。
    """
    gaps = schema_gaps()
    if gaps:
        raise RuntimeError(
            "数据库与模型不一致，先修库再启动：\n  " + "\n  ".join(gaps)
            + "\n（本项目没有迁移工具，create_all 只建缺失的表、不会给已有的表加列。"
              "开发环境重跑 seed：docker compose --profile seed run --rm --build seed；"
              "要保住数据就手工 ALTER TABLE … ADD COLUMN …）")
    leftovers = extra_tables()
    if leftovers:
        logger.warning("库里还有模型里没有的表（改名留下的旧表，不影响运行）：%s",
                       "、".join(leftovers))


def init_db() -> None:
    from . import models  # noqa: F401  确保模型注册完毕
    Base.metadata.create_all(engine)
    check_schema()


def db_session():
    """FastAPI 依赖：每请求一个会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
