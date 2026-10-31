"""开场后不再转正候补：开场那一刻候补自动结束并通知「未能递补」，不算本人退出。"""
from __future__ import annotations

from datetime import timedelta

from backend.enroll_logic import close_waitlists_at_start
from backend.models import Enrollment, Shift, now
from conftest import inbox, login, set_shift_time, student_row
from test_list_lock import _fill_and_queue, _raise_quota


def _start_now(db, g):
    """把场次挪到刚开始：开场 1 分钟前起、3 小时后止。"""
    g = db.get(Shift, g.id)
    set_shift_time(db, g, now() - timedelta(minutes=1), now() + timedelta(hours=3))
    db.commit()


def test_开场时候补自动结束并通知(client, admin, db, open_group, take_student):
    act, g, queued, wl_id = _fill_and_queue(client, db, open_group, take_student)
    _start_now(db, g)

    assert close_waitlists_at_start(db) >= 1
    db.expire_all()
    row = db.get(Enrollment, wl_id)
    assert row.status == "cancelled" and row.cancel_type == "unfilled"
    assert inbox(db, queued, "候补未能递补")
    # 重跑不重复通知
    close_waitlists_at_start(db)
    assert len(inbox(db, queued, "候补未能递补")) == 1


def test_未能递补不算本人退出(client, admin, db, open_group, take_student):
    act, g, queued, wl_id = _fill_and_queue(client, db, open_group, take_student)
    _start_now(db, g)
    close_waitlists_at_start(db)

    u = student_row(db, queued)
    d = client.get(f"/api/staff/users/{u.id}/archive", headers=admin).json()
    assert d["cancels"]["total_count"] == 0
    assert d["locked_quit_count"] == 0


def test_开场前的候补不动(client, admin, db, open_group, take_student):
    _act, _g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student)
    close_waitlists_at_start(db)
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "waitlisted"


def test_开场后不能手动转正(client, admin, db, open_group, take_student):
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             lock=True)
    _raise_quota(client, admin, act, g, 2)
    _start_now(db, g)
    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 409 and "补招" in r.json()["detail"]
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "waitlisted"
