"""志愿服务管理平台 · FastAPI 入口。

每个进程同时提供：
  · /api/**      业务接口
  · /assets/**   前端构建产物（web/dist）
  · /            SPA 回退（前端路由是真实路径，/my、/manage/xxx 都回 index.html）

进程数由部署决定（Dockerfile CMD 读 YVRUC_WORKERS，默认 4）。后台清扫协程
也是每个进程各起一份，靠 Redis 租约选主，同一时刻只有主进程在跑，见
locks.hold_sweeper_lease。
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from pathlib import Path

import anyio.to_thread
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import config, crypto, integrations, locks, monitoring, oplog, scheduled
from .database import SessionLocal, check_schema
from .routers import all_routers

logger = logging.getLogger(__name__)

WEB_DIST = Path(__file__).resolve().parents[1] / "web" / "dist"
SWEEP_INTERVAL_SECONDS = 60


def _sweep_once() -> dict[str, int]:
    db = SessionLocal()
    try:
        return scheduled.run_once(db)
    finally:
        db.close()


async def _sweeper(token: str):
    """定时任务循环：候补清扫、活动状态流转、签到提醒（见 scheduled.py）。

    一次异常不能让循环退出：它一停，超时的候补永远卡在「待确认」、活动永远
    停在「报名中」、签到提醒再也不发，而这些都没有任何外部表现。

    跑多个进程时每个进程都会进到这里，所以每轮先抢一次租约（locks.py 的
    选主）——否则提醒按进程数发几遍，收到的是真人。
    """
    while True:
        try:
            if locks.hold_sweeper_lease(token):
                done = await asyncio.to_thread(_sweep_once)
                if any(done.values()):
                    logger.info("定时任务：候补清理 %(promotions)d、状态流转 %(advanced)d、"
                                "签到提醒 %(reminders)d", done)
        except Exception:  # noqa: BLE001
            logger.exception("定时任务循环出错")
        await asyncio.sleep(SWEEP_INTERVAL_SECONDS)


class _NoHealthAccess(logging.Filter):
    """健康探针与地图瓦片不进访问日志。

    容器编排每隔几十秒探一次 /api/health，日志里全是它，真正要看的请求被冲走。
    地图瓦片一开就是几十张，而且查询串里带着瓦片令牌（见 routers/geo.py）。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        return "/api/health" not in msg and "/api/geo/tiles/" not in msg


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    logging.basicConfig(level=logging.INFO,
                        format="%(levelname)s [%(name)s] %(message)s")
    logging.getLogger("uvicorn.access").addFilter(_NoHealthAccess())
    # 线程池上限按进程设，只能在事件循环里设（见 config.THREADS）
    anyio.to_thread.current_default_thread_limiter().total_tokens = config.THREADS
    locks.ping()          # Redis 是硬依赖：连不上就别假装能扛住抢报
    crypto.check()        # 身份证号的密钥：不对就别起来，别等到有人填资料
    integrations.check()  # 学校对接：开关开了参数却没填齐、或配了短信，不让起来
    logger.info("数据库：%s", config.DATABASE_URL.split("@")[-1])
    if monitoring.enabled():
        logger.info("错误监控已启用：环境 %s，采样率 %s",
                    config.SENTRY_ENVIRONMENT, config.SENTRY_SAMPLE_RATE)
    if config.DEMO_LOGIN_PASSWORD:
        logger.warning("演示登录已启用（口令登录）。接入统一身份认证后请清空 "
                       "YVRUC_DEMO_PASSWORD")
    task, token = None, ""
    if config.RUN_SWEEPER:
        token = locks.sweeper_token()
        task = asyncio.create_task(_sweeper(token))
    yield
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        # 交还主位：不交，下一个进程要等租期到期才接手（重启一次白停五分钟）
        locks.release_sweeper_lease(token)
    # 队列里可能还压着几条操作日志，退出前写完（见 oplog.close）
    oplog.close()


def create_app() -> FastAPI:
    # 错误上报要在建路由之前初始化，晚了只有一部分接口报得出来（见 monitoring.init）
    monitoring.init()
    docs = "/docs" if config.EXPOSE_API_DOCS else None
    app = FastAPI(title="志愿服务管理平台", version="1.0.0",
                  lifespan=lifespan, docs_url=docs, redoc_url=None,
                  openapi_url="/openapi.json" if config.EXPOSE_API_DOCS else None)
    check_schema()
    for r in all_routers:
        app.include_router(r)
    # 操作日志：改数据的请求、登录、导出下载，响应发出后记一条（见 oplog.py）
    app.add_middleware(oplog.OplogMiddleware)

    # async 不是随手写的：同步的 def 接口要占一条线程池的线程，而线程池是**每个
    # 进程共用、有上限的**（config.THREADS）。抢报那种几百个请求一起到的时候，线程全
    # 被报名请求占住，探针拿不到线程就得干等着——容器被判不健康、被摘出负载均衡，
    # 全站 502。它只回一个常量、不碰数据库，本来就不该排队。
    @app.get("/api/health")
    async def health():
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
