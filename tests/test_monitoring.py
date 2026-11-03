"""错误监控：上报前的脱敏、没配就不启用、刷屏时通知管理员。

脱敏这几条是硬要求：事件发出去就收不回来了，所以用例直接盯「序列化之后的事件
里还有没有这些字符串」，而不是盯某个字段，免得哪天 SDK 换了字段名就漏过去。
"""
from __future__ import annotations

import json
import uuid

import pytest

from conftest import ADMIN, inbox

from backend import config, locks, monitoring

身份证 = "110101199003071234"
手机号 = "13800138000"


def 一条事件() -> dict:
    """照 SDK 真实发出的样子造一条：请求、堆栈、面包屑、附加数据都带着。"""
    return {
        "level": "error",
        "transaction": "POST /api/activities/{activity_id}/enrollments",
        "message": f"报名失败：身份证 {身份证}，手机 {手机号}",
        "request": {
            "url": "https://v.ruc.edu.cn/api/exports/hours?ticket=abc123xyz",
            "method": "POST",
            "query_string": "ticket=abc123xyz",
            "cookies": {"session": "cookie口令"},
            "data": {"q1": f"我的身份证是 {身份证}", "q2": 手机号},
            "headers": {
                "Authorization": "Bearer tok-abcdef123",
                "Cookie": "session=cookie口令",
                "User-Agent": "Mozilla/5.0",
                "X-Real-Ip": "10.1.2.3",
            },
        },
        "user": {"id": "u-1", "username": "王砚秋", "student_id": ADMIN},
        "exception": {"values": [{
            "type": "ValueError",
            "value": f"手机号 {手机号} 不合法",
            "stacktrace": {"frames": [{
                "function": "create_enrollment",
                "vars": {"id_card": 身份证, "answers": {"q1": 手机号}},
            }]},
        }]},
        "breadcrumbs": {"values": [
            {"message": "GET /api/geo/tiles/8/1/2?tk=服务端令牌 200"},
            {"message": f"UPDATE users SET phone='{手机号}'"},
        ]},
        "extra": {"身份证号": 身份证, "download_url": "/api/exports/1?ticket=abc123xyz"},
    }


def 脱敏后(event: dict) -> tuple[dict, str]:
    out = monitoring.scrub_event(event)
    return out, json.dumps(out, ensure_ascii=False)


def test_上报里不带身份证号手机号与请求正文():
    out, text = 脱敏后(一条事件())
    for 不该出现 in (身份证, 手机号, "cookie口令", "tok-abcdef123", "abc123xyz",
                 "我的身份证是", "服务端令牌"):
        assert 不该出现 not in text, f"{不该出现} 被报了出去"
    # 正文、Cookie、查询串整块不在了
    assert "data" not in out["request"]
    assert "cookies" not in out["request"]
    assert "query_string" not in out["request"]
    assert out["request"]["url"] == "https://v.ruc.edu.cn/api/exports/hours"


def test_请求头只留无关的那几个():
    out, _ = 脱敏后(一条事件())
    assert out["request"]["headers"] == {"User-Agent": "Mozilla/5.0"}


def test_堆栈局部变量与用户身份不上报():
    out, _ = 脱敏后(一条事件())
    frame = out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert frame["vars"] == monitoring.MASK
    assert frame["function"] == "create_enrollment"   # 定位用的东西要留着
    assert "user" not in out


def test_异常类型与出错的接口照旧留着():
    """脱敏不能把能定位问题的东西一起盖掉，否则监控就只剩一堆打码的噪声。"""
    out, _ = 脱敏后(一条事件())
    assert out["exception"]["values"][0]["type"] == "ValueError"
    assert out["transaction"] == "POST /api/activities/{activity_id}/enrollments"
    assert out["level"] == "error"


def test_脱敏出错时整条不上报():
    """少看到一个错误，比漏出一个身份证号轻得多。"""
    class 会炸的字典(dict):
        def items(self):
            raise RuntimeError("故意炸")

    assert monitoring.scrub_event({"extra": 会炸的字典()}) is None


def test_没配DSN就不启用():
    """本机、CI 和测试里都没有 DSN：SDK 不初始化，一个字节都不出站。"""
    assert config.SENTRY_DSN == ""
    assert monitoring.init() is False
    assert monitoring.enabled() is False


def test_DSN填得不成样子就拒绝启动(monkeypatch):
    """不能退化成「悄悄不上报」：那样一个月后才发现监控从来没工作过。"""
    for 坏值 in ("glitchtip.local/1", "https://glitchtip.local/1", "随便填的"):
        monkeypatch.setattr(config, "SENTRY_DSN", 坏值)
        with pytest.raises(RuntimeError, match="DSN"):
            monitoring.init()
    assert monitoring.enabled() is False


def test_采样率填成百分比就拒绝启动(monkeypatch):
    monkeypatch.setenv("YVRUC_SENTRY_SAMPLE_RATE_FOR_TEST", "100")
    with pytest.raises(RuntimeError, match="0～1"):
        config._rate("YVRUC_SENTRY_SAMPLE_RATE_FOR_TEST", "1")
    monkeypatch.setenv("YVRUC_SENTRY_SAMPLE_RATE_FOR_TEST", "0.25")
    assert config._rate("YVRUC_SENTRY_SAMPLE_RATE_FOR_TEST", "1") == 0.25


def test_没启用时不记频次():
    """关着的时候连计数都不该做，否则 Redis 上多一笔没人用的账。"""
    kind = f"Nothing{uuid.uuid4().hex[:8]}"
    monitoring.scrub_event({"exception": {"values": [{"type": kind}]},
                            "transaction": "/api/x"})
    window = config.SENTRY_ALERT_WINDOW_MINUTES * 60
    assert not [k for k in locks.error_counts(window) if k.startswith(kind)]


def test_同一个错误刷屏时通知管理员(db):
    """阈值是按窗口算的，到了就推一条，当天同一个错误不再推。"""
    kind = f"Boom{uuid.uuid4().hex[:8]}"
    key = f"{kind}|POST /api/enrollments"
    window = config.SENTRY_ALERT_WINDOW_MINUTES * 60
    for _ in range(config.SENTRY_ALERT_THRESHOLD):
        locks.note_error(key, window)
    assert locks.error_counts(window)[key] == config.SENTRY_ALERT_THRESHOLD

    assert monitoring.alert_frequent(db) >= 1
    发出的 = [n for n in inbox(db, ADMIN, "后端报错频繁") if kind in n.content]
    assert len(发出的) == 1
    assert str(config.SENTRY_ALERT_THRESHOLD) in 发出的[0].content
    assert "POST /api/enrollments" in 发出的[0].content

    # 再跑一轮不该重复推：通知跟着错误一起刷屏，管理员会把通知关掉
    monitoring.alert_frequent(db)
    assert len([n for n in inbox(db, ADMIN, "后端报错频繁")
                if kind in n.content]) == 1


def test_阈值以下不打扰(db):
    kind = f"Quiet{uuid.uuid4().hex[:8]}"
    window = config.SENTRY_ALERT_WINDOW_MINUTES * 60
    for _ in range(config.SENTRY_ALERT_THRESHOLD - 1):
        locks.note_error(f"{kind}|/api/y", window)
    monitoring.alert_frequent(db)
    assert not [n for n in inbox(db, ADMIN, "后端报错频繁") if kind in n.content]
