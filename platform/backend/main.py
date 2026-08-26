"""人大校青协志愿服务管理平台 · FastAPI 入口。

单进程同时提供：
  · /api/**      业务接口
  · /assets/**   前端构建产物（web/dist）
  · /            SPA 回退（hash 路由的兜底直链）
  · 候补转正确认窗口的后台清扫协程（每 60s 一轮）
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, locks
from .database import SessionLocal, init_db
from .enroll_logic import expire_stale_promotions
from .routers import all_routers

logger = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
SWEEP_INTERVAL_SECONDS = 60


def _sweep_once() -> int:
    db = SessionLocal()
    try:
        return expire_stale_promotions(db)
    finally:
        db.close()


async def _promotion_sweeper():
    """候补转正超时未确认 → 记放弃 + 顺延下一位。

    一次异常不能让循环退出：它一停，超时的候补就永远卡在「待确认」，
    后面排队的人再也等不到名额，而这件事没有任何外部表现。
    """
    while True:
        try:
            expired = await asyncio.to_thread(_sweep_once)
            if expired:
                logger.info("清理了 %d 条超时未确认的候补转正", expired)
        except Exception:  # noqa: BLE001
            logger.exception("候补清扫出错")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s [%(name)s] %(message)s")
    locks.ping()          # Redis 是硬依赖：连不上就别假装能扛住抢报
    logger.info("数据库：%s", config.DATABASE_URL.split("@")[-1])
    if config.DEMO_LOGIN_PASSWORD:
        logger.warning("演示登录已启用（口令登录）。接入统一身份认证后请清空 "
                       "RUCVA_DEMO_PASSWORD")
    task = asyncio.create_task(_promotion_sweeper())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def create_app() -> FastAPI:
    docs = "/docs" if config.EXPOSE_API_DOCS else None
    app = FastAPI(title="人大校青协志愿服务管理平台", version="1.0.0",
                  lifespan=lifespan, docs_url=docs, redoc_url=None,
                  openapi_url="/openapi.json" if config.EXPOSE_API_DOCS else None)
    init_db()
    for r in all_routers:
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    if WEB_DIST.exists():
        app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"),
                  name="assets")

        @app.get("/{path:path}")
        def spa(path: str):
            file = WEB_DIST / path
            if path and file.is_file():
                # 带内容哈希的静态资源：可永久缓存
                resp = FileResponse(file)
                resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
                return resp
            # 入口页绝不缓存——否则发新版后浏览器拿着旧 hash 清单，
            # 懒加载的页面 chunk 404，表现就是"点进去一片空白"
            resp = FileResponse(WEB_DIST / "index.html")
            resp.headers["Cache-Control"] = "no-cache"
            return resp
    return app


app = create_app()
