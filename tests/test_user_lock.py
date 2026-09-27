"""用户锁：同一个人同时报两场时段重叠、却不在同一场次的班（见 locks.user_lock）。

两个请求拿的是两把不同的场次锁，互不阻塞；锁外各自查时段冲突时都还看不见
对方，不在锁里按人排队重查的话两条都会落库。这里把「进锁之后、落库之前」
拖慢，保证两个请求一定都过了锁外那道检查、并且同时在各自的锁里——没有
用户锁时这个用例每一轮都是两个 200。
"""
from __future__ import annotations

import contextlib
import threading
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from backend import locks
from backend.database import SessionLocal
from backend.main import app
from backend.models import Activity, Enrollment, Shift, User, now, uid
from backend.routers import activities as activities_router
from conftest import login
from test_enroll import enroll


@pytest.fixture
def slow_in_lock(monkeypatch):
    """进了报名的场次锁之后先睡一会儿再往下走。"""
    real = activities_router.shift_lock

    @contextlib.contextmanager
    def slow(db, *ids, **kw):
        with real(db, *ids, **kw):
            time.sleep(0.4)
            yield
    monkeypatch.setattr(activities_router, "shift_lock", slow)


def _race(pairs, h):
    """每个 (act, g) 一条线程，齐步走发报名，返回各自的响应。"""
    gate = threading.Barrier(len(pairs))
    out = [None] * len(pairs)

    def go(i, act, g):
        with TestClient(app) as c:
            gate.wait()
            out[i] = enroll(c, h, act, g)

    ths = [threading.Thread(target=go, args=(i, a, g))
           for i, (a, g) in enumerate(pairs)]
    for t in ths:
        t.start()
    for t in ths:
        t.join()
    return out


def _live(user_id: str, shift_ids: list[str]) -> int:
    s = SessionLocal()
    try:
        return s.query(Enrollment).filter(
            Enrollment.user_id == user_id,
            Enrollment.shift_id.in_(shift_ids),
            Enrollment.status.in_(("enrolled", "waitlisted"))).count()
    finally:
        s.close()


def _codes(rs) -> list[int]:
    return sorted(r.status_code for r in rs)


def test_同一人同时报两场重叠的班_只成一个(client, open_group, take_student,
                                slow_in_lock):
    for k in range(4):
        day = 600 + k
        act1, g1 = open_group(5, day_offset=day, start_in_hours=4)
        act2, g2 = open_group(5, day_offset=day, start_in_hours=6)   # 与 g1 重叠
        sid = take_student()
        h = login(client, sid)
        rs = _race([(act1, g1), (act2, g2)], h)
        assert _codes(rs) == [200, 409], [r.text for r in rs]
        lost = next(r for r in rs if r.status_code == 409)
        assert "冲突" in lost.json()["detail"]["message"]
        s = SessionLocal()
        try:
            uid_ = s.query(User.id).filter(User.student_id == sid).scalar()
        finally:
            s.close()
        assert _live(uid_, [g1.id, g2.id]) == 1


def test_撞的是另一场的彩排_同样只成一个(client, db, open_group, take_student,
                                slow_in_lock):
    """主场次不重叠，但 A 的彩排与 B 重叠：锁里重比时段也要带上附属场次。"""
    act1, g1 = open_group(5, day_offset=640, start_in_hours=4)
    act2, g2 = open_group(5, day_offset=650, start_in_hours=4)
    db.add(Shift(id=uid("g"), activity_id=act1.id, name="彩排",
                 kind="rehearsal", parent_id=g1.id,
                 start_time=g2.start_time + timedelta(hours=1),
                 end_time=g2.start_time + timedelta(hours=2)))
    db.commit()
    h = login(client, take_student())
    rs = _race([(act1, g1), (act2, g2)], h)
    assert _codes(rs) == [200, 409], [r.text for r in rs]


def test_排锁期间进了编辑态_锁里拒掉(client, open_group, take_student,
                            monkeypatch):
    """锁外那道编辑态检查过了之后，负责人才点「调整岗位」：锁里要再看一眼。

    锁外查到的附属场次名单要在锁里接着用，编辑态一开就可能改掉它。
    """
    act, g = open_group(5)
    real = activities_router.shift_lock

    @contextlib.contextmanager
    def edit_then_lock(db, *ids, **kw):
        s = SessionLocal()
        try:
            s.get(Activity, act.id).editing_since = now()
            s.commit()
        finally:
            s.close()
        with real(db, *ids, **kw):
            yield
    monkeypatch.setattr(activities_router, "shift_lock", edit_then_lock)
    try:
        r = enroll(client, login(client, take_student()), act, g)
        assert r.status_code == 409 and "暂停" in r.json()["detail"], r.text
    finally:
        s = SessionLocal()
        s.get(Activity, act.id).editing_since = None
        s.commit()
        s.close()


def test_拿了用户锁不能再拿场次锁(open_group):
    """先用户后场次会和别人互相等成死锁，shift_lock 直接拒绝。"""
    _act, g = open_group(1)
    s = SessionLocal()
    try:
        locks.user_lock(s, "u-order-check")
        with pytest.raises(RuntimeError):
            with locks.shift_lock(s, g.id):
                pass
        s.rollback()
        with locks.shift_lock(s, g.id, user_id="u-order-check"):
            pass              # 新事务里按正确顺序拿，没问题
    finally:
        s.rollback()
        s.close()
