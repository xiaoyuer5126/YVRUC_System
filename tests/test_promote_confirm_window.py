"""候补转正确认窗：夜间 23:00–8:00 不计时，且按场次封顶。"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.enroll_logic import confirm_deadline_from, promote_confirm_deadline
from backend.models import Enrollment, Shift, ShiftPeriod
from test_list_lock import _fill_and_queue, _raise_quota


def D(day, hh, mm=0, ss=0):
    return datetime(2026, 9, day, hh, mm, ss)


@pytest.mark.parametrize("start, want", [
    (D(28, 14), D(28, 16)),            # 白天：原样 2 小时
    (D(28, 8), D(28, 10)),             # 恰好 8:00：立刻开始计时
    (D(28, 7), D(28, 10)),             # 天亮前：从 8:00 起算
    (D(28, 7, 59, 59), D(28, 10)),
    (D(28, 1), D(28, 10)),             # 凌晨
    (D(28, 0), D(28, 10)),             # 恰好午夜
    (D(28, 22, 30), D(29, 9, 30)),     # 跨夜：当晚半小时 + 次日一个半小时
    (D(28, 22, 59), D(29, 9, 59)),
    (D(28, 21), D(28, 23)),            # 恰好在 23:00 用满，不拖到第二天
    (D(28, 21, 0, 1), D(29, 8, 0, 1)),
    (D(28, 23), D(29, 10)),            # 恰好 23:00：已进夜间，次日 8:00 起算
    (D(28, 23, 45), D(29, 10)),
    (D(30, 22, 30), datetime(2026, 10, 1, 9, 30)),   # 跨月
])
def test_夜间不计时(start, want):
    assert confirm_deadline_from(start) == want


def _shift(start, hours=3):
    g = Shift(id="g", activity_id="a", name="场次")
    g.periods = [ShiftPeriod(id="p", shift_id="g", start_time=start,
                             end_time=start + timedelta(hours=hours))]
    return g


def test_截止不晚于场次开始():
    # 开场前 1 小时转正：窗口只剩到开场
    assert promote_confirm_deadline(D(28, 14), _shift(D(28, 15))) == D(28, 15)
    # 前一晚 22:30 转正、次日 7:00 开场：夜间不计时算出 9:30，封到 7:00
    assert promote_confirm_deadline(D(28, 22, 30), _shift(D(29, 7))) == D(29, 7)
    # 离开场还远：不受影响
    assert promote_confirm_deadline(D(28, 22, 30), _shift(D(30, 9))) == D(29, 9, 30)
    # 恰好在开场那一刻用满
    assert promote_confirm_deadline(D(28, 14), _shift(D(28, 16))) == D(28, 16)


def test_场次进行中转正封到场次结束():
    # 14:00 开场、3 小时，15:30 手动补人：窗口本该到 17:30，封到 17:00
    assert promote_confirm_deadline(D(28, 15, 30), _shift(D(28, 14))) == D(28, 17)
    # 场次已结束：不封顶，不然一转正就超时
    assert promote_confirm_deadline(D(28, 18), _shift(D(28, 14))) == D(28, 20)


def _manual_promote(client, admin, db, open_group, take_student, start_in_hours):
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             day_offset=0,
                                             start_in_hours=start_in_hours,
                                             lock=True)
    _raise_quota(client, admin, act, g, 2)
    r = client.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    assert r.status_code == 200, r.text
    db.expire_all()
    return g, db.get(Enrollment, wl_id)


def test_落库的截止时间按夜间不计时算(client, admin, db, open_group, take_student):
    g, row = _manual_promote(client, admin, db, open_group, take_student, 10)
    assert row.status == "pending_confirm"
    want = min(confirm_deadline_from(row.promoted_at), g.start_time)
    assert row.confirm_deadline == want
    assert row.confirm_deadline > row.promoted_at


def test_贴近开场转正截止就是开场(client, admin, db, open_group, take_student):
    g, row = _manual_promote(client, admin, db, open_group, take_student, 0.5)
    assert row.status == "pending_confirm"
    assert row.confirm_deadline == g.start_time
