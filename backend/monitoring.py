"""错误监控：把后端的未捕获异常报到自建的 Sentry 类服务（GlitchTip / Sentry）。

没配 `YVRUC_SENTRY_DSN` 就整块不启用——`init()` 直接返回 False，SDK 不初始化、
上报线程不起、一个字节都不出站。本机调试、CI 和测试里它天然是关着的。

上报什么：异常类型与消息、堆栈、出错的接口、环境名、主机名，以及 ERROR 及以上
的日志（SDK 的日志集成把 `logger.error` / `logger.exception` 也变成事件，所以日志
写在哪里，监控上就看得到哪里）。**503 不报**——它在本平台是有意的拒绝，不是故障，
理由见 `options()`。

**上报前一律脱敏**（`scrub_event`）。库里有身份证号和手机号，请求正文里是报名问卷
的答案，请求头里是会话令牌，查询串里是下载票据——这些东西出了这台服务器就收不
回来。所以脱敏不靠「上报时注意别带」，而是每个事件都从 `before_send` 过一遍：

  · 请求正文、Cookie、查询串整块不报，请求头只留 `KEEP_HEADERS` 那几个；
  · 堆栈帧里的局部变量不报（`include_local_variables=False`）——报名接口的局部
    变量里就坐着整份问卷答案和会话对象；
  · 用户身份不报：要查是谁触发的，拿时间和路径去操作日志里对（oplog.py 记了学号、
    姓名和当时的身份视角，那份日志不出服务器）；
  · 剩下的文字（异常消息、日志、面包屑、网址）按 `_PATTERNS` 盖掉身份证号、手机号、
    Bearer 令牌和 `token=` / `ticket=` 这类参数值；键名撞上 `SENSITIVE_KEYS` 的，
    不管藏在事件哪一层，值一律换成 `MASK`。

脱敏自己出错时整条事件丢掉，不上报半成品：少看到一个错误，比漏出一个身份证号
轻得多。

同一个错误短时间内反复出现，还会给管理员推一条站内通知（`alert_frequent`，由定时
任务调用）：上报的那一刻只在 Redis 上记一次数，不碰数据库——错误风暴十有八九就是
数据库出了问题，那时候再去查一遍管理员名单只会雪上加霜。
"""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from . import config

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as OrmSession

logger = logging.getLogger(__name__)

MASK = "[已脱敏]"

# 事件里最多往下走多少层，更深的整块换成 MASK。嵌套结构是 SDK 和各集成拼出来的，
# 深度不由我们决定，给一个够用的上限免得递归失控
MAX_DEPTH = 24

# 请求头只留这几个：排查时要知道是哪种客户端、什么格式、从哪一页点过来的。其余
# 一概不报——Authorization 里是会话令牌，Cookie 同理，自定义头里也可能夹带东西
KEEP_HEADERS = frozenset({"user-agent", "content-type", "accept-language",
                          "referer"})

# 键名撞上这些，值一律换成 MASK，不管它在事件的哪一层
SENSITIVE_KEYS = frozenset({
    "cookie", "cookies", "set-cookie", "authorization", "proxy-authorization",
    "token", "access_token", "refresh_token", "session_token", "ticket",
    "secret", "client_secret", "password", "passwd", "api_key", "apikey",
    "dsn", "id_card", "idcard", "id_number", "phone", "mobile", "tel",
    # 堆栈帧里的局部变量。init() 那边已经关掉了，这里再挡一道：哪天 SDK 的默认值
    # 变了，报名接口局部变量里的整份问卷答案会跟着出站
    "vars",
})

_PATTERNS = (
    # 身份证号：18 位（末位可能是 X）或 15 位的老号
    (re.compile(r"(?<![0-9A-Za-z])(?:\d{17}[\dXx]|\d{15})(?![0-9A-Za-z])"),
     "[身份证号]"),
    # 手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    # Authorization: Bearer <令牌>
    (re.compile(r"(?i)\bbearer\s+[\w\-.=+/]+"), f"Bearer {MASK}"),
    # 查询串、表单里的令牌与口令：访问日志、面包屑里的网址都带着它们
    (re.compile(r"(?i)\b(token|ticket|tk|code|state|secret|key|password|passwd"
                r"|sign|signature)=[^&\s\"']+"),
     rf"\1={MASK}"),
)

_enabled = False


def enabled() -> bool:
    """这个进程的上报开着没有。"""
    return _enabled


def init() -> bool:
    """初始化上报，返回启用了没有。

    必须在注册路由之前调用：SDK 的 FastAPI 集成是在建路由的那一刻把处理函数包起来
    的，晚一步就只剩一部分接口有上报，而症状是「监控上少了一半错误」，没人看得出来。

    DSN 填得不成样子时拒绝启动，不退化成「不上报」：那样一个月后才发现监控从来
    没工作过。
    """
    global _enabled
    if _enabled:
        return True
    if not config.SENTRY_DSN:
        return False
    dsn = urlparse(config.SENTRY_DSN)
    if dsn.scheme not in ("http", "https") or not dsn.hostname or not dsn.username:
        raise RuntimeError(
            "YVRUC_SENTRY_DSN 不是一个 DSN。它形如 "
            "https://<公钥>@监控服务地址/<项目号>，在监控服务的项目设置里复制")
    try:
        import sentry_sdk
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "配了 YVRUC_SENTRY_DSN，但这个环境里没装 sentry-sdk："
            "它在 backend/requirements.txt 里，重新构建后端镜像即可") from exc

    sentry_sdk.init(**options())
    _enabled = True
    return True


def options() -> dict:
    """传给 SDK 的全部设置。

    单独一个函数，测试才能拿着同一份设置真的初始化一次 SDK：上报的行为取决于
    这几项，而它们是 SDK 的关键字参数——改了名字、打错一个字，后端在启动时才炸，
    或者更糟，静默地把局部变量一起报出去。
    """
    from sentry_sdk.integrations.fastapi import FastApiIntegration
    from sentry_sdk.integrations.starlette import StarletteIntegration

    # 哪些状态码的 HTTPException 算错误。默认是 500～599 全算，而本平台的 503 是
    # **有意的拒绝**：抢报时闸门把超出名额的请求挡回去（locks._busy 的「当前操作
    # 人数较多」）、瓦片额度到顶、签到码连着撞码。一场抢报就能凑出几十条同类事件，
    # 10 分钟 20 次正好触发「后端报错频繁」——管理员每次抢报都收到一条假警报，
    # 下次就不看通知了。所以 503 跳过，其余 5xx 照报。
    failed = {*range(500, 503), *range(504, 600)}
    return {
        "integrations": [StarletteIntegration(failed_request_status_codes=failed),
                         FastApiIntegration(failed_request_status_codes=failed)],
        "dsn": config.SENTRY_DSN,
        "environment": config.SENTRY_ENVIRONMENT,
        "sample_rate": config.SENTRY_SAMPLE_RATE,
        "traces_sample_rate": config.SENTRY_TRACES_SAMPLE_RATE,
        "before_send": scrub_event,
        "before_send_transaction": scrub_event,
        # 下面三项和 scrub_event 一起构成脱敏，改它们等于把个人信息放出去
        "send_default_pii": False,         # 不自动带 IP、Cookie 与请求头里的身份信息
        "include_local_variables": False,  # 堆栈帧里的局部变量不上报
        "max_request_body_size": "never",  # 请求正文不上报
        # 面包屑是出错前的几步（SQL、HTTP、日志），留最近 20 条足够定位；留多了
        # 只是把更多文字送出去
        "max_breadcrumbs": 20,
    }


def scrub_event(event: dict, hint: dict | None = None) -> dict | None:
    """`before_send`：返回脱敏后的事件；返回 None 则这一条不上报。"""
    try:
        scrubbed = _scrub(event)
    except Exception:  # noqa: BLE001
        logger.exception("事件脱敏失败，这条错误不上报")
        return None
    try:
        _note_frequency(scrubbed)
    except Exception:  # noqa: BLE001
        # 记频次失败不该连上报一起搭上：没有通知总比没有错误记录好
        logger.exception("记录错误频次失败")
    return scrubbed


def _scrub(event: dict) -> dict:
    request = event.get("request")
    if isinstance(request, dict):
        # 整块不要：正文里是问卷答案与个人资料，Cookie 和查询串里是会话令牌、
        # 下载票据、瓦片令牌
        for key in ("data", "cookies", "query_string"):
            request.pop(key, None)
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {k: v for k, v in headers.items()
                                  if isinstance(k, str)
                                  and k.lower() in KEEP_HEADERS}
        url = request.get("url")
        if isinstance(url, str):
            request["url"] = url.split("?", 1)[0]
    event.pop("user", None)
    return _walk(event)


def _walk(value: Any, depth: int = 0) -> Any:
    if depth > MAX_DEPTH:
        return MASK
    if isinstance(value, str):
        return mask_text(value)
    if isinstance(value, dict):
        return {k: (MASK if isinstance(k, str) and k.lower() in SENSITIVE_KEYS
                    else _walk(v, depth + 1))
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_walk(v, depth + 1) for v in value]
    return value


def mask_text(text: str) -> str:
    """盖掉一段文字里的身份证号、手机号与令牌。"""
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


# ======================= 频次告警 =======================
#
# 「同一个错误」按异常类型加出错的接口归并，和监控服务自己的归并口径大致一样，
# 够用来判断是不是在刷屏。阈值与窗口见 config.SENTRY_ALERT_*。

def error_key(event: dict) -> str:
    """这条事件算哪一个错误。"""
    kind = ""
    values = (event.get("exception") or {}).get("values") or []
    if values and isinstance(values[-1], dict):
        kind = str(values[-1].get("type") or "")
    if not kind:
        kind = str(event.get("logger") or event.get("level") or "error")
    where = str(event.get("transaction") or "")
    # Redis 的 hash 字段，截短免得一条超长的异常类型名把整个桶撑大
    return f"{kind}|{where}"[:120]


def _note_frequency(event: dict) -> None:
    if not _enabled or not config.SENTRY_ALERT_THRESHOLD:
        return
    # 性能追踪的事件（每个请求一条）从同一个钩子过，但它不是错误，不参与计数
    if event.get("type") == "transaction":
        return
    from . import locks

    locks.note_error(error_key(event), config.SENTRY_ALERT_WINDOW_MINUTES * 60)


def alert_frequent(db: OrmSession) -> int:
    """同一个错误在一个窗口里超过阈值，给管理员推一条站内通知，返回推了几种错误。

    窗口是固定的时间桶，只看当前这一桶（见 locks 里「错误频次」那一节）。

    同一个错误一天只推一次：这条通知要是跟着错误一起刷屏，管理员会先把通知关掉，
    再也不看——那就连第一条也白发了。
    """
    if not config.SENTRY_ALERT_THRESHOLD:
        return 0
    from . import locks
    from .models import User
    from .notify import notify

    window = config.SENTRY_ALERT_WINDOW_MINUTES * 60
    hot = sorted((n, key) for key, n in locks.error_counts(window).items()
                 if n >= config.SENTRY_ALERT_THRESHOLD)
    sent = 0
    for count, key in hot:
        if not locks.claim_once(f"errmon:{key}:{date.today().isoformat()}",
                                2 * 86400):
            continue
        kind, _, where = key.partition("|")
        admins = (db.query(User)
                  .filter(User.roles.contains(["admin"])).all())
        content = (f"最近 {config.SENTRY_ALERT_WINDOW_MINUTES} 分钟内"
                   f"「{kind}」出现 {count} 次"
                   + (f"，出错的接口是 {where}。" if where else "。")
                   + "堆栈在错误监控里看（地址见部署文档）。"
                     "同一个错误今天不再提醒。")
        for u in admins:
            if u.has_role("admin", real=True):
                notify(db, u, "后端报错频繁", content)
        db.commit()
        logger.warning("错误频次告警：%s 在 %d 分钟内 %d 次", key,
                       config.SENTRY_ALERT_WINDOW_MINUTES, count)
        sent += 1
    return sent
