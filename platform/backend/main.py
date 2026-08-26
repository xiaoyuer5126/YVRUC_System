"""人大校青协志愿服务管理平台 · FastAPI 入口。

单进程同时提供：
  · /api/**       业务接口
  /static/**     前端构建产物（web/dist）
  · /             SPA 回退（history 路由）
  · 候补转正确认窗口的后台清扫线程（每 60s 一轮，惰性判定兜底）
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .database import ACTIVE_URL, init_db
from .routers import all_routers

logger = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"


async def _promotion_sweeper():
    """候补转正超时未确认 → 记放弃 + 顺延下一位。"""
    from .database import SessionLocal
    from .enroll_logic import expire_stale_promotions
    while True:
        try:
            await asyncio.to_thread(_sweep_once)
        except Exception:  # noqa: BLE001
            logger.exception("候补清扫出错")
        await asyncio.sleep(60)


def _sweep_once() -> int:
    from .database import SessionLocal
    db = SessionLocal()
    try:
        return expire_stale_promotions(db)
    finally:
        db.close()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s [%(name)s] %(message)s")
    logger.info("数据库：%s", ACTIVE_URL)
    task = asyncio.create_task(_promotion_sweeper())
    yield
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def create_app() -> FastAPI:
    app = FastAPI(title="人大校青协志愿服务管理平台", version="1.0.0-demo",
                  lifespan=lifespan)
    init_db()
    for r in all_routers:
        app.include_router(r)

    @app.get("/api/health")
    def health():
        return {"status": "ok", "db": ACTIVE_URL.split("@")[-1]
                if "@" in ACTIVE_URL else ACTIVE_URL}

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
