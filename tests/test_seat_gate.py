"""名额闸门：抢报时把洪水挡在数据库外面，但**不许改变谁能报上**。

闸门是 Redis 上一个「还剩几个」的计数器（见 locks.py 的「名额闸门」）。它会
漂移——管理者改名额、退选、补招都不经过它——所以这里分两层测：

- 报名接口的行为：不管计数器偏高还是偏低，谁能报上只由数据库说了算。
  漂移一律用真实操作造出来（管理者改名额），不去直接改 Redis 里的数。
- 闸门本身的契约（locks 模块的公开函数）：在途的号不会被核库覆盖掉、
  并发预热不会多放、满了之后核库限流。这是它存在的理由，只能在这一层量。
"""
from __future__ import annotations

import pytest
import redis

from backend import locks
from backend.models import Enrollment
from conftest import editing, login, student_row
from test_enroll import enroll


@pytest.fixture(autouse=True)
def clean_gates():
    """每个用例从「没预热」开始，也不把计数器留给下一个用例。"""
    def wipe():
        keys = locks._client.keys("rucva:gate:*")
        if keys:
            locks._client.delete(*keys)
    wipe()
    yield
    wipe()


def _enrolled(db, g) -> int:
    db.expire_all()
    return db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                       Enrollment.status == "enrolled").count()


def _set_quota(client, admin, act, g, quota):
    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{g.test_position_id}",
                         headers=admin, json={"quota": quota})
        assert r.status_code == 200, r.text


# ======================= 报名接口的行为 =======================

def test_名额调小后计数器偏高也不会超卖(client, admin, db, open_group, take_student):
    """闸门按 5 个预热过，管理者随后把名额改成 2：闸门还以为有位子，
    但报上的人只能是 2 个。"""
    act, g = open_group(5, waitlist_quota=0)
    assert enroll(client, login(client, take_student()), act, g).status_code == 200
    _set_quota(client, admin, act, g, 2)
    assert enroll(client, login(client, take_student()), act, g).status_code == 200
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    assert _enrolled(db, g) == 2


def test_名额调大后计数器偏低也不会冤枉人(client, admin, db, open_group, take_student):
    """满员后闸门停在 0，管理者加了一个名额：下一个人马上得报得上，
    不用等核库限流窗口过去（改名额会作废闸门）。"""
    act, g = open_group(1, waitlist_quota=0)
    assert enroll(client, login(client, take_student()), act, g).status_code == 200
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    _set_quota(client, admin, act, g, 2)     # 刚撞过闸门，核库限流窗口还没过
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"
    assert _enrolled(db, g) == 2


def test_退选放出的名额下一个人马上能报(client, db, open_group, take_student):
    """满员、有人撞过闸门之后退选：不用等核库限流过去。"""
    act, g = open_group(1, waitlist_quota=0)
    h1 = login(client, take_student())
    assert enroll(client, h1, act, g).status_code == 200
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    enr = db.query(Enrollment).filter(Enrollment.shift_id == g.id,
                                      Enrollment.status == "enrolled").one()
    assert client.delete(f"/api/activities/enrollments/{enr.id}",
                         headers=h1).status_code == 200
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"


def test_报不上的人不占名额(client, db, open_group, take_student):
    """资料不全被 403 挡下的人，闸门里扣走的号要还回去：两个名额两个合格的人
    都得报上，而不是只剩一个。"""
    act, g = open_group(2, waitlist_quota=0)
    bad = student_row(db, take_student())
    bad.phone = ""
    db.commit()
    assert enroll(client, login(client, bad.student_id), act, g).status_code == 403
    for _ in range(2):
        assert enroll(client, login(client, take_student()), act, g
                      ).json()["status"] == "enrolled"
    assert _enrolled(db, g) == 2


def test_满员进候补_候补满了才拒(client, db, open_group, take_student):
    act, g = open_group(1, waitlist_quota=2)
    statuses = [enroll(client, login(client, take_student()), act, g).json().get("status")
                for _ in range(3)]
    assert statuses == ["enrolled", "waitlisted", "waitlisted"]
    r = enroll(client, login(client, take_student()), act, g)
    assert r.status_code == 409 and "候补名额已满" in r.json()["detail"]


def test_redis不可用时照常报名(client, db, open_group, take_student, monkeypatch):
    """闸门是加速用的，不能成为新的单点故障：闸门的 Redis 调用全部失败时，
    退回查库那条路，名额照样按数据库发。"""
    act, g = open_group(1, waitlist_quota=0)

    def boom(*_args, **_kwargs):
        raise redis.ConnectionError("redis 不在了")

    for name in ("_TAKE_GATE", "_SYNC_GATE", "_SETTLE_GATE"):
        monkeypatch.setattr(locks, name, boom)
    assert enroll(client, login(client, take_student()), act, g
                  ).json()["status"] == "enrolled"
    assert enroll(client, login(client, take_student()), act, g).status_code == 409
    assert _enrolled(db, g) == 1


# ======================= 闸门本身的契约 =======================

def _take(g, left_in_db):
    """模拟一个请求过闸：先扣，扣不到就按数据库剩余对齐再扣（跟报名接口同一顺序）。"""
    pid, ticket = g.test_position_id, locks.new_gate_ticket()
    got = locks.take_gate(g.id, pid, "seat", ticket)
    if got == locks.GATE_OK:
        return True
    return locks.sync_and_take_gate(g.id, pid, "seat", ticket,
                                    left_in_db) == locks.GATE_OK


def test_在途的号不会被核库覆盖掉(open_group):
    """5 个名额，5 个请求扣到号还没落库（数据库里仍是「剩 5 个」）：
    再来的请求一个都不放。从前核库直接覆盖计数器，在途的号被一次次还回去。"""
    act, g = open_group(5)
    assert sum(_take(g, 5) for _ in range(5)) == 5
    assert not any(_take(g, 5) for _ in range(50))


def test_没预热时并发对齐也不多放(open_group):
    """抢报开始那一刻闸门是空的，每个请求都拿「剩 5 个」去对齐——放进去的总数仍是 5。"""
    act, g = open_group(5)
    key = locks.gate_key(g.id, g.test_position_id, "seat")
    passed = 0
    for _ in range(20):
        locks._client.delete(key)                   # 每次都当作没预热
        passed += _take(g, 5)
    assert passed == 5


def test_结算后号不再算在途(open_group):
    """用掉的号结算后，下一次对齐就按数据库的新剩余来，不会再被当成在途多减一次。"""
    act, g = open_group(2)
    pid = g.test_position_id
    t = locks.new_gate_ticket()
    assert locks.sync_and_take_gate(g.id, pid, "seat", t, 2) == locks.GATE_OK
    locks.settle_gate(g.id, pid, "seat", t, restore=False)   # 落库了：数据库剩 1
    locks._client.delete(locks.gate_key(g.id, pid, "seat"))
    assert _take(g, 1) is True
    assert _take(g, 1) is False


def test_满了之后核库限流(open_group):
    """名额发完后，同一岗位在限流窗口里只放一个请求去核库，其余直接回「满了」。"""
    act, g = open_group(1)
    pid = g.test_position_id
    assert locks.may_recheck_gate(g.id, pid, "seat") is True
    # 次数别多：这几次都得落在同一个限流窗口（GATE_RECHECK_MS）里
    assert not any(locks.may_recheck_gate(g.id, pid, "seat") for _ in range(5))
