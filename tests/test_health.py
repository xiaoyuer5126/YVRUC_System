"""健康探针：它得在任何情况下都答得上来。

compose 靠它决定这个容器是死是活（连续失败就摘出去，表现是全站 502），
所以它不能排在业务请求后面。
"""
from __future__ import annotations

import inspect

from backend.main import app


def _health_endpoint():
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/health")
    return route.endpoint


def test_健康检查答得上(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_健康检查不占线程池的线程():
    """必须是 async。

    同步的 def 接口跑在线程池里，而线程池是**每个进程共用、有上限的**。
    几百个报名请求一起到的时候线程全被占住，同步的探针就得排队等着——容器被
    判不健康、被摘出去，全站跟着 502。2026-09-25 压测里量到过这一幕：尖峰期间
    5 次探针有 3 次拿不到响应。改成 async 之后同一个尖峰下 31 次探针全部正常。
    """
    assert inspect.iscoroutinefunction(_health_endpoint())


def test_线程池上限按配置设(client):
    """默认 40 条不够抢报用：持号等锁的报名请求会占着线程（见 config.THREADS）。"""
    import anyio.to_thread
    from backend import config

    async def tokens():
        return anyio.to_thread.current_default_thread_limiter().total_tokens

    assert config.THREADS != 40            # 否则这条用例测不出有没有设上
    assert client.portal.call(tokens) == config.THREADS
