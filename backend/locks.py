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
import logging
import os
import secrets
import string
import time

import redis
from fastapi import HTTPException

from . import config

logger = logging.getLogger(__name__)

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


# —— 一次性下载票据 ——
#
# 导出与材料下载是 <a> 直链，发不出 Authorization 头。把会话令牌挂在地址上
# 最省事，但它会原样落进 nginx 访问日志、浏览器历史和外链 Referer，而那是
# 一个 24 小时有效的会话。改发票据：绑定这一个网址、一分钟有效、用一次就
# 作废，进了日志也换不出任何东西。

def issue_ticket(user_id: str, path: str, ttl: int) -> str:
    token = secrets.token_urlsafe(24)
    _client.setex(f"rucva:dl:{token}", ttl, f"{user_id}|{path}")
    return token


def redeem_ticket(token: str, path: str) -> str | None:
    """核销票据，返回用户 id；票据不存在、已用过或对不上这个网址都返回 None。"""
    if not token:
        return None
    raw = _client.getdel(f"rucva:dl:{token}")
    if not raw or "|" not in raw:
        return None
    user_id, want = raw.split("|", 1)
    return user_id if want == path else None


def clear_failures(key: str) -> None:
    with contextlib.suppress(redis.RedisError):
        _client.delete(_fail_key(key))


# —— OAuth state ——
#
# 授权回调是「带着 ?code=..&state=.. 从外部跳回来」的入口。不校验 state，
# 任何人都能构造一条回调链接，把别人登进某个账号——这是登录 CSRF。
# state 一次一用、过期即废，顺带把跳转前想去的页面捎回来。

# —— 定时任务的选主 ——
#
# 定时任务从前靠「只有一个进程」保证不重复跑。多进程之后这条前提没了：每个
# 进程各起一份 _sweeper，签到提醒会发两遍、候补清两遍、日报发两份——这些
# 都有外部表现，且是发给真人的。所以用一个带租期的键选出唯一的主。
#
# 租期取得比一轮（60s）长得多：正常跑一轮远用不到它，主进程只要还活着就
# 一定续得上；反过来它被 kill 时，最多一个租期内就有人接手，而这几个任务
# 本来就是分钟级的（提醒按提前量发、候补按截止时间清），晚一轮没有外部表现。
SWEEPER_KEY = "rucva:sweeper:leader"
SWEEPER_LEASE_SECONDS = 300

# 抢占与续期必须在一段 Lua 里做完：分成「抢」和「看是不是我的」两步的话，
# 中间那一瞬别的进程正好可以抢走，于是两个进程都以为自己持锁。
#
# 返回值要分得开：1 = 新抢到，2 = 续上自己的，0 = 别人持有。续期**不能**也回 1
# （expire 成功本来就返回 1），否则每轮续期都打一行「选主」，日志上看着像
# 每分钟换一次主——真换主的那次就淹在里面了。
_TAKE_LEASE = _client.register_script("""
if redis.call('set', KEYS[1], ARGV[1], 'NX', 'EX', ARGV[2]) then
    return 1
end
if redis.call('get', KEYS[1]) == ARGV[1] then
    if redis.call('expire', KEYS[1], ARGV[2]) == 1 then
        return 2
    end
end
return 0
""")


def sweeper_token() -> str:
    """这一进程的选主标识：pid 打头，便于在 ps 与日志里对号。"""
    return f"{os.getpid()}-{secrets.token_hex(4)}"


def release_sweeper_lease(token: str) -> None:
    """退出时把主位交还（只删自己那把）。不是主进程、或已经过期，都是空操作。

    **不交还的话，重启一次就白停一个租期**：新进程一看键还在（持它的那个
    进程刚被 kill 掉，谁也没告诉），就得多等五分钟才接手。被强杀的进程没
    机会走这一步，所以租期还得留着兜底——正常退出走这里，意外死亡靠租期。
    """
    with contextlib.suppress(redis.RedisError):
        _RELEASE(keys=[SWEEPER_KEY], args=[token])


def hold_sweeper_lease(token: str, ttl: int = SWEEPER_LEASE_SECONDS) -> bool:
    """这一轮定时任务由我来跑吗？拿到、或续上自己的，是；别人持有则不是。

    **Redis 不可用时返回 True**：宁可几个进程各跑一遍，也不能谁都不跑——
    候补永远卡在「待确认」、提醒永远不发，没有任何外部表现，比偶尔重发
    一条消息更难发现。
    """
    try:
        result = int(_TAKE_LEASE(keys=[SWEEPER_KEY], args=[token, ttl]))
    except redis.RedisError:
        logger.warning("Redis 不可用，定时任务选主失效：本轮照跑")
        return True
    if result == 1:      # 抢到的（原本没人，或前任的租期已过）
        logger.info("定时任务选主：由本进程（%s）负责，租期 %ss", token, ttl)
    return result != 0


def issue_sso_state(next_url: str, ttl: int) -> str:
    state = secrets.token_urlsafe(24)
    _client.setex(f"rucva:sso:{state}", ttl, next_url or "")
    return state


def redeem_sso_state(state: str) -> str | None:
    """核销 state，返回当初想去的页面。

    **None 表示这个 state 无效**（伪造、已用过或过期）；空串是合法结果，
    表示「没有指定去向」。调用方必须用 `is None` 判断，不能用真假值——
    两者混为一谈的话，一个空 next 就会被当成伪造的回调拒掉。
    """
    if not state:
        return None
    return _client.getdel(f"rucva:sso:{state}")


# —— 动态签到码 ——
#
# 固定码贴在墙上能从头用到尾，拍张照发到群里，人不到场也能签。改成滚动的：
# 管理端每 30 秒来取一次，取到的码 60 秒后自动从 Redis 消失。
#
# **码不落库**：它是一次性的临时凭证，不是任务的一部分，过了那 60 秒就没有
# 任何意义。任务的固定 token 仍在 CheckinTask 里，作为身份与对账依据。
#
# 两个方向都要存：扫码是「码 → 任务」，续期是「任务 → 当前码」，
# 后者让同一张码在有效期内被反复取到（几十个人扫同一块屏，不能一人一码）。
CHECKIN_CODE_LEN = 8      # 固定码是 6 位，长度不同，两类码在形状上就不会撞
_CODE_ALPHABET = string.ascii_uppercase + string.digits


def current_checkin_code(task_id: str, ttl: int, refresh: int) -> tuple[str, int]:
    """取该任务此刻的签到码，没有就发一张。返回 (码, 距离下次换码的秒数)。

    续期用的键比码本身短命（refresh < ttl）：键一过期，下次调用就发新码，
    而旧码还能再用一会儿——正是要的「换码不打断正在扫的人」。
    """
    key = f"rucva:cktask:{task_id}"
    code = _client.get(key)
    if code:
        return code, max(1, int(_client.ttl(key)))
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CHECKIN_CODE_LEN))
    pipe = _client.pipeline()
    pipe.setex(f"rucva:ckcode:{code}", ttl, task_id)
    pipe.setex(key, refresh, code)
    pipe.execute()
    return code, refresh


def redeem_checkin_code(code: str) -> str | None:
    """签到码 → 任务 id；没这个码（过期、伪造、打错）返回 None。

    **只读不删**：一场活动几十个人扫的是同一张码，核销掉第一个后面的人就
    全扫不进了。这也是它能被截图转发的原因，所以寿命必须短。
    """
    if not code:
        return None
    return _client.get(f"rucva:ckcode:{code}")


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
