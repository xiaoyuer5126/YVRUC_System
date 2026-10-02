"""导出表与签到大屏：新模型下字段是否对得上、组长有没有标出来。"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from backend.models import (CheckinRecord, CheckinTask, Enrollment,
                            new_checkin_token, now, uid)
from conftest import dl_url, editing, login


def _roster_rows(client, admin, shift, pos) -> list[dict]:
    """名单上某岗位在册的人，按报名先后。"""
    r = client.get(f"/api/staff/shifts/{shift.id}/roster", headers=admin)
    assert r.status_code == 200, r.text
    return [x for x in r.json()["enrolled"] if x["position_id"] == pos.id]


def _leaders(client, admin, shift, pos) -> list[str]:
    return [x["enrollment_id"] for x in _roster_rows(client, admin, shift, pos)
            if x["is_leader"]]


def _export_sheet(client, admin, act):
    """排班表里第一个场次的那张名单。"""
    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/export.xlsx"))
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    return wb[[s for s in wb.sheetnames if s not in ("活动概览", "志愿者信息")][0]]


def _answers(client, admin, act, query: str = "") -> list[list]:
    """问卷结果导出的全部数据行（不含表头）。"""
    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/answers.xlsx{query}"))
    assert r.status_code == 200, r.text
    ws = load_workbook(BytesIO(r.content)).active
    return [[c.value for c in row] for row in ws.iter_rows(min_row=2)]


def _setup(client, db, admin, enrolled_activity, take_student, n=3):
    """在已有活动上再塞几个人，其中一个当组长、一个签到。"""
    act, shift, first = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = True
    db.commit()

    people = [first]
    for _ in range(n - 1):
        sid = take_student()
        t = login(client, sid)
        r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                        headers=t, json={"position_id": pos.id,
                                         "leader_willing": True})
        assert r.status_code == 200, r.text
        from conftest import student_row
        people.append(student_row(db, sid))
    # 组长不自动产生：由管理者指定第一个报名的人
    head = _roster_rows(client, admin, shift, pos)[0]["enrollment_id"]
    assert client.post(f"/api/staff/enrollments/{head}/leader", headers=admin,
                       json={"is_leader": True}).status_code == 200
    return act, shift, pos, people


def test_排班表带组长性别与微信号(client, admin, db, enrolled_activity,
                                take_student):
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student)
    ws = _export_sheet(client, admin, act)
    header = [c.value for c in ws[1]]
    assert header[:7] == ["姓名", "学号", "性别", "学院", "手机号", "微信号", "场次时间"]
    assert "组长" in header and "岗位" in header and "接受调剂" not in header

    col = header.index("组长") + 1
    leaders = [ws.cell(row=i, column=col).value
               for i in range(2, ws.max_row + 1)]
    assert leaders.count("组长 ★") == 1, f"组长列应恰好一个，实际 {leaders}"


def test_大屏以确认名单为主表并标出组长(client, admin, db, enrolled_activity,
                                     take_student):
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student)
    enr = (db.query(Enrollment)
           .filter(Enrollment.position_id == pos.id,
                   Enrollment.status == "enrolled").all())
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=shift.id,
                       mode="group", token=new_checkin_token(),
                       valid_from=shift.start_time, valid_to=shift.end_time,
                       created_by="system")
    db.add(task)
    db.flush()
    db.add(CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                         shift_id=shift.id, position_id=pos.id,
                         user_id=enr[0].user_id, method="manual",
                         checkin_at=now()))
    db.commit()

    board = client.get(f"/api/staff/shifts/{shift.id}/board",
                       headers=admin).json()
    assert board["expected"] == len(enr)      # 未签到的也要在名单里
    assert board["checked"] == 1
    assert sum(1 for r in board["records"] if r["is_leader"]) == 1
    assert all(r["position"] for r in board["records"])


def test_未分配名单只在有岗位时出现(client, admin, db, enrolled_activity,
                                  take_student):
    """待在「待分配」占位岗位里的人要单独列出来，不能悄悄混进正式名单。

    他们在表里挂着一个岗位名，但那不是真岗位，现场照它点名会找不到地方。
    """
    from conftest import student_row

    act, shift, _u = enrolled_activity
    # 反例：人都在真岗位上时，不该冒出这一块
    ws = _export_sheet(client, admin, act)
    assert "以下人员尚未分配岗位" not in [c.value for row in ws.iter_rows()
                                          for c in row]

    shift.mode = "assign"
    # 把原来的测试岗位改成占位岗位，模拟「截止后统一分配」的场次
    holder = shift.positions[0]
    holder.is_placeholder = True
    holder.quota = 10
    db.commit()

    sid = take_student()
    t = login(client, sid)
    r = client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                    headers=t, json={})
    assert r.status_code == 200, r.text

    ws = _export_sheet(client, admin, act)
    col_a = [ws.cell(row=i, column=1).value for i in range(1, ws.max_row + 1)]
    assert "以下人员尚未分配岗位" in col_a
    # 新报的这个人要列在那行标题下面，而不只是出现在正式名单里
    below = col_a[col_a.index("以下人员尚未分配岗位") + 1:]
    assert student_row(db, sid).name in below


# ======================= 组长 =======================

def test_一个岗位只会有一个组长(client, admin, db, enrolled_activity,
                              take_student):
    """老师改判组长时，原组长必须被摘掉——两个组长会让现场不知道听谁的。"""
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=3)
    assert len(_leaders(client, admin, shift, pos)) == 1, "_setup 应已指定一个组长"

    other = next(x["enrollment_id"] for x in _roster_rows(client, admin, shift, pos)
                 if not x["is_leader"])
    r = client.post(f"/api/staff/enrollments/{other}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 200
    assert _leaders(client, admin, shift, pos) == [other]


def test_取消组长后不自动补位(client, admin, db, enrolled_activity, take_student):
    """组长只由管理者指定：取消之后位子空着，等他再指一位。"""
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=3)
    [leader] = _leaders(client, admin, shift, pos)
    r = client.post(f"/api/staff/enrollments/{leader}/leader",
                    headers=admin, json={"is_leader": False})
    assert r.status_code == 200
    assert _leaders(client, admin, shift, pos) == []


def test_关掉组长开关会清掉组长标记(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=2)
    assert len(_leaders(client, admin, shift, pos)) == 1

    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                         json={"needs_leader": False})
        assert r.status_code == 200
    assert _leaders(client, admin, shift, pos) == []


def test_没设组长的岗位不能指定组长(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = False
    db.commit()
    enr_id = _roster_rows(client, admin, shift, pos)[0]["enrollment_id"]
    r = client.post(f"/api/staff/enrollments/{enr_id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 409
    assert _leaders(client, admin, shift, pos) == []


def test_导出表按岗位分块组长置顶其余按报名时间(client, admin, db,
                                              enrolled_activity, take_student):
    """这张表是拿去现场点名的，顺序要跟现场的组织方式一致。"""
    from datetime import timedelta

    from backend.models import Position, User
    from conftest import POSITION_TEXT, student_row

    act, shift, first = enrolled_activity
    pos_a = shift.positions[0]
    pos_a.needs_leader = True
    # 再建一个岗位，排在 A 后面，用来验证「先按岗位分块」
    pos_b = Position(id=uid("p"), shift_id=shift.id, name="乙岗", quota=5,
                     sort=pos_a.sort + 1, **POSITION_TEXT)
    db.add(pos_b)
    db.commit()

    # 甲岗再塞 3 人（连同原有 1 人共 4 人），把报名时间改得跟写库顺序对不上，
    # 并把最晚报名的那个设成组长
    names = []
    for _ in range(3):
        sid = take_student()
        t = login(client, sid)
        assert client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                           headers=t,
                           json={"position_id": pos_a.id}).status_code == 200
        names.append(student_row(db, sid).name)
    rows_a = (db.query(Enrollment)
              .filter(Enrollment.position_id == pos_a.id,
                      Enrollment.status == "enrolled")
              .order_by(Enrollment.joined_at).all())
    assert len(rows_a) == 4
    # 既不是写库顺序也不是它的倒序：按插入顺序或 id 排都排不出这个结果
    base = now()
    for e, minutes in zip(rows_a, (2, 0, 3, 1)):
        e.joined_at = base + timedelta(minutes=minutes)
        e.is_leader = False
    by_join = sorted(rows_a, key=lambda e: e.joined_at)
    by_join[-1].is_leader = True          # 最后报名的当组长
    leader_name = db.get(User, by_join[-1].user_id).name
    # 乙岗塞 1 人
    sid = take_student()
    t = login(client, sid)
    assert client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                       headers=t,
                       json={"position_id": pos_b.id}).status_code == 200
    b_name = student_row(db, sid).name
    db.commit()

    ws = _export_sheet(client, admin, act)
    header = [c.value for c in ws[1]]
    c_name, c_pos = header.index("姓名") + 1, header.index("岗位") + 1
    table = [(ws.cell(row=i, column=c_name).value,
              ws.cell(row=i, column=c_pos).value)
             for i in range(2, ws.max_row + 1)]

    order = [p for _n, p in table]
    # 岗位分块：甲岗的行必须连续，乙岗在其后
    assert order == sorted(order, key=lambda p: 0 if p == pos_a.name else 1)
    a_block = [n for n, p in table if p == pos_a.name]
    assert a_block[0] == leader_name, f"组长要排在本岗位第一行，实际 {a_block}"
    # 组长之后按报名时间；组长本人是最后报名的，所以余下的顺序就是原顺序
    rest = [db.get(User, e.user_id).name for e in by_join[:-1]]
    assert a_block[1:] == rest, f"组长之后应按报名时间，实际 {a_block[1:]}"
    assert [n for n, p in table if p == pos_b.name] == [b_name]


def test_导出表不把志愿者填的内容当公式(client, admin, db, enrolled_activity,
                                    take_student):
    """志愿者能自由填写的字段会进导出表；= 开头的文本必须按文本存。

    Excel 见到公式就执行，=HYPERLINK("http://…") 之类一打开就中招。
    """
    from backend.models import FormQuestion

    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=1)
    q = FormQuestion(id=uid("fq"), activity_id=act.id, kind="text",
                     title="有什么想说的", required=False, sort=0)
    db.add(q)
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == shift.id,
                   Enrollment.user_id == people[0].id).one())
    enr.answers = {q.id: '=HYPERLINK("http://evil.example","点我")'}
    db.commit()

    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/answers.xlsx"))
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    formulas = [c.value for ws in wb for row in ws.iter_rows() for c in row
                if c.data_type == "f"]
    assert not formulas, f"导出表里仍有公式单元格：{formulas}"
    # 内容本身要原样留着（按文本），不能靠把答案丢掉来「通过」
    payload = '=HYPERLINK("http://evil.example","点我")'
    assert any(c.value == payload and c.data_type == "s"
               for ws in wb for row in ws.iter_rows() for c in row)


def test_微信号不能以公式符号开头(client, take_student):
    sid = take_student()
    t = login(client, sid)
    bad = client.put("/api/me/profile", headers=t,
                     json={"phone": "13900000000", "wechat": "=1+1",
                           "campus": "中关村校区", "no_mainland_id": True})
    assert bad.status_code == 400
    ok = client.put("/api/me/profile", headers=t,
                    json={"phone": "13900000000", "wechat": "zhang_san-01",
                          "campus": "中关村校区", "no_mainland_id": True})
    assert ok.status_code == 200, ok.text


def test_排班表的页签是概览志愿者信息和各场次名单(client, admin, db,
                                                enrolled_activity, take_student):
    """这张表是拿去现场点名、报保险的。时长和问卷各有专门的导出，不混在这儿。"""
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student)
    people[0].id_card = "110101200001011234"
    people[1].id_card = ""
    people[1].no_mainland_id = True
    people[2].wechat = "wx_third"
    db.commit()

    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/export.xlsx"))
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    assert wb.sheetnames[:2] == ["活动概览", "志愿者信息"]
    assert len(wb.sheetnames) == 2 + len(act.shifts)

    info = wb["志愿者信息"]
    assert [c.value for c in info[1]] == ["姓名", "学号", "学院", "身份证号", "手机号", "微信号"]
    rows = {r[1]: r for r in ([c.value for c in row] for row in info.iter_rows(min_row=2))}
    assert set(rows) == {u.student_id for u in people}       # 一人一行
    assert rows[people[0].student_id][3] == "110101200001011234"
    assert rows[people[1].student_id][3] == "无大陆身份证"
    assert rows[people[2].student_id][5] == "wx_third"
    # 问卷答案不进排班表
    ws = _export_sheet(client, admin, act)
    assert [c.value for c in ws[1]][-2:] == ["签到状态", "签退时间"]


def test_问卷结果按场次与岗位筛选(client, admin, db, enrolled_activity, take_student):
    from backend.models import FormQuestion, Position

    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student)
    other = Position(id=uid("st"), shift_id=shift.id, name="另一个岗位", quota=5,
                     sort=99)
    db.add(other)
    db.flush()          # 先落岗位，下面改报名的外键才指得到它
    q = FormQuestion(id=uid("fq"), activity_id=act.id, kind="text",
                     title="特长", required=False, sort=0)
    db.add(q)
    moved = (db.query(Enrollment)
             .filter(Enrollment.shift_id == shift.id,
                     Enrollment.user_id == people[2].id).one())
    moved.position_id = other.id
    moved.answers = {q.id: "会摄影"}
    db.commit()

    everyone = _answers(client, admin, act)
    assert {r[1] for r in everyone} == {u.student_id for u in people}
    assert everyone[0][:6] == [people[0].name, people[0].student_id, people[0].college,
                               people[0].phone, shift.name, pos.name]
    row = next(r for r in everyone if r[1] == people[2].student_id)
    assert row[5:] == ["另一个岗位", "会摄影"]

    only = _answers(client, admin, act, f"?positions={other.id}")
    assert [r[1] for r in only] == [people[2].student_id]
    both = _answers(client, admin, act, f"?shifts={shift.id}&positions={pos.id},{other.id}")
    assert len(both) == 3
    assert _answers(client, admin, act, "?shifts=nope") == []


# ======================= 排班表与附件分开下 =======================

def test_问卷结果只给excel附件按场次岗位分文件夹另外打包(client, admin, db,
                                                  open_group, take_student):
    """表里写附件包内的文件名（带「场次/岗位/」目录），同名的加序号、
    题目里的非法字符换成下划线，与附件包里一字不差。"""
    import zipfile
    from backend.models import Activity
    from conftest import student_row

    act, g = open_group(3)
    db.get(Activity, act.id).status = "draft"
    db.commit()
    r = client.put(f"/api/staff/activities/{act.id}/form", headers=admin,
                   json={"questions": [
                       {"kind": "file", "title": "证明材料", "required": False},
                       {"kind": "file", "title": "证明材料", "required": False},
                       {"kind": "file", "title": "学生证/校园卡", "required": False},
                   ]})
    assert r.status_code == 200, r.text
    qs = [q["id"] for q in r.json()["questions"]]
    db.get(Activity, act.id).status = "published"
    db.commit()

    base = f"/api/staff/activities/{act.id}/answers"
    # 还没人报名：附件数 0，附件包 404
    assert client.get(f"{base}/attachments", headers=admin).json() == {"count": 0}
    assert client.get(dl_url(client, admin, f"{base}/attachments.zip")
                      ).status_code == 404

    sid = take_student()
    h = login(client, sid)
    ids = []
    for i, ext in enumerate(("pdf", "pdf", "jpg")):
        r = client.post(f"/api/activities/{act.id}/apply/upload", headers=h,
                        files={"file": (f"f.{ext}", f"data{i}".encode(), "application/octet-stream")})
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])
    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll", headers=h,
                    json={"position_id": g.test_position_id,
                          "answers": dict(zip(qs, ids))})
    assert r.status_code == 200, r.text

    from backend.models import Position
    u = student_row(db, sid)
    folder = f"{g.name}/{db.get(Position, g.test_position_id).name}/"
    stem = f"{folder}{u.name}_{u.student_id}"
    names = [f"{stem}_证明材料.pdf", f"{stem}_证明材料(2).pdf", f"{stem}_学生证_校园卡.jpg"]

    r = client.get(dl_url(client, admin, f"{base}.xlsx"))
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]
    ws = load_workbook(BytesIO(r.content)).active
    row = [c for c in ws[2]][-3:]
    assert [c.value for c in row] == names
    assert all(c.hyperlink is None for c in row)

    assert client.get(f"{base}/attachments", headers=admin).json() == {"count": 3}
    # 筛到别的岗位：一个附件都没有
    assert client.get(f"{base}/attachments?positions=nope",
                      headers=admin).json() == {"count": 0}
    r = client.get(dl_url(client, admin, f"{base}/attachments.zip"))
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    z = zipfile.ZipFile(BytesIO(r.content))
    assert z.namelist() == names
    assert [z.read(n) for n in names] == [b"data0", b"data1", b"data2"]


def test_打包时文件刚被删掉也跳过不报错(db):
    """登记时还在、打包前没了（清理任务刚好跑过）：跳过这一份，其余照打。"""
    import zipfile
    from backend.exporter import MATERIAL_DIR, Export

    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    here = MATERIAL_DIR / f"{uid('t')}.pdf"
    here.write_bytes(b"ok")
    ex = Export(db)
    ex.files = [("在.pdf", here), ("不在.pdf", MATERIAL_DIR / f"{uid('t')}.pdf")]
    try:
        z = zipfile.ZipFile(ex.attachments_zip())
        assert z.namelist() == ["在.pdf"] and z.read("在.pdf") == b"ok"
    finally:
        here.unlink()
    assert Export(db).attachments_zip() is None
