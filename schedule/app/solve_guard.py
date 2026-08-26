"""一个人同时只能算一次排班。

前端在算的时候会把「重新排班」按钮禁掉，但那**只挡得住同一个页面上的连点**：
开两个标签页、刷新之后再点、或者直接拿 curl 打，都能绕过去。而一次排班最坏
要占一个核 90 秒，几个并发就能把容器的 CPU 配额吃干，社区那边跟着卡。所以
服务端也要有一道。

按人拦而不是全局排队：一个人自己点两次是误操作，该拦；两个不同的人同时排班
是正常使用，不该让后来的人干等。
"""
from __future__ import annotations

import asyncio
import time
import uuid

from fastapi import HTTPException, status

from app.config import get_settings

# user_id -> 开始时刻（time.monotonic）
_inflight: dict[uuid.UUID, float] = {}
_lock = asyncio.Lock()

# 超过「最长可能耗时 + 这么多秒」的记录当作漏掉的，清掉。
#
# 为什么需要这个兜底：正常情况下标记由工作线程自己清（见下），但如果协程在
# 线程池真正开始执行之前就被取消（请求断得极早），那个 finally 就永远不会跑，
# 这个人会被永久锁住。宁可多放一次，也不能让人再也点不了重排。
STALE_SLACK_SECONDS = 60.0


async def acquire(user_id: uuid.UUID) -> None:
    settings = get_settings()
    stale_after = settings.solve_max_seconds + STALE_SLACK_SECONDS
    now = time.monotonic()
    async with _lock:
        started = _inflight.get(user_id)
        if started is not None and now - started < stale_after:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="你已经有一次排班在算了，等它出结果再点（最长约 "
                       f"{settings.solve_max_seconds:g} 秒）",
            )
        _inflight[user_id] = now


def release(user_id: uuid.UUID) -> None:
    """⚠️ **必须从工作线程里调，不要在请求的 finally 里调。**

    请求被取消（用户关掉标签页）时，`asyncio.to_thread` 起的线程**不会**跟着停，
    它会继续把 90 秒烧完。这时候如果在请求侧就把标记清掉，那个人立刻又能点一次，
    两个线程一起烧——正好是这道闸门要防的事。
    """
    _inflight.pop(user_id, None)


def busy(user_id: uuid.UUID) -> bool:
    """给测试和排查用。"""
    return user_id in _inflight
