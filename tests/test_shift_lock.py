"""场次锁：Postgres 事务级咨询锁 + 进程内名额（见 locks.shift_lock）。

锁随事务走，所以要验两件事：同一场次的两个写操作真的排成了队（锁没有在
中途某次 commit 时提前放掉），以及请求在锁里失败时数据库锁和进程名额都还了
——不还的话，这个场次或者这个进程从此就再也进不了锁。
"""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from backend import config, locks
from backend import enroll_logic as logic
from backend.database import SessionLocal
from backend.main import app
from backend.models import Enrollment
from conftest import login
from test_enroll import enroll


def _slots_all_free() -> bool:
    got = [locks._SLOTS.acquire(timeout=0) for _ in range(config.LOCK_SLOTS)]
    for ok in got:
        if ok:
            locks._SLOTS.release()
    return all(got)


def _lock_free(shift_id: str) -> bool:
    """另开一个会话能不能立刻拿到这个场次的锁。"""
    s = SessionLocal()
    try:
        with locks.shift_lock(s, shift_id, wait=0.3):
            return True
    except Exception:  # noqa: BLE001  503 就是没拿到
        return False
    finally:
        s.rollback()
        s.close()


def test_退选递补与新报名排队_空出来的位子归候补队首(client, open_group,
                                            take_student, monkeypatch):
    """1 个名额：A 已报上、B 在候补。A 退选的同时 C 来报名。

    退选、递补必须在同一个事务里做完再放锁：锁若在「退选已提交、递补还没做」
    时就放了，C 正好进来，看见空出来的位子直接报上，B 排了半天反被插队。
    这里把递补拖慢，保证 C 一定是在 A 持锁期间到的。
    """
    act, g = open_group(1, waitlist_quota=3)
    a, b, c = take_student(), take_student(), take_student()
    ha = login(client, a)
    a_id = enroll(client, ha, act, g).json()["enrollment_id"]
    queued = enroll(client, login(client, b), act, g).json()
    assert queued["status"] == "waitlisted"
    hc = login(client, c)

    in_promote = threading.Event()
    real_promote = logic.promote_all

    def slow_promote(db, shift):
        in_promote.set()
        time.sleep(0.5)
        return real_promote(db, shift)

    monkeypatch.setattr(logic, "promote_all", slow_promote)
    out = {}

    def cancel():
        with TestClient(app) as c2:
            out["cancel"] = c2.delete(f"/api/activities/enrollments/{a_id}",
                                      headers=ha)

    th = threading.Thread(target=cancel)
    th.start()
    try:
        assert in_promote.wait(5)
        r = enroll(client, hc, act, g)          # A 还攥着锁、正在递补
    finally:
        th.join()
    assert out["cancel"].status_code == 200, out["cancel"].text
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "waitlisted"   # 位子已经给了 B，C 去排队

    check = SessionLocal()
    try:
        assert check.get(Enrollment, queued["enrollment_id"]).status == "pending_confirm"
        taken = check.query(Enrollment).filter(
            Enrollment.shift_id == g.id,
            Enrollment.status.in_(("enrolled", "pending_confirm"))).count()
        assert taken == 1                       # 不超卖
    finally:
        check.close()


def test_锁里报错_数据库锁和进程名额都还回来(client, open_group, take_student,
                                  monkeypatch):
    act, g = open_group(3)
    h = login(client, take_student())
    assert enroll(client, h, act, g).status_code == 200

    # 业务拒绝：重复报名在锁里判出来，抛 409
    r = enroll(client, h, act, g)
    assert r.status_code == 409, r.text
    assert _slots_all_free()
    assert _lock_free(g.id)

    # 意外异常：锁里炸成 500，会话被关掉、事务回滚，锁照样放
    def boom(*_a, **_kw):
        raise RuntimeError("锁里出错")

    monkeypatch.setattr(logic, "has_room", boom)
    with TestClient(app, raise_server_exceptions=False) as c2:
        r = enroll(c2, login(c2, take_student()), act, g)
    assert r.status_code == 500
    assert _slots_all_free()
    assert _lock_free(g.id)


def test_等不到数据库锁回503_且不影响后来的人(open_group, monkeypatch):
    """锁被别的事务攥着：等满了回 503，名额还回来；那边一放，马上能拿到。"""
    _act, g = open_group(1)
    holder = SessionLocal()
    try:
        with locks.shift_lock(holder, g.id):
            t0 = time.monotonic()
            assert not _lock_free(g.id)          # 等 0.3 秒，503
            assert time.monotonic() - t0 < 2
            assert _slots_all_free() is False    # holder 自己还占着一个
        assert _slots_all_free()                 # 出了 with 块名额就还了……
        assert not _lock_free(g.id)              # ……但数据库锁要等事务结束
    finally:
        holder.rollback()
        holder.close()
    assert _lock_free(g.id)
