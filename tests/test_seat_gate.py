"""名额闸门：抢报时把洪水挡在数据库外面，但**不许改变谁能报上**。

闸门是 Redis 上一个「还剩几个」的计数器，报名先扣一个、扣不到就回「满了」，
这一步不碰数据库。水位会漂移（退选、补招、扩容都不经过它），所以这些用例
盯的就是漂移的两面：多了不能超卖，少了不能冤枉人。
"""
from __future__ import annotations

import redis
import pytest

from backend import locks
from backend.database import SessionLocal
from backend.locks import reset_gate
from backend.models import Enrollment
from conftest import login
from test_enroll import enroll


def _enrolled(g) -> int:
    db = SessionLocal()
    try:
        return db.query(Enrollment).filter(
            Enrollment.shift_id == g.id,
            Enrollment.status == "enrolled").count()
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_gates():
    """每个用例从「没预热」开始，也不把计数器留给下一个用例。"""
    keys = locks._client.keys("rucva:gate:*")
    if keys:
        locks._client.delete(*keys)
    yield
    keys = locks._client.keys("rucva:gate:*")
    if keys:
        locks._client.delete(*keys)


def test_报名会扣闸门(client, open_group, take_student):
    """机制本身：报上一个，水位少一个（这样下一个请求连库都不用查）。"""
    act, g = open_group(5)
    pid = g.test_position_id
    assert locks.gate_left(g.id, pid, "seat") is None      # 还没预热
    assert enroll(client, login(client, take_student()), act, g).status_code == 200
    assert locks.gate_left(g.id, pid, "seat") == 4         # 5 - 1


def test_水位虚高也不会超卖(client, open_group, take_student):
    """闸门说还有 99 个，也不能真发 99 个出去——名额最终由数据库说了算。

    水位虚高是常态：退选、补招、扩容都不经过闸门，计数器只会在「报名」这条
    路上减少。所以拿到闸门只是「放行」，判定还得回数据库那条老路。
    """
    act, g = open_group(2, waitlist_quota=0)
    pid = g.test_position_id
    reset_gate(g.id, pid, "seat", 99)                   # 计数器撒谎
    for _ in range(2):
        assert enroll(client, login(client, take_student()), act, g
                      ).json()["status"] == "enrolled"
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    assert _enrolled(g) == 2


def test_水位偏少也不会冤枉人(client, open_group, take_student):
    """反过来：闸门说没了、数据库其实有位子，也得让人报上。

    这是「退选之后名额要立刻能用」的那条路——退选不经过闸门，计数器还是 0。
    """
    act, g = open_group(1)
    reset_gate(g.id, g.test_position_id, "seat", 0)     # 计数器说满了
    assert enroll(client, login(client, take_student()), act, g).status_code == 200
    assert _enrolled(g) == 1


def test_退选之后水位自己纠正过来(client, open_group, take_student, db):
    """一个人报了又退，第二个人照样能报上（闸门旧了会自己核一遍）。"""
    act, g = open_group(1)
    pid = g.test_position_id
    h1 = login(client, take_student())
    assert enroll(client, h1, act, g).status_code == 200
    reset_gate(g.id, pid, "seat", 0)                    # 假装水位停在 0
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id).one()
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=h1).status_code == 200
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"


def test_闸门扣到号但没报上要还回去(client, open_group, take_student):
    """扣了号最后却转去候补：号得还，否则水位会比实际少，白白少发一个名额。"""
    act, g = open_group(1, waitlist_quota=3)
    pid = g.test_position_id
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"
    assert locks.gate_left(g.id, pid, "seat") == 0
    # 第二个人：位置满了，去看候补——走的是候补闸门，正式名额的水位不该动
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "waitlisted"
    assert locks.gate_left(g.id, pid, "seat") == 0
    assert locks.gate_left(g.id, pid, "waitlist") == 2


def test_资格不符的报名不占水位(client, open_group, take_student, db):
    """资料不全的人报不了名——但他短暂扣走的那个号必须还回去。

    不还的话，水位比实际低，后面明明有位子的人会被闸门挡住（这类漏洞平时
    看不出来，只有在抢报那几分钟才现形）。
    """
    from conftest import student_row

    act, g = open_group(2)
    pid = g.test_position_id
    u = student_row(db, take_student())
    u.phone = ""                       # 资料不全 → 报名会被 403 挡下
    db.commit()

    assert enroll(client, login(client, u.student_id), act, g).status_code == 403
    assert locks.gate_left(g.id, pid, "seat") == 2      # 号还回来了，水位没少


def test_redis不可用时照常报名(client, open_group, take_student, monkeypatch):
    """闸门是加速用的，不能成为新的单点故障：Redis 不通就退回查库那条路。"""
    act, g = open_group(1)

    def boom(*_args, **_kwargs):
        raise redis.ConnectionError("redis 不在了")

    monkeypatch.setattr(locks, "_TAKE_GATE", boom)
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"
    assert _enrolled(g) == 1


# ======================= 在途的号与核库限流 =======================

def _gate_with(db, g, kind="seat"):
    from backend.models import Position, Shift
    from backend.routers import activities as A
    gg, pos = db.get(Shift, g.id), db.get(Position, g.test_position_id)
    left_fn = A._room_left if kind == "seat" else A._waitlist_left
    return lambda: A._gate(db, gg, pos, kind, left_fn, locks.new_gate_ticket())


def test_在途的号不会被核库覆盖掉(open_group, db):
    """5 个名额，5 个请求扣到号还在资格校验里（没落库）：再来的请求一个都不放。

    从前闸门说「满了」就拿数据库剩余（此刻还是 5）覆盖计数器——在途的号被
    一次次「还」回去，抢报高峰里闸门形同虚设。
    """
    act, g = open_group(5, waitlist_quota=0)
    gate = _gate_with(db, g)
    assert sum(1 for _ in range(5) if gate()) == 5
    for _ in range(50):
        locks._client.delete(locks.gate_key(g.id, g.test_position_id, "seat")
                             + ":recheck")          # 每次都真去核库，最坏情形
        assert gate() is False


def test_没预热时并发对齐也不多放(open_group, db):
    """抢报开始那一刻闸门是空的：每个请求都去核库、都拿「剩 5 个」对齐，
    但对齐写的是「剩余 − 在途」，放进去的总数仍是 5。"""
    act, g = open_group(5, waitlist_quota=0)
    gate = _gate_with(db, g)
    key = locks.gate_key(g.id, g.test_position_id, "seat")
    passed = 0
    for _ in range(20):
        locks._client.delete(key)                   # 每次都当作没预热
        passed += bool(gate())
    assert passed == 5


def test_满了之后核库限流(open_group, db, monkeypatch):
    """名额发完后洪水里的请求绝大多数只碰 Redis，不再各查一次库。"""
    from backend.routers import activities as A

    act, g = open_group(1, waitlist_quota=0)
    gate = _gate_with(db, g)
    assert gate() is True
    calls = []
    real = A._room_left
    monkeypatch.setattr(A, "_room_left", lambda *a: calls.append(1) or real(*a))
    gate = _gate_with(db, g)
    assert all(gate() is False for _ in range(100))
    assert len(calls) <= 1


def test_退选后立刻能报(client, open_group, take_student, db):
    """退选放出名额时作废闸门：下一个人不用等核库限流过去。"""
    act, g = open_group(1, waitlist_quota=0)
    h1 = login(client, take_student())
    assert enroll(client, h1, act, g).status_code == 200
    # 满员后有人撞一下闸门，把核库的限流窗口占住
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                      Enrollment.status == "enrolled").one()
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=h1).status_code == 200
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"


def test_报上之后在途清空(client, open_group, take_student):
    act, g = open_group(3, waitlist_quota=0)
    for _ in range(2):
        assert enroll(client, login(client, take_student()), act, g).status_code == 200
    key = locks.gate_key(g.id, g.test_position_id, "seat") + ":inflight"
    assert locks._client.zcard(key) == 0
    assert locks.gate_left(g.id, g.test_position_id, "seat") == 1
