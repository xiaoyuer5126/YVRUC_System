"""扣到闸门号的报名请求等锁更久，不被 503 挤掉。

扣到号的是前 N 个到的人，名额本就该是它的。抢报时几十上百个持号的人排同一把
场次锁，排在后面的等 3 秒就 503，等于把名额让给了后来重试的人。
"""
from __future__ import annotations

import threading
import time

from backend import locks
from backend.database import SessionLocal
from backend.routers import activities as activities_router
from conftest import login


def test_拿到号的人等锁更久(client, open_group, take_student, monkeypatch):
    act, g = open_group(3)
    monkeypatch.setattr(locks, "WAIT_TIMEOUT", 0.2)
    monkeypatch.setattr(locks, "TICKET_WAIT_TIMEOUT", 5.0)
    holder, bare = login(client, take_student()), login(client, take_student())
    url = f"/api/activities/{act.id}/shifts/{g.id}/enroll"
    body = {"position_id": g.test_position_id}

    held, release = threading.Event(), threading.Event()

    def hold():
        # 锁随事务走：在自己的会话上拿住，回滚时放
        s = SessionLocal()
        try:
            with locks.shift_lock(s, g.id):
                held.set()
                release.wait(5)
        finally:
            s.rollback()
            s.close()
    th = threading.Thread(target=hold)
    th.start()
    assert held.wait(5)
    try:
        # 没扣到号（闸门用不了时就是这样）：照旧等 WAIT_TIMEOUT 就 503
        real_gate = activities_router._gate
        monkeypatch.setattr(activities_router, "_gate", lambda *a: None)
        t0 = time.monotonic()
        r = client.post(url, headers=bare, json=body)
        assert r.status_code == 503, r.text
        assert time.monotonic() - t0 < 2
        monkeypatch.setattr(activities_router, "_gate", real_gate)

        # 扣到号的：锁 0.6 秒后才放，远超 WAIT_TIMEOUT，照样等到并报上
        threading.Timer(0.6, release.set).start()
        t0 = time.monotonic()
        r = client.post(url, headers=holder, json=body)
        waited = time.monotonic() - t0
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "enrolled"
        assert waited > locks.WAIT_TIMEOUT
    finally:
        release.set()
        th.join()
