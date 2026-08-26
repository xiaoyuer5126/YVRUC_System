from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db import SessionLocal
from app.origin_guard import OriginGuardMiddleware
from app.routes import projects
from app.store import purge_expired

# 所有接口都挂在这个前缀下，和前端路由 /tools/schedule、compose 服务名
# tool-schedule、数据库 schema tool_schedule 一一对应。
API_PREFIX = "/api/tools/schedule"

# 清理过期项目的间隔。到期日是天级的，一小时一轮足够准。
PURGE_INTERVAL_SECONDS = 3600

logger = logging.getLogger(__name__)


async def _purger() -> None:
    """定时删掉过期项目。

    一次异常不能让循环退出——它一停，过期数据（全员联系方式）就再也不会被清了，
    而这件事没有任何外部表现，直到某天有人去翻库才发现。
    """
    while True:
        try:
            removed = await asyncio.to_thread(_purge_once)
            if removed:
                logger.info("清理了 %d 个过期项目", removed)
        except Exception:  # noqa: BLE001
            logger.exception("清理过期项目出错")
        await asyncio.sleep(PURGE_INTERVAL_SECONDS)


def _purge_once() -> int:
    db = SessionLocal()
    try:
        return purge_expired(db)
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # 配错的表现是「所有写操作 403」，而 403 本身完全看不出是白名单的问题。
    logger.info("允许写操作的来源：%s", settings.allowed_origins)
    logger.info("项目保留 %d 天，排班上限 %g+%g=%g 秒", settings.retention_days,
                settings.solve_main_seconds, settings.solve_fallback_seconds,
                settings.solve_max_seconds)

    task = asyncio.create_task(_purger())
    try:
        yield
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


def create_app() -> FastAPI:
    # ⚠️ 这一句不能省。uvicorn 只配置它自己那几个 logger（uvicorn.error /
    # uvicorn.access），**根 logger 一个 handler 都没有**——于是应用里所有
    # logger.info / logger.exception 全被安静地吞掉，包括「清理过期项目出错」
    # 这种最需要看见的。表现是容器日志里只有访问日志，出了事什么线索都没有。
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")

    settings = get_settings()
    app = FastAPI(
        title=settings.project_name,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.add_middleware(OriginGuardMiddleware)
    app.include_router(projects.router, prefix=API_PREFIX)

    @app.get(f"{API_PREFIX}/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
