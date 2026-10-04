"""急招置顶：全站名额上限，报名一截止就自动取消。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend import config, scheduled
from backend.models import Activity, now


@pytest.fixture
def no_pins(db):
    """名额是全站一份：用例前后都把别处留下的置顶清掉，数才数得准。"""
    def clear():
        (db.query(Activity).filter(Activity.pinned_at.isnot(None))
         .update({"pinned_at": None}, synchronize_session=False))
        db.commit()
    clear()
    yield
    clear()


def _pin(client, headers, act_id, pinned=True):
    return client.post(f"/api/staff/activities/{act_id}/pin", headers=headers,
                       json={"pinned": pinned})


def _brief(client, headers, act_id):
    r = client.get(f"/api/activities/{act_id}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _fill(client, admin, open_group):
    acts = [open_group(1)[0] for _ in range(config.URGENT_LIMIT)]
    for a in acts:
        r = _pin(client, admin, a.id)
        assert r.status_code == 200 and r.json()["pinned"], r.text
    return acts


def test_急招超过上限被拒且列出现有标题(client, admin, open_group, no_pins):
    acts = _fill(client, admin, open_group)
    extra, _g = open_group(1)
    r = _pin(client, admin, extra.id)
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert all(a.title in detail for a in acts)
    assert _brief(client, admin, extra.id)["pinned"] is False

    # 已经置顶的再点一次不算新占名额
    r = _pin(client, admin, acts[0].id)
    assert r.status_code == 200 and r.json()["pinned"]

    # 取消一个就腾出名额
    assert _pin(client, admin, acts[0].id, False).json()["pinned"] is False
    assert _pin(client, admin, extra.id).json()["pinned"] is True


def test_手动截止取消急招且不占名额_恢复报名不自动急招(client, admin, open_group,
                                                     no_pins):
    acts = _fill(client, admin, open_group)
    gone = acts[0]
    r = client.post(f"/api/staff/activities/{gone.id}/close", headers=admin)
    assert r.status_code == 200, r.text
    d = _brief(client, admin, gone.id)
    assert d["pinned"] is False and d["urgent"] is False
    # 已截止的活动不能置顶
    assert _pin(client, admin, gone.id).status_code == 409

    # 截止的那个让出了名额
    extra, _g = open_group(1)
    assert _pin(client, admin, extra.id).status_code == 200

    r = client.post(f"/api/staff/activities/{gone.id}/reopen", headers=admin,
                    json={"reg_end": (now() + timedelta(days=1)).isoformat()})
    assert r.status_code == 200, r.text
    d = _brief(client, admin, gone.id)
    assert d["status"] == "published"
    assert d["pinned"] is False and d["urgent"] is False


def test_自动截止取消急招(client, admin, db, open_group, no_pins):
    act, _g = open_group(1)
    assert _pin(client, admin, act.id).json()["pinned"] is True
    db.expire_all()
    row = db.get(Activity, act.id)
    row.reg_end = now() - timedelta(minutes=1)
    db.commit()
    scheduled.auto_advance_activities(db)
    d = _brief(client, admin, act.id)
    assert d["status"] == "closed"
    assert d["pinned"] is False and d["urgent"] is False
