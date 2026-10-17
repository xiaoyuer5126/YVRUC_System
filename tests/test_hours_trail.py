"""时长变动按人留痕，以及补记录名单的「先预览、再确认」导入。

改某人时长结算值的每条路径，都在审计 changes 里按人记一条（field="hours"）：
哪位同学、从几小时到几小时、为什么，不截断。名单导入先预览（只读），带着预览
给的指纹确认才写入；预览之后名单被人改过，确认被拒、什么也不写。
"""
from __future__ import annotations

from backend.models import AuditLog, HoursAppeal, HoursRecord
from conftest import import_roster, login, student_row
from test_hours_segments import _confirm_all, _make, _manual, _record_activity


def _trail(db, act_id: str, action: str | None = None) -> list[dict]:
    """这场活动所有留痕里按人记的时长变动，按写入顺序。"""
    db.expire_all()
    q = db.query(AuditLog).filter(AuditLog.activity_id == act_id)
    if action:
        q = q.filter(AuditLog.action == action)
    return [c for log in q.order_by(AuditLog.created_at).all()
            for c in (log.changes or []) if c.get("field") == "hours"]


def _moves(changes) -> list[tuple]:
    return [(c["student_id"], c["old_hours"], c["new_hours"]) for c in changes]


def _base(act_id: str) -> str:
    return f"/api/staff/activities/{act_id}/record-roster"


# ======================= 名单导入：预览与确认 =======================

def test_预览只读_列出新增覆盖未改动跳过与找不到(client, admin, db, take_student):
    a, b, c = take_student(), take_student(), take_student()
    club = _record_activity(db)
    assert import_roster(client, admin, club.id, f"{a} 2\n{b} 3").status_code == 200

    text = f"{a} 5\n{b} 3\n{c} 30 通宵\n9999999999 1\n{c} 2"
    r = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                    json={"text": text})
    assert r.status_code == 200, r.text
    d = r.json()
    assert not d["applied"] and d["fingerprint"]
    assert [(x["student_id"], x["hours"]) for x in d["added"]] == [(c, 30.0)]
    assert "超过 24 小时" in d["added"][0]["warning"]
    assert [(x["student_id"], x["old_hours"], x["hours"])
            for x in d["overwritten"]] == [(a, 2.0, 5.0)]
    assert d["unchanged"] == 1
    assert d["not_found"] == [{"line": 4, "student_id": "9999999999"}]
    assert [x["line"] for x in d["skipped"]] == [5]
    assert not d["reapproval_required"]

    # 预览不写：名单还是原样，也没有新的导入留痕
    rows = client.get(_base(club.id), headers=admin).json()["items"]
    assert sorted((x["user"]["student_id"], x["hours"]) for x in rows) == \
        sorted([(a, 2.0), (b, 3.0)])
    assert len(_trail(db, club.id, "roster_import")) == 2

    r = client.post(f"{_base(club.id)}/import", headers=admin,
                    json={"text": text, "fingerprint": d["fingerprint"]})
    assert r.status_code == 200, r.text
    assert r.json()["applied"]
    rows = client.get(_base(club.id), headers=admin).json()["items"]
    assert sorted((x["user"]["student_id"], x["hours"]) for x in rows) == \
        sorted([(a, 5.0), (b, 3.0), (c, 30.0)])


def test_预览后名单被改_确认被拒且不写(client, admin, db, take_student):
    a, b = take_student(), take_student()
    club = _record_activity(db)
    assert import_roster(client, admin, club.id, f"{a} 2").status_code == 200
    text = f"{a} 4\n{b} 1"
    fp = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                     json={"text": text}).json()["fingerprint"]

    # 另一位管理者在这期间改了 a 的时长
    rec = client.get(_base(club.id), headers=admin).json()["items"][0]
    assert client.patch(f"{_base(club.id)}/{rec['id']}", headers=admin,
                        json={"hours": 3}).status_code == 200

    r = client.post(f"{_base(club.id)}/import", headers=admin,
                    json={"text": text, "fingerprint": fp})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ROSTER_CHANGED"
    rows = client.get(_base(club.id), headers=admin).json()["items"]
    assert [(x["user"]["student_id"], x["hours"]) for x in rows] == [(a, 3.0)]

    # 交上来的内容和预览的不同，同样不写
    fp = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                     json={"text": text}).json()["fingerprint"]
    r = client.post(f"{_base(club.id)}/import", headers=admin,
                    json={"text": f"{a} 9\n{b} 1", "fingerprint": fp})
    assert r.status_code == 409
    # 不带指纹不能直接导入
    r = client.post(f"{_base(club.id)}/import", headers=admin, json={"text": text})
    assert r.status_code == 422
    assert len(client.get(_base(club.id), headers=admin).json()["items"]) == 1


def test_预览后有人刚建档_确认被拒(client, admin, db, take_student):
    """预览时某学号还没建档、列在「找不到」里；确认前他刚建了档，名单本身没变，
    但这次确认会把他导进去——这不是管理者看过的那份结果，要重新预览。"""
    from backend.models import User, uid
    from backend.seed import fake_id_card
    a = take_student()
    club = _record_activity(db)
    newcomer = f"8{uid('s')[-8:]}"[:10]
    text = f"{a} 2\n{newcomer} 3"
    d = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                    json={"text": text}).json()
    assert [x["student_id"] for x in d["not_found"]] == [newcomer]

    db.add(User(id=uid("u"), student_id=newcomer, name="刚建档同学", gender="女",
                college="明理书院", grade="2024", campus="中关村校区",
                phone="13911112222", email=f"{newcomer}@test.local",
                wechat=f"wx_{newcomer}", id_card=fake_id_card("20050101", 77),
                user_type="student", roles=["student"]))
    db.commit()

    r = client.post(f"{_base(club.id)}/import", headers=admin,
                    json={"text": text, "fingerprint": d["fingerprint"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "ROSTER_CHANGED"
    assert client.get(_base(club.id), headers=admin).json()["items"] == []
    # 重新预览后他就在「新增」里，按新指纹确认才写入
    d = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                    json={"text": text}).json()
    assert {x["student_id"] for x in d["added"]} == {a, newcomer}
    r = client.post(f"{_base(club.id)}/import", headers=admin,
                    json={"text": text, "fingerprint": d["fingerprint"]})
    assert r.status_code == 200, r.text


def test_核准后导入_预览就说会撤销核准(client, admin, db, take_student):
    a = take_student()
    club = _record_activity(db)
    assert import_roster(client, admin, club.id, f"{a} 2").status_code == 200
    assert client.post(f"/api/staff/activities/{club.id}/hours/approve",
                       headers=admin, json={}).status_code == 200
    d = client.post(f"{_base(club.id)}/import/preview", headers=admin,
                    json={"text": f"{a} 3"}).json()
    assert d["reapproval_required"] and "会撤销" in d["message"]
    # 只预览，核准还在
    assert client.get(f"/api/staff/activities/{club.id}/hours",
                      headers=admin).json()["approved_at"]


def test_导入留痕记全名单不截断(client, admin, db, take_student):
    sids = [take_student() for _ in range(12)]
    club = _record_activity(db)
    text = "\n".join(f"{s} 2" for s in sids) + "\n8888888888 1\n"
    assert import_roster(client, admin, club.id, text).status_code == 200
    again = "\n".join(f"{s} 3" for s in sids)
    assert import_roster(client, admin, club.id, again).status_code == 200

    trail = _trail(db, club.id, "roster_import")
    assert _moves(trail[:12]) == [(s, None, 2.0) for s in sids]
    assert _moves(trail[12:]) == [(s, 2.0, 3.0) for s in sids]
    first = (db.query(AuditLog).filter(AuditLog.activity_id == club.id)
             .order_by(AuditLog.created_at).first())
    assert "…" not in first.summary
    # 找不到的学号也逐条记下
    assert any(c["field"] == "not_found" and c["new"] == "8888888888"
               for c in first.changes)


def test_名单单人修改与删除按人留痕(client, admin, db, take_student):
    a = take_student()
    club = _record_activity(db)
    assert import_roster(client, admin, club.id, f"{a} 2").status_code == 200
    rec = client.get(_base(club.id), headers=admin).json()["items"][0]
    assert client.patch(f"{_base(club.id)}/{rec['id']}", headers=admin,
                        json={"hours": 4.5}).status_code == 200
    assert client.delete(f"{_base(club.id)}/{rec['id']}",
                         headers=admin).status_code == 200
    trail = _trail(db, club.id)
    assert _moves(trail) == [(a, None, 2.0), (a, 2.0, 4.5), (a, 4.5, None)]
    assert "删除" in trail[-1]["reason"]


# ======================= 其他改时长的路径 =======================

def test_手动加减与删手动段按人留痕(client, admin, db, take_student):
    sid = take_student()
    act, u, _ = _make(db, sid, [("上午场", 2)])
    _confirm_all(client, admin, act)
    assert _manual(client, admin, act, sid, 1.5, "场外搬运").status_code == 200
    assert _manual(client, admin, act, sid, -1, "提前离场").status_code == 200
    db.expire_all()
    seg = next(s for s in db.query(HoursRecord).filter(
        HoursRecord.activity_id == act.id).one().segments if s.hours == 1.5)
    rec_id = seg.record_id
    assert client.delete(f"/api/staff/activities/{act.id}/hours/manual/{rec_id}/{seg.id}",
                         headers=admin).status_code == 200

    trail = _trail(db, act.id)
    assert _moves(trail) == [(sid, None, 2.0), (sid, 2.0, 3.5), (sid, 3.5, 2.5),
                             (sid, 2.5, 1.0)]
    assert "场外搬运" in trail[1]["reason"] and "提前离场" in trail[2]["reason"]
    assert trail[1]["name"] == u.name and "→" not in trail[1]["label"]
    assert trail[1]["old"] == "2h" and trail[1]["new"] == "3.5h"


def test_确认全部只记有变动的人_部分确认记点名的人(client, admin, db, take_student):
    a = take_student()
    act, _u, _ = _make(db, a, [("上午场", 2)])
    _confirm_all(client, admin, act)
    assert _moves(_trail(db, act.id)) == [(a, None, 2.0)]

    # 再确认一次，数没变：不多记
    _confirm_all(client, admin, act)
    assert len(_trail(db, act.id)) == 1

    # 部分确认：点名的人都记，数没变的也记
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm", headers=admin,
                    json={"items": [{"student_id": a, "hours": 3}]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{act.id}/hours/confirm", headers=admin,
                    json={"items": [{"student_id": a, "hours": 3}]})
    assert r.status_code == 200, r.text
    trail = _trail(db, act.id)
    assert _moves(trail[1:]) == [(a, 2.0, 3.0), (a, 3.0, 3.0)]
    assert "部分确认" in trail[1]["reason"]


def test_申诉采纳按人留痕(client, admin, db, take_student):
    a = take_student()
    club = _record_activity(db)
    assert import_roster(client, admin, club.id, f"{a} 4").status_code == 200
    assert client.post(f"/api/staff/activities/{club.id}/hours/approve",
                       headers=admin, json={}).status_code == 200
    stu = login(client, a)
    db.expire_all()
    rec = db.query(HoursRecord).filter(
        HoursRecord.activity_id == club.id,
        HoursRecord.user_id == student_row(db, a).id).one()
    r = client.post(f"/api/me/hours/{rec.id}/appeal", headers=stu,
                    json={"claimed_hours": 5, "reason": "实际多干了一小时"})
    assert r.status_code == 200, r.text
    appeal = db.query(HoursAppeal).filter(HoursAppeal.record_id == rec.id).one()
    r = client.post(f"/api/staff/hours-appeals/{appeal.id}/resolve", headers=admin,
                    json={"accept": True, "reply": "核实无误"})
    assert r.status_code == 200, r.text
    trail = _trail(db, club.id)
    assert _moves(trail)[-1] == (a, 4.0, 5.0)
    assert "申诉" in trail[-1]["reason"]
