"""报名并发控制：场次粒度的 Redis 分布式锁。

抢报瞬间「数已报名人数 → 判断还有没有位 → 写一条报名」是读-改-写，
多个进程/多个 worker 同时跑就会双双读到「还剩 1 个位」而双双写入，
表现为超卖；同一个人双击也会插出两条。所以容量判断与落库整段串行化
到 group 粒度——锁的是场次，不同场次互不阻塞，抢报高峰照样并行。

唯一索引（models.Enrollment.__table_args__）是第二道防线：锁没拿住的
极端情况（Redis 重启、锁过期）仍然插不进重复报名。
"""
from __future__ import annotations

import contextlib
import secrets
import time

import redis
from fastapi import HTTPException

from . import config

_client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

LOCK_TTL_MS = 5_000      # 锁自动过期：持锁进程崩了也不会把场次锁死
WAIT_TIMEOUT = 3.0       # 等锁上限，超过就让用户重试而不是干等
RETRY_INTERVAL = 0.02

# 只删自己那把锁：TTL 到期后锁可能已被别人持有，无条件 del 会误删。
_RELEASE = _client.register_script("""
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
""")


def ping() -> None:
    """启动自检。连不上就让进程起不来。"""
    _client.ping()


# —— 失败计数（登录爆破防护）——
#
# 只统计**失败**：整个学院可能共用一个 NAT 出口，按登录次数计会把正常用户
# 一起挡在外面。Redis 不可用时一律放行——加固不该变成新的单点故障。

def _fail_key(key: str) -> str:
    return f"rucva:fail:{key}"


def failure_count(key: str) -> int:
    try:
        return int(_client.get(_fail_key(key)) or 0)
    except redis.RedisError:
        return 0


def note_failure(key: str, window_seconds: int) -> None:
    try:
        pipe = _client.pipeline()
        pipe.incr(_fail_key(key))
        pipe.expire(_fail_key(key), window_seconds)
        pipe.execute()
    except redis.RedisError:
        pass


def clear_failures(key: str) -> None:
    with contextlib.suppress(redis.RedisError):
        _client.delete(_fail_key(key))


@contextlib.contextmanager
def shift_lock(shift_id: str):
    """锁住一个场次的名额账本。"""
    key = f"rucva:lock:group:{shift_id}"
    token = secrets.token_hex(8)
    deadline = time.monotonic() + WAIT_TIMEOUT
    try:
        while not _client.set(key, token, nx=True, px=LOCK_TTL_MS):
            if time.monotonic() >= deadline:
                raise HTTPException(503, "当前操作人数较多，请稍后重试")
            time.sleep(RETRY_INTERVAL)
    except redis.RedisError as exc:
        raise HTTPException(503, "报名服务暂时不可用，请稍后重试") from exc
    try:
        yield
    finally:
        with contextlib.suppress(redis.RedisError):
            _RELEASE(keys=[key], args=[token])
