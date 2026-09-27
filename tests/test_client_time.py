"""客户端传来的时间串一律换成本地裸时间，带不带时区都一样。

库里、now()、比较用的都是不带时区的本地时间（TZ=Asia/Shanghai）。带时区的串
（…Z、…+08:00）从前原样解析成带时区的时间，一比较就 TypeError，接口 500。
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from backend.models import parse_local_dt
from test_checkin import two_shift_act  # noqa: F401
from test_hours_appeal import served  # noqa: F401


@pytest.mark.parametrize("raw", ["2026-09-27T09:00", "2026-09-27 09:00",
                                 "2026-09-27T01:00Z", "2026-09-27T01:00:00+00:00",
                                 "2026-09-27T09:00+08:00", "2026-09-27T02:00+01:00"])
def test_各种写法都解析成同一个本地时间(raw):
    dt = parse_local_dt(raw)
    assert dt == datetime(2026, 9, 27, 9, 0) and dt.tzinfo is None


def test_格式不对抛_ValueError():
    with pytest.raises(ValueError):
        parse_local_dt("明天")


def test_建签到码和改时段收带时区的时间(client, admin, two_shift_act):
    _act, am, _train = two_shift_act
    r = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                    json={"mode": "shift", "valid_from": "2026-09-27T00:30Z",
                          "valid_to": "2026-09-27T12:00+08:00"})
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    r = client.patch(f"/api/staff/checkin-tasks/{tid}", headers=admin,
                     json={"valid_from": "2026-09-27T01:00Z",
                           "valid_to": "2026-09-27T13:00+08:00"})
    assert r.status_code == 200, r.text
    assert (r.json()["valid_from"], r.json()["valid_to"]) == \
        ("2026-09-27T09:00", "2026-09-27T13:00")
    r = client.post(f"/api/staff/shifts/{am.id}/checkin-tasks", headers=admin,
                    json={"mode": "shift", "valid_from": "明天"})
    assert r.status_code == 400


def test_核准时长的申诉截止收带时区的时间(client, admin, served):
    act, _g, _u = served
    later = (datetime.now() + timedelta(days=2)).replace(second=0, microsecond=0)
    aware = later.astimezone().isoformat()          # 带 +08:00
    r = client.post(f"/api/staff/activities/{act.id}/hours/approve", headers=admin,
                    json={"appeal_deadline": aware})
    assert r.status_code == 200, r.text
