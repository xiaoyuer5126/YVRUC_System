"""即选即得的报名问卷：同一活动报第二个场次时，答过的题不再问。

答案沿用他在本活动里还有效（已报名、候补中、待确认）的报名；全部退掉之后再报
要从头答。每条报名仍存这个岗位适用的整份答案，导出照旧一人每岗位一行。
"""
from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from openpyxl import load_workbook

from backend.models import Attachment, Enrollment, FormQuestion, Position, now, uid
from conftest import POSITION_TEXT, dl_url, login, mk_shift, student_row


def _setup(db, open_group):
    """一个活动两个场次（各一个岗位），问卷四道题：

    尺码（人人答，必填）、证明（人人答，文件，选填）、需要住宿（人人答，必填）、
    住几晚（只问第二场的岗位，显示条件：需要住宿 = 是，必填）。
    """
    act, g1 = open_group(5)
    start = g1.start_time + timedelta(days=1)
    g2 = mk_shift(id=uid("g"), activity_id=act.id, name="第二场",
                  start_time=start, end_time=start + timedelta(hours=3),
                  mode="pick", sort=1, lock_hours=0)
    db.add(g2)
    db.flush()
    p2 = Position(id=uid("p"), shift_id=g2.id, name="第二场岗位", quota=5,
                  waitlist_quota=3, sort=0, **POSITION_TEXT)
    db.add(p2)
    db.flush()
    q = {
        "size": FormQuestion(id=uid("fq"), activity_id=act.id, kind="single",
                             title="尺码", options=["M", "L"], sort=0),
        "proof": FormQuestion(id=uid("fq"), activity_id=act.id, kind="file",
                              title="证明", required=False, sort=1),
        "stay": FormQuestion(id=uid("fq"), activity_id=act.id, kind="single",
                             title="需要住宿", options=["是", "否"], sort=2),
    }
    q["nights"] = FormQuestion(id=uid("fq"), activity_id=act.id, kind="text",
                               title="住几晚", sort=3,
                               applies_to_positions=[p2.id],
                               show_if={"question_id": q["stay"].id, "values": ["是"]})
    db.add_all(q.values())
    db.commit()
    return act, g1, g1.test_position_id, g2, p2.id, {k: v.id for k, v in q.items()}


def _upload(client, h, act):
    r = client.post(f"/api/activities/{act.id}/apply/upload", headers=h,
                    files={"file": ("证明.pdf", b"%PDF-1.4 demo", "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _enroll(client, h, act, g, pid, answers):
    return client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll", headers=h,
                       json={"position_id": pid, "answers": answers})


def _row(db, act, g, user_id) -> Enrollment:
    db.expire_all()
    return (db.query(Enrollment)
            .filter(Enrollment.activity_id == act.id, Enrollment.shift_id == g.id,
                    Enrollment.user_id == user_id)
            .order_by(Enrollment.joined_at.desc()).first())


def test_第二次报名不交答过的题也能报上_答案合并存下(client, db, open_group,
                                                take_student):
    act, g1, p1, g2, p2, q = _setup(db, open_group)
    sid = take_student()
    h = login(client, sid)
    u = student_row(db, sid)
    att = _upload(client, h, act)
    first = {q["size"]: "L", q["proof"]: att, q["stay"]: "是"}
    r = _enroll(client, h, act, g1, p1, first)
    assert r.status_code == 200, r.text
    assert _row(db, act, g1, u.id).answers == first

    # 详情页把答过的题给到报名弹窗
    d = client.get(f"/api/activities/{act.id}", headers=h).json()
    assert d["my_answers"] == first

    # 第二场只交新题；住几晚的显示条件按沿用来的「需要住宿 = 是」判断
    r = _enroll(client, h, act, g2, p2, {q["nights"]: "两晚"})
    assert r.status_code == 200, r.text
    assert _row(db, act, g2, u.id).answers == {**first, q["nights"]: "两晚"}
    # 第一条报名上的答案不动
    assert _row(db, act, g1, u.id).answers == first


def test_答过的题以已存的为准(client, db, open_group, take_student):
    """页面数据旧了、把答过的题又交一遍，同一题也不会在两条报名上答成两样。"""
    act, g1, p1, g2, p2, q = _setup(db, open_group)
    sid = take_student()
    h = login(client, sid)
    u = student_row(db, sid)
    assert _enroll(client, h, act, g1, p1,
                   {q["size"]: "L", q["stay"]: "否"}).status_code == 200
    r = _enroll(client, h, act, g2, p2, {q["size"]: "M", q["stay"]: "否"})
    assert r.status_code == 200, r.text
    assert _row(db, act, g2, u.id).answers == {q["size"]: "L", q["stay"]: "否"}


def test_新题必填照样拦(client, db, open_group, take_student):
    act, g1, p1, g2, p2, q = _setup(db, open_group)
    h = login(client, take_student())
    assert _enroll(client, h, act, g1, p1,
                   {q["size"]: "L", q["stay"]: "是"}).status_code == 200
    r = _enroll(client, h, act, g2, p2, {})
    assert r.status_code == 400 and "住几晚" in r.json()["detail"]


def test_全部退掉之后再报要重答(client, db, open_group, take_student):
    act, g1, p1, g2, p2, q = _setup(db, open_group)
    sid = take_student()
    h = login(client, sid)
    u = student_row(db, sid)
    assert _enroll(client, h, act, g1, p1,
                   {q["size"]: "L", q["stay"]: "否"}).status_code == 200
    enr = _row(db, act, g1, u.id)
    r = client.delete(f"/api/activities/enrollments/{enr.id}", headers=h)
    assert r.status_code == 200, r.text
    assert client.get(f"/api/activities/{act.id}", headers=h).json()["my_answers"] == {}

    r = _enroll(client, h, act, g2, p2, {})
    assert r.status_code == 400 and "尺码" in r.json()["detail"]
    # 重答就能改答案
    r = _enroll(client, h, act, g2, p2,
                {q["size"]: "M", q["stay"]: "是", q["nights"]: "一晚"})
    assert r.status_code == 200, r.text
    assert _row(db, act, g2, u.id).answers == {
        q["size"]: "M", q["stay"]: "是", q["nights"]: "一晚"}


def test_沿用的附件退掉一条报名也不被清理(client, db, open_group, take_student):
    from backend import config
    from backend.routers.materials import MATERIAL_DIR
    from backend.scheduled import purge_orphan_attachments

    act, g1, p1, g2, p2, q = _setup(db, open_group)
    sid = take_student()
    h = login(client, sid)
    u = student_row(db, sid)
    att = _upload(client, h, act)
    assert _enroll(client, h, act, g1, p1,
                   {q["size"]: "L", q["proof"]: att, q["stay"]: "否"}).status_code == 200
    assert _enroll(client, h, act, g2, p2, {}).status_code == 200
    assert _row(db, act, g2, u.id).answers[q["proof"]] == att

    # 退掉第一条，第二条仍引用着这个文件
    first = _row(db, act, g1, u.id)
    assert client.delete(f"/api/activities/enrollments/{first.id}",
                         headers=h).status_code == 200
    row = db.get(Attachment, att)
    row.created_at = now() - timedelta(days=config.ORPHAN_ATTACHMENT_DAYS + 1)
    stored = row.stored_name
    db.commit()
    purge_orphan_attachments(db)
    db.expire_all()
    assert db.get(Attachment, att) is not None
    assert (MATERIAL_DIR / stored).exists()


def test_导出仍是一人每个岗位一行(client, admin, db, open_group, take_student):
    act, g1, p1, g2, p2, q = _setup(db, open_group)
    sid = take_student()
    h = login(client, sid)
    assert _enroll(client, h, act, g1, p1,
                   {q["size"]: "L", q["stay"]: "是"}).status_code == 200
    assert _enroll(client, h, act, g2, p2, {q["nights"]: "两晚"}).status_code == 200

    r = client.get(dl_url(client, admin, f"/api/staff/activities/{act.id}/answers.xlsx"))
    assert r.status_code == 200, r.text
    ws = load_workbook(BytesIO(r.content)).active
    header = [c.value for c in ws[1]]
    rows = [dict(zip(header, (c.value for c in row)))
            for row in ws.iter_rows(min_row=2)]
    mine = [x for x in rows if x["学号"] == sid]
    assert sorted(x["场次"] for x in mine) == sorted([g1.name, "第二场"])
    assert all(x["尺码"] == "L" and x["需要住宿"] == "是" for x in mine)
    by_shift = {x["场次"]: x for x in mine}
    assert by_shift["第二场"]["住几晚"] == "两晚"
    assert not by_shift[g1.name]["住几晚"]
