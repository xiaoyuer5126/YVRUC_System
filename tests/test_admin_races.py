"""管理端一键操作与别人同时动同一条记录：锁里要重读，状态变了就明说。

一键操作（手动转正、释放扣留名额、录取/不录取）先在锁外读记录、判状态，
再排场次锁。排锁那几十毫秒里，另一位管理者或者本人可能已经动过这条记录；
不在锁里重读的话，就会照着旧状态再做一遍——给已经退出候补的人开确认窗、
把已录取的申请盖成「不录取」。

造「锁外读到的已经旧了」这一瞬：让先到的那个请求在锁里多待一会儿（拖慢它
锁内的一步），后到的请求这时读到的必然是提交前的状态，然后去排锁。
"""
from __future__ import annotations

import threading
import time

from fastapi.testclient import TestClient

from backend import enroll_logic as logic
from backend.main import app
from backend.models import Activity, Application, Enrollment, VbConfirm
from backend.routers import liaison as liaison_router, screening, staff_activities
from conftest import login
from test_list_lock import _fill_and_queue, _raise_quota
from test_screening import _create_screened, _answers, _holders
from test_vb_confirm import _approved_act, _lock, _office, _pending_confirm

HOLD = 0.6        # 先到的请求在锁里多待这么久
GAP = 0.15        # 后到的请求晚这么久出发：够它读到旧状态，又一定在先到的提交之前


def _slow_once(monkeypatch, module, name):
    """把 module.name 的第一次调用拖慢 HOLD 秒（只拖先到的那个请求）。"""
    real = getattr(module, name)
    fired = threading.Event()

    def slow(*a, **kw):
        if not fired.is_set():
            fired.set()
            time.sleep(HOLD)
        return real(*a, **kw)
    monkeypatch.setattr(module, name, slow)


def _race(first, second):
    """first 先出发，GAP 秒后 second 出发；返回两边的响应。"""
    out = {}

    def run(key, fn):
        with TestClient(app) as c:
            out[key] = fn(c)
    t1 = threading.Thread(target=run, args=("first", first))
    t2 = threading.Thread(target=run, args=("second", second))
    t1.start()
    time.sleep(GAP)
    t2.start()
    t1.join()
    t2.join()
    return out["first"], out["second"]


def test_两位管理者同时手动转正只开一次确认窗(client, admin, db, open_group,
                                         take_student, monkeypatch):
    act, g, _queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                             lock=True)
    _raise_quota(client, admin, act, g, 2)
    _slow_once(monkeypatch, staff_activities, "audit_log")

    def promote(c):
        return c.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin)
    first, second = _race(promote, promote)

    assert first.status_code == 200, first.text
    assert second.status_code == 409 and "已被处理" in second.json()["detail"]
    db.expire_all()
    assert db.get(Enrollment, wl_id).status == "pending_confirm"


def test_本人刚退出候补时管理者转正不会再给他开确认窗(client, admin, db, open_group,
                                            take_student, monkeypatch):
    act, g, queued, wl_id = _fill_and_queue(client, db, open_group, take_student,
                                            lock=True)
    _raise_quota(client, admin, act, g, 2)
    stu = login(client, queued)
    # 本人的退出在锁里多待一会儿；管理者这时读到的还是「候补中」
    _slow_once(monkeypatch, logic, "record_change")

    first, second = _race(
        lambda c: c.delete(f"/api/activities/enrollments/{wl_id}", headers=stu),
        lambda c: c.post(f"/api/staff/enrollments/{wl_id}/promote", headers=admin))

    assert first.status_code == 200, first.text
    assert second.status_code == 409 and "已取消" in second.json()["detail"]
    db.expire_all()
    row = db.get(Enrollment, wl_id)
    assert row.status == "cancelled" and row.confirm_deadline is None


def test_一位录取一位不录取_结论不会被后提交的盖掉(client, admin, db, take_student,
                                            monkeypatch):
    act_id, ids, q = _create_screened(client, admin, db)
    sid = take_student()
    r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                    json={"position_ids": _holders(db, ids), "answers": _answers(q)})
    assert r.status_code == 200, r.text
    app_id = client.get(f"/api/staff/activities/{act_id}/applications",
                        headers=admin).json()["applications"][0]["id"]
    _slow_once(monkeypatch, screening, "audit_log")

    def decide(accept):
        return lambda c: c.post(
            f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
            json={"application_ids": [app_id], "accept": accept,
                  "targets": [{"shift_id": ids[0]}]})
    first, second = _race(decide(True), decide(False))

    assert first.status_code == 200, first.text
    assert second.status_code == 409 and "已被其他管理者处理" in second.json()["detail"]
    db.expire_all()
    assert db.get(Application, app_id).status == "accepted"
    statuses = {e.status for e in db.query(Enrollment)
                .filter(Enrollment.activity_id == act_id)}
    assert "enrolled" in statuses and "applied" not in statuses


def test_两位管理者同时把两个人录到同一批场次_最后一个名额只给一人(
        client, admin, db, take_student, monkeypatch):
    """两份申请都录到第 1、2 场，第 2 场只剩一个名额。两个请求锁的是同一批场次
    （按 id 排序加锁），只会一先一后：后到的看得见先到的那一个，整份拦下。"""
    from backend.models import Shift
    act_id, ids, q = _create_screened(client, admin, db)
    db.get(Shift, ids[1]).placeholder().quota = 1
    db.commit()
    for _ in range(2):
        r = client.post(f"/api/activities/{act_id}/apply",
                        headers=login(client, take_student()),
                        json={"position_ids": _holders(db, ids), "answers": _answers(q)})
        assert r.status_code == 200, r.text
    app_ids = [a["id"] for a in client.get(
        f"/api/staff/activities/{act_id}/applications",
        headers=admin).json()["applications"]]
    _slow_once(monkeypatch, screening, "audit_log")

    def decide(app_id, order):
        return lambda c: c.post(
            f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
            json={"application_ids": [app_id], "accept": True,
                  "picks": {app_id: [{"shift_id": ids[i]} for i in order]}})
    first, second = _race(decide(app_ids[0], (0, 1)), decide(app_ids[1], (1, 0)))

    assert first.status_code == 200, first.text
    assert second.status_code == 409 and "名额" in second.json()["detail"], second.text
    db.expire_all()
    assert db.get(Application, app_ids[1]).status == "applied"
    enrolled = (db.query(Enrollment)
                .filter(Enrollment.activity_id == act_id,
                        Enrollment.status == "enrolled").all())
    assert sorted(e.shift_id for e in enrolled) == sorted(ids)
    assert {e.user_id for e in enrolled} == {db.get(Application, app_ids[0]).user_id}


def test_外联同时确认和退回只保留先提交的结论(client, admin, db, liaison,
                                      monkeypatch):
    act = _approved_act(db, _office(db).id)
    _lock(client, admin, act)
    row = _pending_confirm(client, admin, act)
    url = f"/api/staff/vb-confirms/{row['id']}/decide"
    _slow_once(monkeypatch, liaison_router, "audit_log")

    first, second = _race(
        lambda c: c.post(url, headers=admin, json={"approve": True}),
        lambda c: c.post(url, headers=admin,
                         json={"approve": False, "reply": "志愿北京未查到"}))
    assert first.status_code == 200 and first.json()["status"] == "confirmed", first.text
    assert second.status_code == 409, second.text
    db.expire_all()
    assert db.get(VbConfirm, row["id"]).status == "confirmed"
    assert db.get(Activity, act.id).status == "completed"
