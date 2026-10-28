"""留痕的对外可见性。

签到码、他人学号、信用分处分都在留痕里，默认公开等于「忘了标就泄露」。
这里把默认口径和几条关键项钉住。
"""
from __future__ import annotations

from conftest import editing, login


def _visible_summaries(client, headers, activity_id):
    r = client.get(f"/api/activities/{activity_id}", headers=headers)
    assert r.status_code == 200, r.text
    # 字段缺了的话「一条都看不到」就成了空转，这里直接挂
    return [l["summary"] for l in r.json()["change_logs"]]


def test_签到码不出现在志愿者可见的留痕里(client, admin, db, enrolled_activity,
                                        take_student):
    """签到码整串进了志愿者可见的变更记录，学生不到现场就能签到。"""
    act, shift, _u = enrolled_activity
    r = client.post(f"/api/staff/shifts/{shift.id}/checkin-tasks",
                    headers=admin, json={"mode": "group"})
    assert r.status_code == 200
    token = r.json()["token"]

    student = login(client, take_student())
    for summary in _visible_summaries(client, student, act.id):
        assert token not in summary, f"签到码泄露：{summary}"

    # 管理者自己在审计中心仍然查得到
    logs = client.get(f"/api/staff/audit-logs?activity_id={act.id}",
                      headers=admin).json()["items"]
    assert any(token in l["summary"] for l in logs)


def test_补录不把他人学号暴露给志愿者(client, admin, db, enrolled_activity,
                                    take_student):
    act, shift, _u = enrolled_activity
    walkin = take_student()
    r = client.post(f"/api/staff/shifts/{shift.id}/add-walkin", headers=admin,
                    json={"student_id": walkin,
                          "position_id": shift.positions[0].id})
    assert r.status_code == 200

    student = login(client, take_student())
    for summary in _visible_summaries(client, student, act.id):
        assert walkin not in summary, f"学号泄露：{summary}"


def test_结构性变更仍然对志愿者公开(client, admin, db, enrolled_activity,
                                  take_student):
    """「谁动了我的岗位」是设计目标，不能因为收紧默认值把它一起关掉。"""
    act, shift, _u = enrolled_activity
    with editing(client, admin, act.id):
        assert client.patch(f"/api/staff/shifts/{shift.id}", headers=admin,
                            json={"note": "集合点改到北门"}).status_code == 200

    student = login(client, take_student())
    summaries = _visible_summaries(client, student, act.id)
    assert any("修改场次" in s for s in summaries), summaries


def test_默认不公开(db):
    """新写的留痕忘了标 visible，应当是内部可见而不是全体可见。"""
    from backend.audit import log
    entry = log(db, actor=None, action="test", target_type="x",
                summary="随手记一条")
    assert entry.visible is False


# ======================= 发布之前的改动不对学生公开 =======================

def test_筹备期的改动不进志愿者的变更记录(client, admin, db, take_student):
    """草稿/待审批期间建场次、改名额，学生从来没见过，不该摆给他看。

    「变更记录」的意思是「你报名之后发生了什么变化」，得从公开那一刻算起。
    """
    from datetime import timedelta

    from backend.models import Activity, now
    from conftest import POSITION_TEXT

    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"筹备期留痕 {now():%H%M%S%f}",
                          "organizer": "校青协", "intro": "x",
                          "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=2)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "上午场", "mode": "pick",
                          "periods": [{"start_time": (now() + timedelta(days=5)).isoformat(),
                          "end_time": (now() + timedelta(days=5, hours=3)).isoformat()}]})
    assert r.status_code == 200, r.text
    gid = r.json()["id"]
    r = client.post(f"/api/staff/shifts/{gid}/positions", headers=admin,
                    json={"name": "引导岗", "quota": 3, **POSITION_TEXT})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert client.patch(f"/api/staff/positions/{pid}", headers=admin,
                        json={"quota": 5}).status_code == 200

    # 发布之前：这些留痕都在，但一条都不该对学生可见
    assert _visible_summaries(client, admin, aid) == []

    act = db.get(Activity, aid)
    act.status = "published"
    db.commit()
    t = login(client, take_student())
    assert _visible_summaries(client, t, aid) == []

    # 发布之后的改动才进得去
    assert client.post(f"/api/staff/activities/{aid}/edit-lock",
                       headers=admin).status_code == 200
    assert client.patch(f"/api/staff/positions/{pid}", headers=admin,
                        json={"quota": 8}).status_code == 200
    got = _visible_summaries(client, t, aid)
    assert len(got) == 1 and "修改岗位「引导岗」" in got[0]


def test_审批中的改动同样不可见(client, admin, db, take_student):
    """待审批、待发布都还没公开，跟草稿是一回事。"""
    from datetime import timedelta

    from backend.models import Activity, now

    r = client.post("/api/staff/activities", headers=admin,
                    json={"contact_group_qr": "x", "title": f"审批期留痕 {now():%H%M%S%f}",
                          "organizer": "校青协", "intro": "x",
                          "location": "世纪馆",
                          "contacts": [{"name": "王", "phone": "13900000000"}],
                          "reg_start": (now() - timedelta(hours=1)).isoformat(),
                          "reg_end": (now() + timedelta(days=2)).isoformat()})
    assert r.status_code == 200, r.text
    aid = r.json()["id"]
    for status in ("pending", "approved"):
        db.get(Activity, aid).status = status
        db.commit()
        r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                         json={"location": f"改到 {status}"})
        assert r.status_code == 200, (status, r.text)
        assert _visible_summaries(client, admin, aid) == [], status


def test_每个审计动作都有中文名():
    """审计页按 ACTION_LABELS 显示动作，表里漏了的就显示成英文（如 expire）。

    写死在代码里的动作（action="..."）和人员变动流水的动作（ROSTER_ACTIONS）
    都要在表里。
    """
    import re
    from pathlib import Path

    from backend.audit import ACTION_LABELS
    from backend.enroll_logic import ROSTER_ACTIONS

    root = Path(__file__).resolve().parent.parent / "backend"
    used = set(ROSTER_ACTIONS)
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for call in re.finditer(r"audit_log\((.*?)\)", text, re.S):
            used.update(re.findall(r'action="([a-z_]+)"', call.group(1)))
    assert "expire" in used
    assert sorted(used - set(ACTION_LABELS)) == []
    # 变更记录和审计日志上，志愿者的报名、退选这些统一标「人员变动」；
    # 具体是哪一种写在说明里。志愿者管理页的人员变动流水另有各自动作名。
    assert ACTION_LABELS["expire"] == "人员变动"
    assert ROSTER_ACTIONS["expire"] == "候补转正超时未确认"
    # 审批驳回曾经没有中文名，页面上直接显示英文 reject
    assert ACTION_LABELS["reject"] == "审批驳回"
