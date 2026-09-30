"""定时任务选主：多个进程同时跑时，同一轮只许一个进程干活。

后端起多个进程，每个进程都有一份后台清扫。漏掉选主，签到提醒就按进程数
发几遍——而且是发给真人的。
"""
from __future__ import annotations

import logging
import time

import pytest
import redis

from backend import locks


@pytest.fixture(autouse=True)
def clean_lease():
    """每个用例都从「没人持锁」开始，也不把锁留给下一个用例。"""
    locks._client.delete(locks.SWEEPER_KEY)
    yield
    locks._client.delete(locks.SWEEPER_KEY)


def test_同一时刻只有一个进程当主(caplog):
    caplog.set_level(logging.INFO, logger="backend.locks")
    a, b = locks.sweeper_token(), locks.sweeper_token()
    assert a != b
    assert locks.hold_sweeper_lease(a) is True
    assert locks.hold_sweeper_lease(b) is False
    # 主进程每轮开头再来一次就是续期，仍然归它
    assert locks.hold_sweeper_lease(a) is True
    assert locks.hold_sweeper_lease(b) is False
    # 续期不打「选主」：每轮都打的话，真换主那次就看不出来了
    taken = [r for r in caplog.records if "选主" in r.getMessage()]
    assert len(taken) == 1


def test_主进程一停别人一个租期内接手():
    """解锁靠租期到期，不靠进程自己放手——被 kill 的进程没机会放手，
    所以这里 a 什么都不做，只是不再续期。"""
    a, b = locks.sweeper_token(), locks.sweeper_token()
    assert locks.hold_sweeper_lease(a, ttl=1) is True
    assert locks.hold_sweeper_lease(b) is False
    time.sleep(1.2)                       # 租期过了，锁自己就没了
    assert locks.hold_sweeper_lease(b) is True
    assert locks.hold_sweeper_lease(a) is False


def test_主进程正常退出后下一个立刻接手():
    """重启是常态（发版、改配置）。不主动交还的话，新进程得等满租期才接手，
    发版一次就白停五分钟，还看不出是为什么。"""
    a, b = locks.sweeper_token(), locks.sweeper_token()
    assert locks.hold_sweeper_lease(a) is True
    locks.release_sweeper_lease(a)
    assert locks.hold_sweeper_lease(b) is True
    # 交还只交自己那把：已经不是主的进程再交一次，不该把别人的主位删掉。
    # 用第三个进程来看：b 自己再抢一次也会成功，看不出主位被删过
    locks.release_sweeper_lease(a)
    assert locks.hold_sweeper_lease(locks.sweeper_token()) is False


def test_redis挂了照跑不误(monkeypatch):
    """宁可各跑一遍，也不能谁都不跑：定时任务停摆没有任何外部表现。"""
    def boom(*_args, **_kwargs):
        raise redis.ConnectionError("redis 不在了")

    monkeypatch.setattr(locks, "_TAKE_LEASE", boom)
    assert locks.hold_sweeper_lease(locks.sweeper_token()) is True
