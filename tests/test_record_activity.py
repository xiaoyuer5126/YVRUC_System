"""不公开招募（补记录）活动：线下办完、平台补记时长。

创建（不要联络群、报名时间，要活动时间）→ 提交立项审批 → 通过即「已结束」
→ 导入「学号 + 时长」名单（找不到的只提醒、重复/非法/老师/已在名单跳过）
→ 单人改删 → 核准 → 志愿者看得到、能申诉 → 提交时长审核 → 外联确认 → 已完成。
全程不上广场、详情对学生 404、不能报名；导出含导入的人；提示分导入即参与。
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO

import pytest
from openpyxl import Workbook, load_workbook

from backend import config, scoring, scheduled
from backend.models import Activity, AuditLog, HoursRecord, HoursRequest, Notification, User, now, uid
from conftest import (ADMIN, INITIATOR, dl_url, import_roster,
                      import_roster_file, login, student_row, upload_proposal,
                      upload_stamped)

TEACHER = INITIATOR          # 李慕清是老师类型


def _create(client, headers, **extra) -> str:
    body = {"title": f"补记录 {uid('t')}", "organizer": "文学院青协",
            "recruit_mode": "record", "intro": "线下已办", "location": "世纪馆",
            "contacts": [{"name": "王", "phone": "13900000000"}],
            "held_start": (now() - timedelta(days=3, hours=4)).isoformat(),
            "held_end": (now() - timedelta(days=3)).isoformat(), **extra}
    r = client.post("/api/staff/activities", headers=headers, json=body)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _submit_and_approve(client, headers, aid):
    depts = [d for d in client.get("/api/staff/departments", headers=headers).json()
             if d["has_head"]]
    upload_proposal(client, headers, aid)
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=headers,
                    json={"department_id": depts[0]["id"]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/activities/{aid}/decide", headers=headers,
                    json={"approve": True})
    assert r.status_code == 200, r.text
    return r.json()


def _import(client, headers, aid, text):
    return import_roster(client, headers, aid, text)


@pytest.fixture
def approved(client, admin):
    """审批通过、等着导入名单的补记录活动。"""
    aid = _create(client, admin)
    assert _submit_and_approve(client, admin, aid)["status"] == "finished"
    return aid


# ======================= 创建与审批 =======================

def test_创建不要联络群和报名时间_要活动时间(client, admin, db):
    base = {"title": f"补记录 {uid('t')}", "organizer": "x", "recruit_mode": "record",
            "intro": "x", "location": "x",
            "contacts": [{"name": "王", "phone": "13900000000"}]}
    r = client.post("/api/staff/activities", headers=admin, json=base)
    assert r.status_code == 400 and "活动开始与结束时间" in r.json()["detail"]
    r = client.post("/api/staff/activities", headers=admin, json={
        **base, "held_start": now().isoformat(),
        "held_end": (now() - timedelta(hours=1)).isoformat()})
    assert r.status_code == 400 and "晚于开始" in r.json()["detail"]

    # 招募相关的字段传了也不收
    aid = _create(client, admin, contact_group_qr="x",
                  reg_start=now().isoformat(), recruit_campuses=["中关村校区"])
    act = db.get(Activity, aid)
    assert act.recruit_mode == "record" and act.is_record()
    assert act.contact_group_qr == "" and act.reg_start is None
    assert act.recruit_campuses == [] and act.held_end is not None
    d = client.get(f"/api/activities/{aid}", headers=admin).json()
    assert d["record_only"] and d["recruit_label"] == "不公开招募（补记录）"
    assert d["time_text"] != "待定"

    # 不建场次、不设问卷、不发布
    r = client.post(f"/api/staff/activities/{aid}/shifts", headers=admin,
                    json={"name": "x", "start_time": now().isoformat(),
                          "end_time": (now() + timedelta(hours=1)).isoformat()})
    assert r.status_code == 409 and "不设场次" in r.json()["detail"]
    r = client.put(f"/api/staff/activities/{aid}/form", headers=admin,
                   json={"questions": []})
    assert r.status_code == 409
    assert client.post(f"/api/staff/activities/{aid}/publish",
                       headers=admin).status_code == 409
    # 类型常量下发给前端
    modes = client.get("/api/auth/constants", headers=admin).json()["recruit_modes"]
    assert "record" in modes


def test_已有场次的草稿不能改成补记录(client, admin):
    from conftest import make_draft
    aid = make_draft(client, admin, proposal=False)
    r = client.patch(f"/api/staff/activities/{aid}", headers=admin,
                     json={"recruit_mode": "record"})
    assert r.status_code == 409 and "场次" in r.json()["detail"]


def test_审批前不能导入_通过即已结束(client, admin, db, take_student):
    sid = take_student()
    aid = _create(client, admin)
    r = _import(client, admin, aid, f"{sid} 2")
    assert r.status_code == 409 and "审批通过后" in r.json()["detail"]
    # 提交审批照常：对外联络部要立项书
    depts = [d for d in client.get("/api/staff/departments", headers=admin).json()
             if d["has_head"]]
    assert client.get(f"/api/staff/activities/{aid}/submit-check",
                      headers=admin).status_code == 200
    r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                    json={"department_id": depts[0]["id"]})
    if depts[0]["name"] == "对外联络部":
        assert r.status_code == 400 and "立项书" in r.json()["detail"]
        upload_proposal(client, admin, aid)
        r = client.post(f"/api/staff/activities/{aid}/submit", headers=admin,
                        json={"department_id": depts[0]["id"]})
    assert r.status_code == 200, r.text
    assert _import(client, admin, aid, f"{sid} 2").status_code == 409
    appr = client.get("/api/staff/approvals", headers=admin).json()
    assert next(x for x in appr if x["id"] == aid)["record_only"] is True
    r = client.post(f"/api/staff/activities/{aid}/decide", headers=admin,
                    json={"approve": True})
    assert r.status_code == 200 and r.json()["status"] == "finished"
    db.expire_all()
    assert db.get(Activity, aid).status == "finished"
    assert _import(client, admin, aid, f"{sid} 2").status_code == 200


# ======================= 导入 =======================

def test_导入名单_成功与跳过清单(client, admin, db, approved, take_student):
    a, b, c = take_student(), take_student(), take_student()
    c2, c3, c4 = take_student(), take_student(), take_student()
    text = "\n".join([
        "学号\t时长\t备注",           # 表头：不当数据
        f"{a}\t2.5",
        f"{b}, 3, 场外支援 半天",
        "",
        "9999999999 2",               # 平台里没有
        f"{a} 4",                     # 同一批重复
        f"{c} abc",                   # 不是数字
        f"{c2} 0.3",                  # 不是 0.5 的倍数
        f"{c3} 0",
        f"{TEACHER} 2",               # 老师不参与志愿
        f"{c4} 36.5",                 # 不设上限：照导，但标出来请核对
    ])
    r = _import(client, admin, approved, text)
    assert r.status_code == 200, r.text
    d = r.json()
    assert {(x["student_id"], x["hours"]) for x in d["added"]} == \
        {(a, 2.5), (b, 3.0), (c4, 36.5)}
    warned = {x["student_id"]: x["warning"] for x in d["added"]}
    assert "超过 24 小时" in warned[c4] and not warned[a]
    assert "超过 24 小时请核对" in d["message"]
    assert d["not_found"] == [{"line": 5, "student_id": "9999999999"}]
    reasons = {(x["line"], x["student_id"]): x["reason"] for x in d["skipped"]}
    assert "重复" in reasons[(6, a)] and "第 2 行" in reasons[(6, a)]
    assert "不是数字" in reasons[(7, c)]
    assert "0.5" in reasons[(8, c2)]
    assert "大于 0" in reasons[(9, c3)]
    assert "老师" in reasons[(10, TEACHER)]
    assert len(d["skipped"]) == 5
    # 找不到的学号不建档
    from backend.models import User
    assert db.query(User).filter(User.student_id == "9999999999").first() is None

    rows = client.get(f"/api/staff/activities/{approved}/record-roster",
                      headers=admin).json()
    assert rows["editable"] and rows["total_hours"] == 42
    # 名单页上的活动时间和其他页面的「活动时间」同一个写法
    assert rows["held_text"] == db.get(Activity, approved).time_text() != "待定"
    by_sid = {x["user"]["student_id"]: x for x in rows["items"]}
    assert by_sid[b]["note"] == "场外支援 半天"
    db.expire_all()
    rec = db.query(HoursRecord).filter(HoursRecord.activity_id == approved,
                                       HoursRecord.user_id == student_row(db, b).id).one()
    assert rec.source == "import" and [s.kind for s in rec.segments] == ["import"]

    # 再导一次：新人追加，已在名单的按新时长覆盖（列出旧值→新值），没变的不动
    r = _import(client, admin, approved, f"{a} 5\n{c} 1.5\n{b} 3")
    d = r.json()
    assert [x["student_id"] for x in d["added"]] == [c]
    assert [(x["student_id"], x["old_hours"], x["hours"]) for x in d["overwritten"]] \
        == [(a, 2.5, 5.0)]
    assert d["unchanged"] == 1 and not d["skipped"]
    db.expire_all()
    rec = db.query(HoursRecord).filter(HoursRecord.activity_id == approved,
                                       HoursRecord.user_id == student_row(db, a).id).one()
    assert rec.hours == 5 and [s.hours for s in rec.segments] == [5]

    # 留痕
    logs = db.query(AuditLog).filter(AuditLog.activity_id == approved,
                                     AuditLog.action == "roster_import").all()
    assert len(logs) == 2 and "找不到" in logs[0].summary + logs[1].summary
    assert any("覆盖 1 人" in l.summary for l in logs)
    hours_of = [{(c["student_id"], c["old_hours"], c["new_hours"])
                 for c in l.changes if c["field"] == "hours"} for l in logs]
    assert {(a, None, 2.5), (b, None, 3.0), (c4, None, 36.5)} in hours_of
    assert {(c, None, 1.5), (a, 2.5, 5.0)} in hours_of


def test_上传xlsx与csv(client, admin, approved, take_student):
    a, b = take_student(), take_student()
    wb = Workbook()
    ws = wb.active
    ws.append(["学号", "时长", "备注"])
    ws.append([int(a), 2, "上午"])          # Excel 里学号常被存成数字
    ws.append(["8888888888", 1])
    buf = BytesIO()
    wb.save(buf)
    r = import_roster_file(client, admin, approved, (
        "名单.xlsx", buf.getvalue(),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"))
    assert r.status_code == 200, r.text
    assert [x["student_id"] for x in r.json()["added"]] == [a]
    assert r.json()["not_found"][0]["student_id"] == "8888888888"

    csv_bytes = f"学号,时长\n{b},1.5\n".encode("gb18030")
    r = import_roster_file(client, admin, approved,
                           ("名单.csv", csv_bytes, "text/csv"))
    assert r.status_code == 200 and r.json()["added"][0]["hours"] == 1.5
    r = import_roster_file(client, admin, approved,
                           ("名单.pdf", b"%PDF", "application/pdf"))
    assert r.status_code == 400


def test_单人修改与删除(client, admin, db, approved, take_student):
    a, b = take_student(), take_student()
    _import(client, admin, approved, f"{a} 2\n{b} 3")
    items = client.get(f"/api/staff/activities/{approved}/record-roster",
                       headers=admin).json()["items"]
    ra = next(x for x in items if x["user"]["student_id"] == a)
    rb = next(x for x in items if x["user"]["student_id"] == b)
    url = f"/api/staff/activities/{approved}/record-roster"
    r = client.patch(f"{url}/{ra['id']}", headers=admin,
                     json={"hours": 4.5, "note": "补上下午"})
    assert r.status_code == 200 and r.json()["hours"] == 4.5
    assert client.patch(f"{url}/{ra['id']}", headers=admin,
                        json={"hours": 1.2}).status_code == 400
    assert client.delete(f"{url}/{rb['id']}", headers=admin).status_code == 200
    # 别人（不管这个活动的发起方）动不了
    other = login(client, INITIATOR)
    assert client.patch(f"{url}/{ra['id']}", headers=other,
                        json={"hours": 1}).status_code == 403
    items = client.get(url, headers=admin).json()["items"]
    assert [(x["user"]["student_id"], x["hours"], x["note"]) for x in items] == \
        [(a, 4.5, "补上下午")]
    db.expire_all()
    summaries = [l.summary for l in db.query(AuditLog)
                 .filter(AuditLog.activity_id == approved,
                         AuditLog.action == "roster_import").all()]
    assert any("修改补记录名单" in s and "2h → 4.5h" in s for s in summaries)
    assert any("从补记录名单删除" in s for s in summaries)


def test_普通活动不走名单导入(client, admin, open_group):
    act, _g = open_group(3)
    r = _import(client, admin, act.id, "2025001 2")
    assert r.status_code == 409


# ======================= 可见性 =======================

def test_广场与详情对学生不可见_不能报名(client, admin, db, approved, take_student):
    sid = take_student()
    _import(client, admin, approved, f"{sid} 2")
    stu = login(client, sid)
    title = db.get(Activity, approved).title
    r = client.get("/api/activities", headers=stu, params={"q": title})
    assert r.status_code == 200 and r.json()["total"] == 0
    # 管理者自己在广场上也不列（广场是招募用的）
    assert client.get("/api/activities", headers=admin,
                      params={"q": title}).json()["total"] == 0
    assert client.get(f"/api/activities/{approved}", headers=stu).status_code == 404
    r = client.post(f"/api/activities/{approved}/shifts/nope/enroll", headers=stu,
                    json={})
    assert r.status_code == 404
    r = client.post(f"/api/activities/{approved}/apply", headers=stu,
                    json={"position_ids": [], "answers": {}})
    assert r.status_code in (404, 409)
    # 管理者照常看得到
    assert client.get(f"/api/activities/{approved}", headers=admin).json()["manager"]


# ======================= 结账全流程 =======================

def test_核准_申诉_提交审核_外联确认_完成(client, admin, db, approved, take_student):
    a, b = take_student(), take_student()
    _import(client, admin, approved, f"{a} 2\n{b} 3 引导")
    hours = client.get(f"/api/staff/activities/{approved}/hours", headers=admin).json()
    assert sorted((x["user"]["student_id"], x["hours"], x["confirmed"])
                  for x in hours["items"]) == sorted([(a, 2.0, True), (b, 3.0, True)])
    # 签到口径的确认、手动加减对它关着
    assert client.post(f"/api/staff/activities/{approved}/hours/confirm",
                       headers=admin, json={}).status_code == 409
    assert client.post(f"/api/staff/activities/{approved}/hours/manual",
                       headers=admin, json={"items": [
                           {"student_id": a, "hours": 1, "note": "x"}]}).status_code == 409

    # 核准 → 志愿者在「我的志愿时长」看得到、能申诉
    deadline = (now() + timedelta(days=1)).isoformat()
    r = client.post(f"/api/staff/activities/{approved}/hours/approve", headers=admin,
                    json={"appeal_deadline": deadline})
    assert r.status_code == 200 and r.json()["count"] == 2, r.text
    stu = login(client, a)
    mine = client.get("/api/me/hours", headers=stu).json()
    rec = next(x for x in mine["records"] if x["activity_id"] == approved)
    assert rec["hours"] == 2 and rec["record_only"] and rec["can_appeal"]
    assert rec["parts"][0]["kind"] == "import" and rec["parts"][0]["time"]
    r = client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu,
                    json={"claimed_hours": 3, "reason": "实际服务了三个小时"})
    assert r.status_code == 200, r.text
    # 申诉没处理完：再导一次不覆盖他，列出来提醒；别人照常覆盖
    r = _import(client, admin, approved, f"{a} 6\n{b} 3.5 引导")
    d = r.json()
    assert [x["student_id"] for x in d["overwritten"]] == [b]
    assert d["reapproval_required"]
    assert d["skipped"][0]["student_id"] == a and "申诉" in d["skipped"][0]["reason"]
    db.expire_all()
    assert db.get(Activity, approved).hours_approved_at is None
    assert db.get(Activity, approved).appeal_deadline is None
    assert _import(client, admin, approved, f"{b} 3 引导").json()["overwritten"]
    # 有申诉的记录不能删
    assert client.delete(f"/api/staff/activities/{approved}/record-roster/{rec['id']}",
                         headers=admin).status_code == 409
    appeal = client.get(f"/api/staff/activities/{approved}/hours-appeals",
                        headers=admin).json()[0]
    r = client.post(f"/api/staff/hours-appeals/{appeal['id']}/resolve", headers=admin,
                    json={"accept": True})
    assert r.status_code == 200 and r.json()["hours"] == 3
    items = client.get(f"/api/staff/activities/{approved}/record-roster",
                       headers=admin).json()["items"]
    assert next(x for x in items if x["user"]["student_id"] == a)["hours"] == 3
    hours = client.get(f"/api/staff/activities/{approved}/hours", headers=admin).json()
    assert next(x for x in hours["items"] if x["user"]["student_id"] == a)["hours"] == 3

    # 提交时长审核：对外活动要盖章立项书，还要志愿北京名称
    act = db.get(Activity, approved)
    url = f"/api/staff/activities/{approved}/hours/lock-request"
    names = {"vb_names": ["补记录志愿服务"]}
    from backend import liaison
    r = client.post(url, headers=admin, json=names)
    assert r.status_code == 409 and "先核准" in r.json()["detail"]
    r = client.post(f"/api/staff/activities/{approved}/hours/approve",
                    headers=admin, json={"appeal_deadline": (
                        now() + timedelta(days=1)).isoformat()})
    # 只通知申诉过的 a；b 被覆盖成 3.5 又改回 3，和上次通知他的数一样
    assert r.status_code == 200 and r.json()["count"] == 1, r.text
    if liaison.is_external(db, act):
        assert client.post(url, headers=admin, json=names).status_code == 400
        assert upload_stamped(client, admin, approved).status_code == 200
    r = client.post(url, headers=admin, json=names)
    assert r.status_code == 200, r.text
    # 审核期间名单冻结
    assert _import(client, admin, approved, f"{take_student()} 1").status_code == 409
    r = _import(client, admin, approved, f"{b} 4")          # 覆盖同样被拒
    assert r.status_code == 409 and "审批" in r.json()["detail"]
    req = next(x for x in client.get("/api/staff/hours-requests", headers=admin).json()
               if x["activity_id"] == approved)
    r = client.post(f"/api/staff/hours-requests/{req['id']}/decide", headers=admin,
                    json={"approve": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, approved).status == "vb_pending"
    row = next(x for x in client.get("/api/staff/vb-confirms", headers=admin).json()
               if x["activity_id"] == approved)
    r = client.post(f"/api/staff/vb-confirms/{row['id']}/decide", headers=admin,
                    json={"approve": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Activity, approved).status == "completed"
    # 锁定之后整批导入被拒，也不能单独改
    r = _import(client, admin, approved, f"{a} 8")
    assert r.status_code == 409 and "锁定" in r.json()["detail"]
    r = client.patch(f"/api/staff/activities/{approved}/record-roster/{rec['id']}",
                     headers=admin, json={"hours": 1})
    assert r.status_code == 409
    mine = client.get("/api/me/hours", headers=stu).json()
    rec = next(x for x in mine["records"] if x["activity_id"] == approved)
    assert rec["locked"] and rec["hours"] == 3

    # 导出：姓名、学号、身份证号、时长、备注
    r = client.get(dl_url(client, admin, f"/api/staff/activities/{approved}/hours.xlsx"))
    assert r.status_code == 200
    rows = list(load_workbook(BytesIO(r.content)).active.values)
    assert rows[0] == ("姓名", "学号", "身份证号", "时长", "备注")
    by_sid = {x[1]: x for x in rows[1:]}
    assert by_sid[a][3] == 3 and by_sid[a][2]
    assert by_sid[b][3] == 3 and "引导" in by_sid[b][4]


@pytest.mark.parametrize("change", ["import", "edit", "delete"])
def test_核准后改名单撤销旧申诉截止时间(client, admin, db, approved,
                               take_student, change):
    a, b = take_student(), take_student()
    assert _import(client, admin, approved, f"{a} 2\n{b} 3").status_code == 200
    url = f"/api/staff/activities/{approved}"
    r = client.post(f"{url}/hours/approve", headers=admin, json={
        "appeal_deadline": (now() + timedelta(days=1)).isoformat()})
    assert r.status_code == 200 and r.json()["count"] == 2, r.text
    rows = client.get(f"{url}/record-roster", headers=admin).json()["items"]
    by_sid = {x["user"]["student_id"]: x["id"] for x in rows}
    # b 在名单变动前就确认过：改的不是他，这句确认要留着
    assert client.post(f"/api/me/hours/{by_sid[b]}/confirm",
                       headers=login(client, b), json={}).status_code == 200
    # 模拟截止时间已到，而定时任务尚未扫到这场活动。
    db.expire_all()
    db.get(Activity, approved).appeal_deadline = now() - timedelta(minutes=1)
    db.commit()
    if change == "import":
        c = take_student()
        r = _import(client, admin, approved, f"{c} 1")
    elif change == "edit":
        r = client.patch(f"{url}/record-roster/{by_sid[a]}", headers=admin,
                         json={"hours": 2.5, "note": "更正"})
    else:
        r = client.delete(f"{url}/record-roster/{by_sid[a]}", headers=admin)
    assert r.status_code == 200 and r.json()["reapproval_required"], r.text
    db.expire_all()
    act = db.get(Activity, approved)
    assert act.hours_approved_at is None and act.appeal_deadline is None
    kept = db.get(HoursRecord, by_sid[b])
    assert kept.approved_hours == 3 and kept.confirmed_at is not None
    scheduled.submit_lock_when_due(db)
    assert db.query(HoursRequest).filter_by(activity_id=approved).count() == 0
    r = client.post(f"{url}/hours/lock-request", headers=admin,
                    json={"vb_names": ["补记录志愿服务"]})
    assert r.status_code == 409 and "先核准" in r.json()["detail"]
    if change == "delete":
        user = db.query(User).filter_by(student_id=a).one()
        assert db.query(Notification).filter(
            Notification.user_id == user.id,
            Notification.title.like("志愿时长核准已撤销%")).count() == 1
    r = client.post(f"{url}/hours/approve", headers=admin, json={
        "appeal_deadline": (now() + timedelta(days=1)).isoformat()})
    # 只通知新增的 c / 改了时长的 a；删掉 a 时谁也不用再通知
    expected = {"import": 1, "edit": 1, "delete": 0}[change]
    assert r.status_code == 200 and r.json()["count"] == expected, r.text
    db.expire_all()
    assert db.get(HoursRecord, by_sid[b]).confirmed_at is not None


def test_改名单时这一轮申诉过的人_重新核准照样通知(client, admin, db, approved,
                                          take_student):
    a, b, c = take_student(), take_student(), take_student()
    _import(client, admin, approved, f"{a} 2\n{b} 3")
    url = f"/api/staff/activities/{approved}"
    client.post(f"{url}/hours/approve", headers=admin, json={
        "appeal_deadline": (now() + timedelta(days=1)).isoformat()})
    stu = login(client, b)
    rec = next(x for x in client.get("/api/me/hours", headers=stu).json()["records"]
               if x["activity_id"] == approved)
    assert client.post(f"/api/me/hours/{rec['id']}/appeal", headers=stu, json={
        "claimed_hours": 4, "reason": "实际服务了四个小时"}).status_code == 200
    appeal = client.get(f"{url}/hours-appeals", headers=admin).json()[0]
    client.post(f"/api/staff/hours-appeals/{appeal['id']}/resolve", headers=admin,
                json={"accept": False, "reply": "按签到记录核对无误"})
    assert _import(client, admin, approved, f"{c} 1").json()["reapproval_required"]
    r = client.post(f"{url}/hours/approve", headers=admin, json={
        "appeal_deadline": (now() + timedelta(days=1)).isoformat()})
    # 新增的 c + 申诉被驳回、数值没变的 b；a 没变也没申诉，不打扰
    assert r.status_code == 200 and r.json()["count"] == 2, r.text


# ======================= 提示分 / 档案 / 违规 =======================

def test_提示分_导入即参与_有违规不加(client, admin, db, approved, take_student):
    a, b = take_student(), take_student()
    _import(client, admin, approved, f"{a} 2\n{b} 2")
    ua, ub = student_row(db, a), student_row(db, b)
    # 满分时加分被上限吞掉：先造一条早先的违规把分压下来，才看得见加分
    from backend.models import ViolationRecord
    for u in (ua, ub):
        db.add(ViolationRecord(id=uid("vio"), user_id=u.id, activity_id="old",
                               activity_title="更早的活动", kind="other",
                               created_at=now() - timedelta(days=30)))
    db.commit()
    base = config.SCORE_BASE - config.VIOLATION_WEIGHTS["other"]
    da = scoring.detail(db, ua)
    assert da["score"] == min(config.SCORE_BASE, base + config.SCORE_PER_ACTIVITY)
    assert [x["label"] for x in da["items"]][-1] == "顺利参与"

    # 名单里的人可以登记违规；登记了就不加分
    r = client.post("/api/staff/violations", headers=admin, json={
        "user_id": ub.id, "activity_id": approved, "kind": "late",
        "detail": "迟到半小时"})
    assert r.status_code == 200, r.text
    db.expire_all()
    labels = [x["label"] for x in scoring.detail(db, ub)["items"]]
    assert "顺利参与" not in labels

    # 档案里列出补记录活动
    r = client.get(f"/api/staff/activities/{approved}/volunteers/{ua.id}/archive",
                   headers=admin)
    assert r.status_code == 200, r.text
    hist = [h for h in r.json()["history"] if h["activity_id"] == approved]
    assert hist and hist[0]["status"] == "recorded" and hist[0]["checked_in"]


def test_活动还没结束不加分(client, admin, db, take_student):
    aid = _create(client, admin,
                  held_start=(now() - timedelta(hours=1)).isoformat(),
                  held_end=(now() + timedelta(days=1)).isoformat())
    _submit_and_approve(client, admin, aid)
    sid = take_student()
    assert _import(client, admin, aid, f"{sid} 2").status_code == 200
    assert "顺利参与" not in [x["label"] for x in
                             scoring.detail(db, student_row(db, sid))["items"]]
