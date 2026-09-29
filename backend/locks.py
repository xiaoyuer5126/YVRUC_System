"""并发控制与 Redis 上的瞬时状态。

★ 场次锁（shift_lock）：Postgres 事务级咨询锁 + 进程内限流。
抢报瞬间「数已报名人数 → 判断还有没有位 → 写一条报名」是读-改-写，
多个进程/多个 worker 同时跑就会双双读到「还剩 1 个位」而双双写入，
表现为超卖；同一个人双击也会插出两条。所以容量判断与落库整段串行化
到场次粒度——锁的是场次，不同场次互不阻塞，抢报高峰照样并行。

唯一索引（models.Enrollment.__table_args__）是第二道防线：哪条改报名的
路径漏了加锁，也插不进重复报名。

其余几样（登录失败计数、下载票据、名额闸门、定时任务选主、签到码）是
Redis 上可丢可重建的瞬时状态，见各节。
"""
from __future__ import annotations

import contextlib
import contextvars
import logging
import os
import secrets
import string
import threading
import time

import redis
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session as OrmSession

from . import config

logger = logging.getLogger(__name__)

_client = redis.Redis.from_url(config.REDIS_URL, decode_responses=True)

WAIT_TIMEOUT = 3.0       # 等锁上限，超过就让用户重试而不是干等
# 已经扣到闸门号的报名请求等得更久，见 shift_lock 与 config.TICKET_WAIT_SECONDS
TICKET_WAIT_TIMEOUT = config.TICKET_WAIT_SECONDS

# 只删自己那把：租期到了之后键可能已被别人持有，无条件 del 会误删。
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

def issue_ticket(user_id: str, path: str, ttl: int, identity: str = "") -> str:
    """identity：换票时的身份视角（X-Identity），兑换时照样套上；没有就留空。"""
    token = secrets.token_urlsafe(24)
    _client.setex(f"rucva:dl:{token}", ttl, f"{user_id}|{path}|{identity}")
    return token


def redeem_ticket(token: str, path: str) -> tuple[str, str] | None:
    """核销票据，返回 (用户 id, 身份视角)；票据不存在、已用过或对不上这个
    网址都返回 None。旧票只有两段（没有视角），按空视角处理。"""
    if not token:
        return None
    raw = _client.getdel(f"rucva:dl:{token}")
    if not raw or "|" not in raw:
        return None
    # 网址以 /api/ 开头、不含查询串，里面不会有「|」；视角在最后一段
    user_id, rest = raw.split("|", 1)
    want, _, identity = rest.partition("|")
    return (user_id, identity) if want == path else None


def clear_failures(key: str) -> None:
    with contextlib.suppress(redis.RedisError):
        _client.delete(_fail_key(key))


# —— 按人限频（意见反馈等） ——
#
# 固定窗口：第一次记数时起算，到点整个清零。跟失败计数不同，这里每记一次
# 都不续期——否则一个人隔一会儿交一条，窗口永远过不去。
# Redis 不可用时同样放行。

def rate_count(key: str) -> int:
    try:
        return int(_client.get(f"rucva:rate:{key}") or 0)
    except redis.RedisError:
        return 0


def note_rate(key: str, window_seconds: int) -> None:
    try:
        if _client.incr(f"rucva:rate:{key}") == 1:
            _client.expire(f"rucva:rate:{key}", window_seconds)
    except redis.RedisError:
        pass


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


# —— 名额闸门 ——
#
# 抢报时几百个请求同一瞬间到。每个请求都进数据库「数一遍还剩几个位子」的话，
# 数据库连接和线程池会被一起拖垮。所以在 Redis 上放一个「还剩几个」的计数器，
# 报名先来扣一个：扣不到就直接回「满了」，这一步完全不碰数据库。
#
# **闸门只挡洪水，不判定名额**：拿到闸门的请求照样走原来的锁 + 实际人数判定，
# 数据库始终是账本。所以计数器旧了也不会超卖。
#
# 计数器要跟数据库对齐（预热、以及闸门说「满了」时核一遍），但**不能直接覆盖成
# 数据库的剩余数**：已经扣到号、还在做资格校验没落库的那些请求，数据库里看不见。
# 直接覆盖的话，在途的号全被「还」了回去——抢报高峰里计数器一次次被改回
# 「还剩 N 个」，闸门等于没有。所以每个扣到的号都登记在一个在途集合里
# （带时间戳，超过 GATE_INFLIGHT_SECONDS 的视为进程崩掉没还，不再算数），
# 对齐时写的是「数据库剩余 − 在途」。
#
# 闸门说「满了」时核库也要限流：每个岗位每 GATE_RECHECK_MS 最多核一次，其余的
# 直接回「满了」。不限的话，名额发完之后洪水里的每个请求仍各查一次库。代价是
# 退选放出来的名额最多晚这么一小会儿才能被闸门看见——退选本身会把闸门作废
# （invalidate_gates），所以通常连这一小会儿也没有。
GATE_TTL_SECONDS = 300
GATE_INFLIGHT_SECONDS = 60       # 比一次报名请求的最长耗时宽得多
GATE_RECHECK_MS = 500
GATE_NONE, GATE_FULL, GATE_OK = -1, 0, 1

# 扣一个，但**不扣到负数**：负数会让后来的还号把计数顶回去，一个旧计数器
# 就能凭空变出名额来。所以在 Lua 里判完再扣；扣到了就把号登记为在途。
_TAKE_GATE = _client.register_script("""
local n = redis.call('get', KEYS[1])
if not n then return -1 end
if tonumber(n) <= 0 then return 0 end
redis.call('decr', KEYS[1])
redis.call('zadd', KEYS[2], ARGV[2], ARGV[1])
redis.call('expire', KEYS[2], ARGV[3])
return 1
""")

# 按数据库剩余对齐，再扣一个。「剩余 − 在途」和扣号在一段 Lua 里做完，
# 中间不会有别的请求插进来扣号或还号。
_SYNC_GATE = _client.register_script("""
redis.call('zremrangebyscore', KEYS[2], '-inf', ARGV[4])
local left = tonumber(ARGV[5]) - redis.call('zcard', KEYS[2])
if left < 0 then left = 0 end
redis.call('set', KEYS[1], left, 'EX', ARGV[3])
if left <= 0 then return 0 end
redis.call('decr', KEYS[1])
redis.call('zadd', KEYS[2], ARGV[2], ARGV[1])
redis.call('expire', KEYS[2], ARGV[3])
return 1
""")

# 结算一个号：从在途里划掉；restore=1 时把名额还给计数器。
# 只有真从在途里划掉了才还——同一个号结算两次不会多还一个出来。
_SETTLE_GATE = _client.register_script("""
if redis.call('zrem', KEYS[2], ARGV[1]) == 1 and ARGV[2] == '1' then
    if redis.call('exists', KEYS[1]) == 1 then
        redis.call('incr', KEYS[1])
    end
    return 1
end
return 0
""")


def gate_key(shift_id: str, position_id: str, kind: str) -> str:
    """一个岗位一种闸门一条键。kind 目前是 seat（正式名额）与 waitlist（候补）。"""
    return f"rucva:gate:{kind}:{shift_id}:{position_id}"


def _gate_keys(shift_id: str, position_id: str, kind: str) -> list[str]:
    key = gate_key(shift_id, position_id, kind)
    return [key, key + ":inflight"]


def new_gate_ticket() -> str:
    return secrets.token_hex(8)


def take_gate(shift_id: str, position_id: str, kind: str, ticket: str) -> int:
    """扣一个名额。返回 GATE_OK（扣到了）/ GATE_FULL（说满了）/ GATE_NONE（没预热）。

    Redis 不可用时返回 GATE_NONE：闸门是加速用的，不该成为新的单点故障——
    退回到「每个请求都查库」那条老路，功能照旧，只是慢。
    """
    try:
        return int(_TAKE_GATE(keys=_gate_keys(shift_id, position_id, kind),
                              args=[ticket, time.time(), GATE_TTL_SECONDS]))
    except redis.RedisError:
        return GATE_NONE


def sync_and_take_gate(shift_id: str, position_id: str, kind: str,
                       ticket: str, db_left: int) -> int:
    """按数据库剩余（减去在途）对齐计数器，再扣一个。Redis 不可用时 GATE_NONE。"""
    t = time.time()
    try:
        return int(_SYNC_GATE(keys=_gate_keys(shift_id, position_id, kind),
                              args=[ticket, t, GATE_TTL_SECONDS,
                                    t - GATE_INFLIGHT_SECONDS, db_left]))
    except redis.RedisError:
        return GATE_NONE


def may_recheck_gate(shift_id: str, position_id: str, kind: str) -> bool:
    """闸门说满了，这一次要不要去数据库核一遍（每个岗位限流，见上）。"""
    try:
        return bool(_client.set(gate_key(shift_id, position_id, kind) + ":recheck",
                                1, nx=True, px=GATE_RECHECK_MS))
    except redis.RedisError:
        return True


def settle_gate(shift_id: str, position_id: str, kind: str, ticket: str,
                *, restore: bool) -> None:
    """结算扣到的号。restore=True：没用上，名额还回去；False：用掉了（或者
    数据库说其实没位子，还回去只会让计数器更虚高），只从在途里划掉。"""
    with contextlib.suppress(redis.RedisError):
        _SETTLE_GATE(keys=_gate_keys(shift_id, position_id, kind),
                     args=[ticket, "1" if restore else "0"])


def invalidate_gates(shift_id: str, position_id: str) -> None:
    """名额在闸门之外变多了（退选）：作废计数器，下一个请求会按数据库重新对齐。
    在途集合留着——对齐时还得靠它减掉在途的号。"""
    with contextlib.suppress(redis.RedisError):
        _client.delete(gate_key(shift_id, position_id, "seat"),
                       gate_key(shift_id, position_id, "waitlist"))


def gate_left(shift_id: str, position_id: str, kind: str) -> int | None:
    """闸门现在还剩几个。None = 没预热。给用例和排查用。"""
    try:
        raw = _client.get(gate_key(shift_id, position_id, kind))
    except redis.RedisError:
        return None
    return int(raw) if raw is not None else None


def reset_gate(shift_id: str, position_id: str, kind: str, left: int) -> None:
    """直接把计数器设成某个数。只给用例和排查用——业务路径走 sync_and_take_gate。"""
    with contextlib.suppress(redis.RedisError):
        _client.set(gate_key(shift_id, position_id, kind), max(0, left),
                    ex=GATE_TTL_SECONDS)


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


# —— 场次锁 ——
#
# 从前是 Redis 上 SET NX 加租期、20ms 轮询一次的锁，压测（300 人抢 200+50 个位）
# 里毛病全出来了：几百个线程轮询把 CPU 烧在空转上，持锁的人反而跑不动；持锁
# 一慢过 5 秒租期，锁自动过期、放第二个人进来，就是超卖。
#
# 现在用 Postgres 的事务级咨询锁（pg_advisory_xact_lock），在**请求自己的会话**
# 上拿：
#   · 和业务写入在同一个事务里，提交或回滚时由数据库放锁——没有租期，不会
#     「还在干活锁却过期了」；请求中途抛错、会话被关，锁也跟着放掉；
#   · 等锁的人在数据库里按先来后到排队，不轮询、不占 CPU。
# 代价是**锁只活到第一次 commit**：锁里不能 commit 一半再接着改名额，调用方
# 要把「判定 + 落库 + 递补」放进同一个事务（各调用点有注释）。
#
# 在数据库里等锁要占着一条连接。几百个请求都进去等的话，连接池被等锁的人占满，
# 刷页面的请求拿不到连接。所以前面再加一道**进程内**的名额（LOCK_SLOTS）：
# 同一进程里最多这么多个请求带着连接去数据库排队，其余的在进程里等（不占连接、
# 不占 CPU），轮到了再去。
_SLOTS = threading.BoundedSemaphore(config.LOCK_SLOTS)
# 双参数形式的第一个参数当命名空间：场次锁 1、用户锁 2（见 user_lock），
# 同一个 id 串哈希到同一个数也不会互相挡
_LOCK_NS = 1
_USER_NS = 2
_USER_LOCKED = "rucva_user_locked"       # 会话上记着：本事务已经拿了用户锁
_SLOT_DEPTH = "rucva_shift_lock_depth"     # 会话上记着：这一个请求已经占了名额

# —— 锁内不做外呼 ——
#
# 场次锁在事务提交时就放了，外呼（消息中心推送）本来就挂在 after_commit 上，
# 不会拖住数据库锁；但它仍在 with 块里、占着进程内的名额和这条线程——一次推送
# 最长等 8 秒，名额被推送占着，排在后面的人全跟着等。所以锁里产生的外呼
# 一律排到 with 块结束（名额也还了）之后再做：
# after_unlock(fn) 在锁里调用就排队，最外层的锁结束时依次执行；不在锁里就立刻执行。
_after_unlock: contextvars.ContextVar[list | None] = contextvars.ContextVar(
    "rucva_after_unlock", default=None)


def after_unlock(fn) -> None:
    queued = _after_unlock.get()
    if queued is None:
        fn()
    else:
        queued.append(fn)


def _run_after_unlock(queued: list) -> None:
    for fn in queued:
        try:
            fn()
        except Exception:  # noqa: BLE001  收尾动作失败不能把已经落库的业务变成 500
            logger.exception("锁外收尾动作失败")


def _busy() -> HTTPException:
    return HTTPException(503, "当前操作人数较多，请稍后重试")


def _pg_lock(db: OrmSession, shift_ids: list[str], deadline: float,
             user_id: str | None = None) -> None:
    """在 db 当前的事务里依次拿场次锁、再拿用户锁（给了 user_id 的话），
    合计最多等到 deadline（time.monotonic() 的刻度）。

    lock_timeout 只在拿锁那几句上生效，拿完马上改回默认：SET LOCAL 管的是
    整个事务，不改回去的话，锁里后面的 SQL 等行锁时也会被这点剩余时间掐掉。
    每把场次锁前按剩余时间重设一次；用户锁紧跟在场次锁后面，沿用最后那个
    值，不再多一个往返（锁里每个往返都算在别人的排队时间上）。
    """
    def limit() -> None:
        left = deadline - time.monotonic()
        if left <= 0:
            raise _busy()
        db.execute(text("SELECT set_config('lock_timeout', :t, true)"),
                   {"t": f"{max(1, int(left * 1000))}ms"})

    lock = text("SELECT pg_advisory_xact_lock(:ns, hashtext(:k))")
    try:
        for sid in shift_ids:
            limit()
            db.execute(lock, {"ns": _LOCK_NS, "k": sid})
        if user_id is not None:
            if not shift_ids:
                limit()
            db.execute(lock, {"ns": _USER_NS, "k": user_id})
    except OperationalError as exc:
        # 55P03 lock_not_available：等满了。事务已经废了，调用方抛出去之后
        # 会话回滚（请求里由 db_session 关掉），不会留半截
        if getattr(exc.orig, "sqlstate", None) == "55P03":
            raise _busy() from exc
        raise
    db.execute(text("SET LOCAL lock_timeout TO DEFAULT"))
    if user_id is not None:
        db.info[_USER_LOCKED] = db.get_transaction()


@contextlib.contextmanager
def shift_lock(db: OrmSession, *shift_ids: str, wait: float | None = None,
               user_id: str | None = None):
    """锁住场次的名额账本，直到 db 的**这个事务**结束（commit / rollback）。

    with 块只管「拿锁」和「还进程名额」；锁本身随事务走，所以块里 commit 过
    之后就不再受保护——要么一个事务做完，要么重新进一次 shift_lock。

    shift_ids 可以给多个：按 id 排序依次加锁，两个请求锁同一批场次时顺序一致，
    不会互相等成死锁。

    user_id：场次锁拿到之后，接着拿这个人的用户锁（见下面「用户锁」一节）。

    wait：最多等多久（进程名额 + 数据库锁合计），默认 WAIT_TIMEOUT，等不到回 503。
    报名时**已经扣到闸门号**的请求传 TICKET_WAIT_TIMEOUT：它是前 N 个到的人之一，
    名额本就该是它的，3 秒没排到就回 503 等于把名额让给了后来重试的人——抢报时
    几十上百个持号的人排一把锁，排到后面的正好要等好几秒。闸门把这种人限在
    「名额数」上下，所以多等的队伍不会无限长；没拿到号的照旧 3 秒，洪水不会
    因此堆起来。等待期间号一直登记在途，GATE_INFLIGHT_SECONDS 比这宽得多，
    不会被当成崩掉的请求清走。
    """
    # 用户锁之后再拿场次锁就可能和别人互相等成死锁，见 user_lock
    held = db.info.get(_USER_LOCKED)
    if held is not None and held is db.get_transaction():
        raise RuntimeError("本事务已持有用户锁，不能再拿场次锁：顺序必须是先场次后用户")
    # 在调用时读模块变量而不是写成默认参数：用例会 monkeypatch 它
    budget = WAIT_TIMEOUT if wait is None else wait
    deadline = time.monotonic() + budget
    depth = db.info.get(_SLOT_DEPTH, 0)
    # 同一个会话嵌套进锁不再占第二个名额：名额只有几个，自己等自己会卡死
    if depth == 0 and not _SLOTS.acquire(timeout=budget):
        raise _busy()
    db.info[_SLOT_DEPTH] = depth + 1
    outer = _after_unlock.get() is None
    reset = _after_unlock.set([]) if outer else None
    try:
        _pg_lock(db, sorted(set(shift_ids)), deadline, user_id)
        yield
    finally:
        if depth == 0:
            db.info.pop(_SLOT_DEPTH, None)
            _SLOTS.release()
        else:
            db.info[_SLOT_DEPTH] = depth
        if outer:
            queued = _after_unlock.get()
            _after_unlock.reset(reset)
            _run_after_unlock(queued)


# —— 用户锁 ——
#
# 场次锁只管「一个场次的名额」。时段冲突是**跨场次**的：同一个人几乎同时报
# 两场时间重叠、却不在同一场次（甚至不在同一活动）的班，两个请求拿的是两把
# 不同的场次锁，互不阻塞；各自在锁外查冲突时都还看不见对方，于是两条都落库，
# 一个人同一时间被排在两个地方。
#
# 所以报名在拿到场次锁**之后**再按人拿一把事务级咨询锁（命名空间 2），在锁里
# 重查一遍时段冲突。同一个人的请求在这里排队，后到的那个一定看得见先到的
# 那条已提交的报名（读已提交：锁拿到之后发的每条 SQL 都是新快照）。
#
# ★ 顺序只能是「先场次锁、后用户锁」，拿了用户锁之后不再去等任何场次锁：
#   同一个人报 A、B 两场，请求 1 持 A 等用户锁、请求 2 持 B 等用户锁——
#   谁先拿到用户锁谁就一路做到提交，不会再回头等对方手里的场次锁，
#   另一个随后拿到，环凑不起来。反过来（先用户后场次）就会出现：请求 1 持
#   用户锁等 A，而 A 被请求 3 持着、请求 3 又在等同一个人的用户锁……
#   shift_lock 会检查这条，违反了直接抛错，而不是等数据库报死锁。
#
# 不另占进程名额：它总是在场次锁里面拿，名额已经占着了；等它的只有同一个人
# 的其他请求，队伍长度以「一个人同时点了几下」为限。


def user_lock(db: OrmSession, user_id: str, wait: float | None = None) -> None:
    """在 db 当前事务里锁住这个人的时段账本，直到事务结束。

    必须在 shift_lock 块**里面**调（或者这个事务根本不拿场次锁），之后不能再
    进新的 shift_lock。等不到回 503（默认 WAIT_TIMEOUT）。
    报名这种一进锁就要拿的，用 shift_lock(..., user_id=) 省一个往返。
    """
    budget = WAIT_TIMEOUT if wait is None else wait
    _pg_lock(db, [], time.monotonic() + budget, user_id)


# —— 天地图瓦片的每日额度 ——
#
# 只数**真正打到天地图**的请求（缓存命中不算），键上带日期、两天后自己消失。
# 按天地图的口径是自然日，所以日期用本地时间（容器 TZ=Asia/Shanghai）。
# 多个进程共用这一个数，所以放 Redis 而不是进程内存。

TILE_QUOTA_TTL = 2 * 24 * 3600


def tile_fetches(day: str) -> int:
    """这一天已经向天地图取了多少张瓦片。"""
    return int(_client.get(f"rucva:tiles:{day}") or 0)


def count_tile_fetch(day: str) -> int:
    """记一次向天地图取瓦片，返回记完之后的数。"""
    key = f"rucva:tiles:{day}"
    pipe = _client.pipeline()
    pipe.incr(key)
    pipe.expire(key, TILE_QUOTA_TTL)
    return int(pipe.execute()[0])
