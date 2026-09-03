"""导出表与签到大屏：新模型下字段是否对得上、组长有没有标出来。"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from backend.models import CheckinRecord, CheckinTask, Enrollment, now, uid
from conftest import editing, login


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
    # 组长不再自动产生：由管理者指定第一个报名的人
    head = (db.query(Enrollment)
            .filter(Enrollment.position_id == pos.id,
                    Enrollment.status == "enrolled")
            .order_by(Enrollment.joined_at).first())
    assert client.post(f"/api/staff/enrollments/{head.id}/leader", headers=admin,
                       json={"is_leader": True}).status_code == 200
    return act, shift, pos, people


def test_导出表带组长与调剂意愿(client, admin, db, enrolled_activity,
                              take_student):
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student)

    token = admin["Authorization"].split()[1]
    r = client.get(f"/api/staff/activities/{act.id}/export.xlsx?token={token}")
    assert r.status_code == 200

    wb = load_workbook(BytesIO(r.content))
    ws = wb[[s for s in wb.sheetnames if s != "活动概览"][0]]
    header = [c.value for c in ws[1]]
    assert "组长" in header and "接受调剂" in header and "岗位" in header

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
                       mode="group", token="ABC123",
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
    from backend.models import Position

    act, shift, _u = enrolled_activity
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

    token = admin["Authorization"].split()[1]
    r = client.get(f"/api/staff/activities/{act.id}/export.xlsx?token={token}")
    wb = load_workbook(BytesIO(r.content))
    ws = wb[[s for s in wb.sheetnames if s != "活动概览"][0]]
    text = "\n".join(str(c.value) for row in ws.iter_rows() for c in row
                     if c.value)
    assert "尚未分配岗位" in text


# ======================= 组长 =======================

def test_一个岗位只会有一个组长(client, admin, db, enrolled_activity,
                              take_student):
    """老师改判组长时，原组长必须被摘掉——两个组长会让现场不知道听谁的。"""
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=3)
    rows = (db.query(Enrollment)
            .filter(Enrollment.position_id == pos.id,
                    Enrollment.status == "enrolled")
            .order_by(Enrollment.joined_at).all())
    auto = [r for r in rows if r.is_leader]
    assert len(auto) == 1, "报名时应按先后自动产生一个组长"

    other = next(r for r in rows if not r.is_leader)
    r = client.post(f"/api/staff/enrollments/{other.id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 200
    db.expire_all()
    leaders = [x for x in db.query(Enrollment)
               .filter(Enrollment.position_id == pos.id,
                       Enrollment.is_leader.is_(True)).all()]
    assert len(leaders) == 1 and leaders[0].id == other.id


def test_取消组长后不自动补位(client, admin, db, enrolled_activity, take_student):
    """组长只由管理者指定：取消之后位子空着，等他再指一位。"""
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=3)
    leader = (db.query(Enrollment)
              .filter(Enrollment.position_id == pos.id,
                      Enrollment.is_leader.is_(True)).one())
    r = client.post(f"/api/staff/enrollments/{leader.id}/leader",
                    headers=admin, json={"is_leader": False})
    assert r.status_code == 200
    db.expire_all()
    assert (db.query(Enrollment)
            .filter(Enrollment.position_id == pos.id,
                    Enrollment.is_leader.is_(True)).count()) == 0


def test_关掉组长开关会清掉组长标记(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, pos, people = _setup(client, db, admin, enrolled_activity,
                                     take_student, n=2)
    assert db.query(Enrollment).filter(
        Enrollment.position_id == pos.id,
        Enrollment.is_leader.is_(True)).count() == 1

    with editing(client, admin, act.id):
        r = client.patch(f"/api/staff/positions/{pos.id}", headers=admin,
                         json={"needs_leader": False})
        assert r.status_code == 200
    db.expire_all()
    assert db.query(Enrollment).filter(
        Enrollment.position_id == pos.id,
        Enrollment.is_leader.is_(True)).count() == 0


def test_没设组长的岗位不能指定组长(client, admin, db, enrolled_activity,
                                  take_student):
    act, shift, _u = enrolled_activity
    pos = shift.positions[0]
    pos.needs_leader = False
    db.commit()
    enr = db.query(Enrollment).filter(
        Enrollment.position_id == pos.id).first()
    r = client.post(f"/api/staff/enrollments/{enr.id}/leader",
                    headers=admin, json={"is_leader": True})
    assert r.status_code == 409


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

    # 甲岗塞 3 人，故意让报名时间乱序，并把最后报名的那个设成组长
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
    base = now()
    for i, e in enumerate(rows_a):
        e.joined_at = base + timedelta(minutes=i)
        e.is_leader = False
    rows_a[-1].is_leader = True          # 最后报名的当组长
    leader_name = db.get(User, rows_a[-1].user_id).name
    # 乙岗塞 1 人
    sid = take_student()
    t = login(client, sid)
    assert client.post(f"/api/activities/{act.id}/shifts/{shift.id}/enroll",
                       headers=t,
                       json={"position_id": pos_b.id}).status_code == 200
    b_name = student_row(db, sid).name
    db.commit()

    token = admin["Authorization"].split()[1]
    r = client.get(f"/api/staff/activities/{act.id}/export.xlsx?token={token}")
    wb = load_workbook(BytesIO(r.content))
    ws = wb[[s for s in wb.sheetnames if s != "活动概览"][0]]
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
    rest = [db.get(User, e.user_id).name for e in rows_a[:-1]]
    assert a_block[1:] == rest, f"组长之后应按报名时间，实际 {a_block[1:]}"
    assert [n for n, p in table if p == pos_b.name] == [b_name]
