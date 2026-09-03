"""报名后筛选：填表申请、意向场次、录取与未录取；联络群二维码只弹一次。"""
from __future__ import annotations

from datetime import timedelta

import pytest

from backend.models import (Activity, Application, Enrollment, Notification,
                            User, now)
from conftest import login


def _create_screened(client, admin, db, *, status="published", shifts=2):
    r = client.post("/api/staff/activities", headers=admin,
                    json={"title": f"筛选活动 {now():%H%M%S%f}", "organizer": "校青协",
                          "recruit_mode": "screened", "intro": "x",
                          "location": "东门", "contact_group_qr": "data:qr",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(days=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=3)).isoformat()})
    assert r.status_code == 200, r.text
    act_id = r.json()["id"]
    ids = []
    for i in range(shifts):
        start = now() + timedelta(days=5, hours=i * 5)
        r = client.post(f"/api/staff/activities/{act_id}/shifts", headers=admin,
                        json={"name": f"第{i + 1}场", "mode": "assign", "capacity": 10,
                              "start_time": start.isoformat(),
                              "end_time": (start + timedelta(hours=3)).isoformat()})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [
                       {"kind": "single", "title": "参加过吗", "options": ["是", "否"]},
                       {"kind": "multi", "title": "能做什么",
                        "options": ["引导", "搬运", "登记"]},
                       {"kind": "text", "title": "介绍自己", "required": False},
                       {"kind": "file", "title": "证明材料", "required": False},
                   ]})
    assert r.status_code == 200, r.text
    questions = r.json()["questions"]
    db.get(Activity, act_id).status = status
    db.commit()
    return act_id, ids, questions


def _answers(questions, **override):
    by_title = {q["title"]: q["id"] for q in questions}
    base = {by_title["参加过吗"]: "是", by_title["能做什么"]: ["引导", "登记"],
            by_title["介绍自己"]: "我很热心"}
    for title, v in override.items():
        base[by_title[title]] = v
    return base


def test_筛选活动的场次一律统一分配(client, admin, db):
    act_id, ids, _q = _create_screened(client, admin, db, status="draft")
    d = client.get(f"/api/activities/{act_id}", headers=admin).json()
    assert d["recruit_mode"] == "screened"
    assert all(g["mode"] == "assign" for g in d["shifts"])
    assert all(any(p["is_placeholder"] for p in g["positions"]) for g in d["shifts"])
    # 就算传 pick 也落成统一分配
    start = now() + timedelta(days=6)
    r = client.post(f"/api/staff/activities/{act_id}/shifts", headers=admin,
                    json={"name": "加场", "mode": "pick", "capacity": 5,
                          "start_time": start.isoformat(),
                          "end_time": (start + timedelta(hours=2)).isoformat()})
    assert r.status_code == 200, r.text
    d = client.get(f"/api/activities/{act_id}", headers=admin).json()
    assert next(g for g in d["shifts"] if g["name"] == "加场")["mode"] == "assign"


def test_填表申请_必填校验_只能填一次(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    form = client.get(f"/api/activities/{act_id}/form", headers=t).json()
    assert [x["title"] for x in form["questions"]] == ["参加过吗", "能做什么", "介绍自己", "证明材料"]
    assert form["mine"] is None

    # 直接报名的口子关着
    r = client.post(f"/api/activities/{act_id}/shifts/{ids[0]}/enroll", headers=t,
                    json={"position_id": None})
    assert r.status_code == 409 and "报名表" in r.json()["detail"]

    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": ids, "answers": _answers(q, 参加过吗=None)})
    assert r.status_code == 400 and "参加过吗" in r.json()["detail"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": [], "answers": _answers(q)})
    assert r.status_code == 400
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": ids, "answers": _answers(q)})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": ids, "answers": _answers(q)})
    assert r.status_code == 409

    d = client.get(f"/api/activities/{act_id}", headers=t).json()
    mine = d["application"]["mine"]
    assert mine["status"] == "applied"
    assert {w["shift_id"] for w in mine["wanted"]} == set(ids)
    # 我的报名里每个意向场次一条「待筛选」
    rows = client.get("/api/me/enrollments", headers=t).json()
    assert sorted(r["status"] for r in rows if r["activity"]["id"] == act_id) == ["applied", "applied"]
    assert all(not r["group_qr_pending"] and not r["contact_group_qr"]
               for r in rows if r["activity"]["id"] == act_id)


def test_申请时就拦时段冲突(client, admin, db, enrolled_activity, take_student):
    act, shift, u = enrolled_activity
    mine = login(client, u.student_id)
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    # 把筛选活动的场次挪到跟已报名的场次同一时段
    from backend.models import Shift
    g = db.get(Shift, ids[0])
    g.start_time, g.end_time = shift.start_time, shift.end_time
    db.commit()
    r = client.post(f"/api/activities/{act_id}/apply", headers=mine,
                    json={"shift_ids": ids, "answers": _answers(q)})
    assert r.status_code == 409 and r.json()["detail"]["conflicts"]


def test_撤回申请(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    client.post(f"/api/activities/{act_id}/apply", headers=t,
                json={"shift_ids": ids, "answers": _answers(q)})
    rows = client.get("/api/me/enrollments", headers=t).json()
    enr_id = next(r["id"] for r in rows if r["activity"]["id"] == act_id)
    # 意向记录不能走退选，只能撤回申请
    assert client.delete(f"/api/activities/enrollments/{enr_id}",
                         headers=t).status_code == 409
    assert client.delete(f"/api/activities/{act_id}/apply", headers=t).status_code == 200
    assert client.delete(f"/api/activities/{act_id}/apply", headers=t).status_code == 409
    d = client.get(f"/api/activities/{act_id}", headers=t).json()
    assert d["application"]["mine"]["status"] == "withdrawn"


def test_录取与未录取(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    sids = [take_student() for _ in range(3)]
    for sid in sids:
        r = client.post(f"/api/activities/{act_id}/apply", headers=login(client, sid),
                        json={"shift_ids": ids, "answers": _answers(q)})
        assert r.status_code == 200, r.text

    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    assert len(lst["applications"]) == 3
    first = lst["applications"][0]
    assert first["status"] == "applied" and len(first["wanted"]) == 2
    assert [a["value"] for a in first["answers"]][:2] == ["是", ["引导", "登记"]]
    app_ids = [a["id"] for a in lst["applications"]]

    # 录取前两人到第二场；第三人不录取
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[:2], "accept": True,
                          "shift_id": ids[1]})
    assert r.status_code == 200 and r.json()["count"] == 2
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[2:], "accept": False})
    assert r.status_code == 200
    # 处理过的不能再处理
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids[:1], "accept": False})
    assert r.status_code == 409

    u0 = db.query(User).filter(User.student_id == sids[0]).one()
    rows = db.query(Enrollment).filter(Enrollment.activity_id == act_id,
                                       Enrollment.user_id == u0.id).all()
    by_shift = {r.shift_id: r for r in rows}
    assert by_shift[ids[1]].status == "enrolled"
    assert by_shift[ids[1]].position_id is not None      # 落在占位岗位上
    assert by_shift[ids[0]].status == "cancelled"
    assert by_shift[ids[0]].cancel_type == "not_chosen"
    assert (db.query(Notification)
            .filter(Notification.user_id == u0.id,
                    Notification.title.like("录取通知｜%")).count() == 1)

    # 录取的人：我的报名里只剩录到的那一场，联络群二维码待弹
    mine = client.get("/api/me/enrollments", headers=login(client, sids[0])).json()
    mine = [r for r in mine if r["activity"]["id"] == act_id]
    assert len(mine) == 1 and mine[0]["status"] == "enrolled"
    assert mine[0]["contact_group_qr"] == "data:qr" and mine[0]["group_qr_pending"]
    t0 = login(client, sids[0])
    assert client.post(f"/api/me/enrollments/{mine[0]['id']}/group-qr-seen",
                       headers=t0).status_code == 200
    mine = client.get("/api/me/enrollments", headers=t0).json()
    assert not next(r for r in mine if r["activity"]["id"] == act_id)["group_qr_pending"]

    # 未录取：通知留余地，档案里不留痕
    u2 = db.query(User).filter(User.student_id == sids[2]).one()
    n = (db.query(Notification)
         .filter(Notification.user_id == u2.id,
                 Notification.title.like("报名结果｜%")).one())
    assert "暂未能" in n.content
    rows2 = client.get("/api/me/enrollments", headers=login(client, sids[2])).json()
    assert {r["cancel_type"] for r in rows2 if r["activity"]["id"] == act_id} == {"rejected"}
    archive = client.get(f"/api/staff/users/{u2.id}/archive", headers=admin).json()
    assert archive["violations"] == []
    assert act_id not in [h["activity_id"] for h in archive["history"]]


def test_录取名额不够先拦下(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db, shifts=1)
    from backend.models import Shift
    holder = db.get(Shift, ids[0]).placeholder()
    holder.quota = 1
    db.commit()
    app_ids = []
    for _ in range(2):
        t = login(client, take_student())
        client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": ids, "answers": _answers(q)})
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    app_ids = [a["id"] for a in lst["applications"]]
    r = client.post(f"/api/staff/activities/{act_id}/applications/decide", headers=admin,
                    json={"application_ids": app_ids, "accept": True})
    assert r.status_code == 409 and "名额" in r.json()["detail"]


def test_公开后报名表锁定(client, admin, db):
    act_id, _ids, q = _create_screened(client, admin, db)
    r = client.put(f"/api/staff/activities/{act_id}/form", headers=admin,
                   json={"questions": [{"kind": "text", "title": "新题"}]})
    assert r.status_code == 409
    assert client.get(f"/api/staff/activities/{act_id}/form",
                      headers=admin).json()["editable"] is False


def test_报名表文件只有本人和管理者能下载(client, admin, db, take_student):
    act_id, ids, q = _create_screened(client, admin, db)
    t = login(client, take_student())
    r = client.post(f"/api/activities/{act_id}/apply/upload", headers=t,
                    files={"file": ("证明.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert r.status_code == 200, r.text
    att_id = r.json()["id"]
    r = client.post(f"/api/activities/{act_id}/apply", headers=t,
                    json={"shift_ids": ids, "answers": _answers(q, 证明材料=att_id)})
    assert r.status_code == 200, r.text
    lst = client.get(f"/api/staff/activities/{act_id}/applications", headers=admin).json()
    file_answer = lst["applications"][0]["answers"][3]["value"]
    assert file_answer["attachment_id"] == att_id
    assert client.get(f"/api/materials/{att_id}", headers=admin).status_code == 200
    assert client.get(f"/api/materials/{att_id}", headers=t).status_code == 200
    other = login(client, take_student())
    assert client.get(f"/api/materials/{att_id}", headers=other).status_code == 404


def test_复制活动带报名表不带联络群(client, admin, db):
    act_id, _ids, q = _create_screened(client, admin, db, status="draft")
    r = client.post(f"/api/staff/activities/{act_id}/copy", headers=admin,
                    json={"shift_days": 3, "title": "副本", "contact_group_qr": "data:new"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    d = client.get(f"/api/activities/{new_id}", headers=admin).json()
    assert d["recruit_mode"] == "screened" and d["contact_group_qr"] == "data:new"
    assert [x["title"] for x in d["application"]["questions"]] == [x["title"] for x in q]


def test_报名成功即弹联络群(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    t = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll", headers=t,
                    json={"position_id": shift.positions[0].id})
    assert r.status_code == 200, r.text
    assert r.json()["contact_group_qr"] == "x"
    rows = client.get("/api/me/enrollments", headers=t).json()
    row = next(x for x in rows if x["activity"]["id"] == act.id)
    assert row["contact_group_qr"] == "x" and not row["group_qr_pending"]
    # 志愿者看的活动详情里没有二维码
    assert "contact_group_qr" not in client.get(f"/api/activities/{act.id}", headers=t).json()
