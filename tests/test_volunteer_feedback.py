"""志愿者反馈：提交、谁能处理、匿名不泄露身份、通知对象、48 小时催办、
值班日报、回复与模板、限流、截图权限、关联活动。平台问题见 test_feedback.py。"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

from backend import config, scheduled
from backend.models import AuditLog, Department, Feedback, Notification, now
from conftest import (ADMIN, INITIATOR, LIAISON_CORE, LIAISON_HEAD, LIAISON_MEMBER,
                      QINGXIE, dl_url, inbox, login, make_enrolled_activity,
                      student_row)

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _submit(client, headers, content="活动现场没有饮用水", kind="issue", *,
            anonymous=False, activity_id="", contact="", files=None):
    data = {"content": content, "category": "volunteer", "kind": kind,
            "contact": contact, "activity_id": activity_id}
    if anonymous:
        data["anonymous"] = "true"
    return client.post("/api/feedback", headers=headers, data=data,
                       files=files or [])


def _items(client, headers, query=""):
    r = client.get(f"/api/staff/volunteer-feedback?size=100{query}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _notes_for(db, student_id, fid, title_prefix=""):
    db.expire_all()
    u = student_row(db, student_id)
    rows = (db.query(Notification).filter(Notification.user_id == u.id).all())
    return [n for n in rows if (n.data or {}).get("feedback_id") == fid
            and n.title.startswith(title_prefix)]


# ======================= 提交 =======================

def test_意见类型必选_内容必填(client, take_student):
    me = login(client, take_student())
    assert _submit(client, me, kind="").status_code == 400
    assert _submit(client, me, kind="乱填").status_code == 400
    assert _submit(client, me, content="  ").status_code == 400
    too_long = "x" * (config.FEEDBACK_CONTACT_MAX + 1)
    assert _submit(client, me, contact=too_long).status_code == 400
    assert client.get("/api/feedback/mine", headers=me).json() == []


def test_提交成功本人看得到类型与回复(client, take_student):
    me = login(client, take_student())
    r = _submit(client, me, "能不能多办周末的活动", kind="suggest", contact="微信 abc")
    assert r.status_code == 200, r.text
    fb = r.json()
    assert fb["category"] == "volunteer" and fb["status"] == "open"
    assert fb["kind_label"] == "提出对于青协工作的改进建议或想法"
    mine = client.get("/api/feedback/mine", headers=me).json()
    assert mine[0]["id"] == fb["id"] and mine[0]["contact"] == "微信 abc"


def test_平台与志愿共用每小时限额(client, take_student):
    me = login(client, take_student())
    for i in range(config.FEEDBACK_PER_HOUR - 1):
        assert _submit(client, me, f"志愿 {i}").status_code == 200
    assert client.post("/api/feedback", headers=me,
                       data={"content": "平台问题"}).status_code == 200
    assert _submit(client, me, "再来一条").status_code == 429


# ======================= 可见范围 =======================

def test_只有外联部骨干部长和管理员能看(client, admin, take_student, liaison):
    me = login(client, take_student())
    fid = _submit(client, me).json()["id"]
    pid = client.post("/api/feedback", headers=me,
                      data={"content": "平台的问题"}).json()["id"]

    for sid in (LIAISON_HEAD, LIAISON_CORE):
        ids = [x["id"] for x in _items(client, login(client, sid))]
        assert fid in ids and pid not in ids
    # 管理员、会长团（张远航不在外联部）也能看
    for h in (admin, login(client, QINGXIE)):
        ids = [x["id"] for x in _items(client, h)]
        assert fid in ids and pid not in ids

    # 部门成员、活动发起方、普通学生都不行
    for h in (login(client, LIAISON_MEMBER), login(client, INITIATOR), me):
        assert client.get("/api/staff/volunteer-feedback", headers=h).status_code == 403
        assert client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=h,
                           json={"reply": "x"}).status_code == 403
    # 管理员切到别的身份也不行
    assert client.get("/api/staff/volunteer-feedback",
                      headers={**admin, "X-Identity": "initiator"}).status_code == 403
    # 平台问题列表不混进志愿反馈，处理接口也互不通用
    plat = [x["id"] for x in client.get("/api/staff/feedback?size=100",
                                        headers=admin).json()["items"]]
    assert pid in plat and fid not in plat
    assert client.post(f"/api/staff/feedback/{fid}/handle", headers=admin,
                       json={"reply": "x"}).status_code == 404
    assert client.post(f"/api/staff/volunteer-feedback/{pid}/reply", headers=admin,
                       json={"reply": "x"}).status_code == 404


def test_实名的处理方看得到提交人(client, take_student, liaison):
    sid = take_student()
    fid = _submit(client, login(client, sid)).json()["id"]
    item = [x for x in _items(client, login(client, LIAISON_HEAD)) if x["id"] == fid][0]
    assert item["user"]["student_id"] == sid and item["anonymous"] is False


def test_按状态类型活动筛选(client, admin, db, take_student, liaison):
    act, _shift, u = make_enrolled_activity(db, take_student)
    me = login(client, u.student_id)
    a = _submit(client, me, kind="ask", activity_id=act.id).json()["id"]
    b = _submit(client, me, kind="other").json()["id"]
    assert [x["id"] for x in _items(client, admin, f"&activity_id={act.id}")] == [a]
    by_kind = [x["id"] for x in _items(client, admin, "&kind=other")]
    assert b in by_kind and a not in by_kind
    assert client.post(f"/api/staff/volunteer-feedback/{a}/reply", headers=admin,
                       json={"reply": "已答复"}).status_code == 200
    opened = [x["id"] for x in _items(client, admin, "&status=open")]
    assert b in opened and a not in opened
    assert client.get("/api/staff/volunteer-feedback?kind=xx",
                      headers=admin).status_code == 400


# ======================= 匿名 =======================

def test_匿名时处理方接口不返回身份(client, admin, db, take_student, liaison):
    sid = take_student()
    me_row = student_row(db, sid)
    me = login(client, sid)
    r = _submit(client, me, "不想被知道是谁", anonymous=True,
                files=[("files", ("a.png", PNG, "image/png"))])
    assert r.status_code == 200, r.text
    fid = r.json()["id"]

    for h in (admin, login(client, LIAISON_HEAD), login(client, QINGXIE)):
        resp = client.get("/api/staff/volunteer-feedback?size=100", headers=h)
        item = [x for x in resp.json()["items"] if x["id"] == fid][0]
        assert item["anonymous"] is True and item["user"] is None
        raw = json.dumps(item, ensure_ascii=False)
        assert sid not in raw and me_row.name not in raw and me_row.id not in raw

    # 回复接口的返回同样不带
    r = client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=admin,
                    json={"reply": "收到"})
    raw = json.dumps(r.json(), ensure_ascii=False)
    assert r.json()["user"] is None and sid not in raw and me_row.name not in raw
    # 本人照样看得到自己的反馈和回复
    mine = client.get("/api/feedback/mine", headers=me).json()
    assert mine[0]["id"] == fid and mine[0]["reply"] == "收到"
    # 匿名提交不留审计（审计的操作人就是提交人，会长团看得到）
    db.expire_all()
    assert not (db.query(AuditLog)
                .filter(AuditLog.target_id == fid, AuditLog.actor_id == me_row.id)
                .count())


def test_匿名时通知和通知记录不露身份(client, admin, db, take_student, liaison):
    sid = take_student()
    me_row = student_row(db, sid)
    fid = _submit(client, login(client, sid), anonymous=True).json()["id"]

    notes = _notes_for(db, LIAISON_HEAD, fid, "新的志愿者反馈")
    assert len(notes) == 1
    assert "匿名志愿者" in notes[0].content
    assert sid not in notes[0].content and me_row.name not in notes[0].content
    assert notes[0].triggered_by is None

    client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=admin,
                json={"reply": "已转达"})
    assert len(_notes_for(db, sid, fid, "志愿者反馈已回复")) == 1

    # 管理端「通知记录」：回给他的那条不显示收件人，按学号也搜不出来；
    # 发给部长的那条触发人不是他
    from backend.routers import platform
    reply = _notes_for(db, sid, fid, "志愿者反馈已回复")[0]
    shown = platform._notify_out(reply, sid, me_row.name, None, None, {})
    assert shown["recipient_name"] == "匿名志愿者" and shown["recipient_sid"] == ""
    row = platform._notify_excel_row(reply, sid, me_row.name, None, None, {})
    assert sid not in row and me_row.name not in row
    new = _records(client, admin, person=LIAISON_HEAD)
    new = [x for x in new if x["title"].startswith("新的志愿者反馈")]
    assert new and all(x["trigger_sid"] != sid for x in new)
    found = _records(client, admin, person=sid)
    assert not [x for x in found if x["title"] == "志愿者反馈已回复"]
    # 导出也一样
    r = client.get(f"/api/staff/notifications.xlsx?person={sid}", headers=admin)
    assert r.status_code == 200


def _records(client, admin, **params):
    out, page = [], 1
    while page <= 50:
        r = client.get("/api/staff/notifications", headers=admin,
                       params={"size": 100, "page": page, **params})
        assert r.status_code == 200, r.text
        d = r.json()
        out += d["items"]
        if not d["items"] or len(out) >= d["total"]:
            break
        page += 1
    return out


# ======================= 通知对象 =======================

def test_新反馈发值班人_没人值班发部长(client, db, take_student, liaison):
    me = login(client, take_student())
    fid = _submit(client, me).json()["id"]
    head_notes = _notes_for(db, LIAISON_HEAD, fid, "新的志愿者反馈")
    assert len(head_notes) == 1
    assert not _notes_for(db, LIAISON_CORE, fid)
    from backend.integrations import msgcenter
    n = head_notes[0]
    assert msgcenter.page_for(n.template_key, n.data) == "/feedback?tab=volunteer"

    head = login(client, LIAISON_HEAD)
    lin = student_row(db, LIAISON_CORE)
    assert client.post(f"/api/staff/duty/{liaison.id}", headers=head,
                       json={"user_id": lin.id, "note": ""}).status_code == 200
    fid2 = _submit(client, me, "第二条").json()["id"]
    assert len(_notes_for(db, LIAISON_CORE, fid2, "新的志愿者反馈")) == 1
    assert not _notes_for(db, LIAISON_HEAD, fid2)
    # 管理员不逐条收志愿反馈的通知
    assert not _notes_for(db, ADMIN, fid2)


def test_回复后通知提交人且状态变已处理(client, db, take_student, liaison):
    sid = take_student()
    me = login(client, sid)
    fid = _submit(client, me).json()["id"]
    core = login(client, LIAISON_CORE)
    assert client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=core,
                       json={"reply": "   "}).status_code == 400
    r = client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=core,
                    json={"reply": "下次活动会备足饮用水"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "handled" and r.json()["handled_by_name"] == "林骁"
    notes = _notes_for(db, sid, fid, "志愿者反馈已回复")
    assert len(notes) == 1 and "下次活动会备足饮用水" in notes[0].content
    from backend.integrations import msgcenter
    assert msgcenter.page_for(notes[0].template_key, notes[0].data) \
        == "/messages?feedback=mine"
    mine = client.get("/api/feedback/mine", headers=me).json()
    assert mine[0]["status"] == "handled" and mine[0]["reply"] == "下次活动会备足饮用水"


# ======================= 回复模板 =======================

def test_回复模板按类型填充(client, admin, take_student, liaison):
    me = login(client, take_student())
    s = _submit(client, me, kind="suggest").json()["id"]
    a = _submit(client, me, kind="ask").json()["id"]
    items = {x["id"]: x for x in _items(client, admin)}
    t = now()
    for fid, label in ((s, "对于青协工作的改进建议或想法"), (a, "询问志愿相关问题")):
        draft = items[fid]["reply_draft"]
        assert draft.startswith("同学你好！\n")
        assert f"协会已收到平台上同学提出的【{label}】。" in draft
        assert "【解决方法】" in draft and "【提出" not in draft
        assert "中国人民大学青年志愿者协会" in draft
        assert draft.endswith(f"{t.year}年{t.month}月{t.day}日")


# ======================= 48 小时催办 =======================

def test_超时没回复只催一次并抄送分管会长(client, db, take_student, liaison, council_reset):
    fid = _submit(client, login(client, take_student()), anonymous=True).json()["id"]
    assert scheduled.remind_stale_feedback(db) >= 0
    assert not _notes_for(db, LIAISON_HEAD, fid, "志愿者反馈已等待")   # 还没到 48 小时

    fb = db.get(Feedback, fid)
    fb.created_at = now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1)
    db.commit()
    assert scheduled.remind_stale_feedback(db) >= 1
    head = _notes_for(db, LIAISON_HEAD, fid, "志愿者反馈已等待")
    assert len(head) == 1
    assert len(_notes_for(db, QINGXIE, fid, "志愿者反馈已等待")) == 1   # 没人分管，抄送会长
    assert not _notes_for(db, ADMIN, fid, "志愿者反馈已等待")   # 会长团里不分管的不收
    assert not _notes_for(db, LIAISON_CORE, fid, "志愿者反馈已等待")

    scheduled.remind_stale_feedback(db)
    assert len(_notes_for(db, LIAISON_HEAD, fid, "志愿者反馈已等待")) == 1


def test_有人值班时催值班人(client, db, take_student, liaison):
    lin = student_row(db, LIAISON_CORE)
    assert client.post(f"/api/staff/duty/{liaison.id}",
                       headers=login(client, LIAISON_HEAD),
                       json={"user_id": lin.id, "note": ""}).status_code == 200
    fid = _submit(client, login(client, take_student())).json()["id"]
    fb = db.get(Feedback, fid)
    fb.created_at = now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1)
    db.commit()
    # 值班人是接口在另一个会话里设的，这个会话里的部门对象还是旧的
    db.expire_all()
    assert scheduled.remind_stale_feedback(db) >= 1
    assert len(_notes_for(db, LIAISON_CORE, fid, "志愿者反馈已等待")) == 1
    assert not _notes_for(db, LIAISON_HEAD, fid, "志愿者反馈已等待")


def test_已回复的不催(client, admin, db, take_student, liaison):
    fid = _submit(client, login(client, take_student())).json()["id"]
    client.post(f"/api/staff/volunteer-feedback/{fid}/reply", headers=admin,
                json={"reply": "好"})
    fb = db.get(Feedback, fid)
    db.refresh(fb)
    fb.created_at = now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1)
    db.commit()
    scheduled.remind_stale_feedback(db)
    assert not _notes_for(db, LIAISON_HEAD, fid, "志愿者反馈已等待")


# ======================= 值班日报 =======================

def _at(hour: int) -> datetime:
    return datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)


def test_日报列出志愿反馈且匿名不写名字(client, admin, db, take_student, liaison,
                                monkeypatch):
    named_sid = take_student()
    named = student_row(db, named_sid)
    anon_sid = take_student()
    anon = student_row(db, anon_sid)
    done = _submit(client, login(client, named_sid), kind="resource").json()["id"]
    _submit(client, login(client, anon_sid), kind="nature", anonymous=True)
    client.post(f"/api/staff/volunteer-feedback/{done}/reply", headers=admin,
                json={"reply": "谢谢推荐"})

    dept = db.get(Department, liaison.id)
    dept.duty_digest_on = None
    db.commit()
    monkeypatch.setattr(scheduled, "now", lambda: _at(21))
    assert scheduled.send_duty_digests(db) >= 1
    db.expire_all()
    head = student_row(db, LIAISON_HEAD)
    text = (db.query(Notification)
            .filter(Notification.user_id == head.id,
                    Notification.title.startswith("值班日报｜对外联络部"))
            .order_by(Notification.created_at.desc()).first()).content
    new_part, rest = text.split("【今日处理】")
    done_part, pending_part = rest.split("【尚未处理】")
    assert f"志愿者反馈【推荐校内外志愿服务资源、活动创意等】（{named.name} 提交）" in new_part
    assert "志愿者反馈【存在不符合志愿性质的活动】（匿名志愿者 提交）" in new_part
    assert anon.name not in text
    assert "志愿者反馈【推荐校内外志愿服务资源、活动创意等】已回复" in done_part
    assert "志愿者反馈【存在不符合志愿性质的活动】" in pending_part


# ======================= 截图 =======================

def test_截图下载权限覆盖外联部(client, admin, take_student, liaison):
    me = login(client, take_student())
    vol = _submit(client, me, files=[("files", ("v.png", PNG, "image/png"))]).json()
    plat = client.post("/api/feedback", headers=me, data={"content": "平台"},
                       files=[("files", ("p.png", PNG, "image/png"))]).json()
    vpath, ppath = vol["images"][0]["url"], plat["images"][0]["url"]
    core = login(client, LIAISON_CORE)
    assert client.get(dl_url(client, core, vpath)).status_code == 200
    assert client.get(dl_url(client, me, vpath)).status_code == 200
    assert client.get(dl_url(client, admin, vpath)).status_code == 200
    assert client.get(dl_url(client, login(client, QINGXIE), vpath)).status_code == 200
    # 平台问题的截图外联部看不到；部门成员、别人看不到志愿反馈的截图
    assert client.get(ppath, headers=core).status_code == 404
    assert client.get(vpath, headers=login(client, LIAISON_MEMBER)).status_code == 404
    assert client.get(vpath, headers=login(client, take_student())).status_code == 404


# ======================= 关联活动 =======================

def test_关联活动候选与校验(client, admin, db, take_student, liaison):
    act, _shift, u = make_enrolled_activity(db, take_student)
    me = login(client, u.student_id)
    mine = client.get("/api/feedback/activities", headers=me).json()
    assert act.id in [x["id"] for x in mine]
    found = client.get("/api/feedback/activities",
                       params={"q": act.title[-6:]}, headers=me).json()
    assert found[0]["id"] == act.id and found[0]["mine"] is True
    # 别的学生也能按标题搜到已发布的活动
    other = login(client, take_student())
    hits = client.get("/api/feedback/activities", params={"q": act.title[-6:]},
                      headers=other).json()
    assert act.id in [x["id"] for x in hits]

    r = _submit(client, me, activity_id=act.id)
    assert r.status_code == 200, r.text
    assert r.json()["activity"] == {"id": act.id, "title": act.title}
    item = [x for x in _items(client, admin) if x["id"] == r.json()["id"]][0]
    assert item["activity"]["id"] == act.id
    assert _submit(client, me, activity_id="act_nope").status_code == 400
