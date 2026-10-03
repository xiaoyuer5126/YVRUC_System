"""导入排班与调岗后的递补。

导入：管理者导出排班表、在 Excel 里改「岗位」「组长」两列、传回来，拼成一份
和一键分配同样的方案，确认后走同一个提交接口。只给截止后统一分配的场次。
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from backend.models import Enrollment, Position, User, uid
from conftest import POSITION_TEXT, dl_url, login

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _upload(client, admin, g, data: bytes):
    return client.post(f"/api/staff/shifts/{g.id}/assign/import", headers=admin,
                       files={"file": ("排班.xlsx", data, XLSX)})


def test_导出改好再导入(client, admin, db, open_group, take_student):
    act, g = open_group(5, mode="assign")
    a = Position(id=uid("p"), shift_id=g.id, name="甲", quota=2, sort=0, **POSITION_TEXT)
    b = Position(id=uid("p"), shift_id=g.id, name="乙", quota=2, sort=1,
                 needs_leader=True, **POSITION_TEXT)
    db.add_all([a, b])
    db.commit()
    sids = [take_student() for _ in range(4)]
    for sid in sids:
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=login(client, sid), json={})
        assert r.status_code == 200, r.text

    r = client.get(dl_url(client, admin, f"/api/staff/activities/{act.id}/export.xlsx"))
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    ws = wb[g.name]
    header = [c.value for c in ws[1]]
    col_sid, col_pos, col_leader = (header.index(k) + 1 for k in ("学号", "岗位", "组长"))
    rows = {ws.cell(row=i, column=col_sid).value: i for i in range(2, ws.max_row + 1)
            if ws.cell(row=i, column=col_pos).value}
    ws.cell(row=rows[sids[0]], column=col_pos, value="甲")
    ws.cell(row=rows[sids[0]], column=col_leader, value="是")     # 甲不设组长：这一格不导
    ws.cell(row=rows[sids[1]], column=col_pos, value="乙")
    ws.cell(row=rows[sids[1]], column=col_leader, value="组长 ★")
    ws.cell(row=rows[sids[2]], column=col_pos, value="丙")        # 没有这个岗位
    # sids[3] 那一行原样是「待分配」：不动
    n = ws.max_row + 1
    ws.cell(row=n, column=col_sid, value="20990001")
    ws.cell(row=n, column=col_pos, value="甲")
    buf = BytesIO()
    wb.save(buf)

    r = _upload(client, admin, g, buf.getvalue())
    assert r.status_code == 200, r.text
    d = r.json()
    by_user = {x["user"]["student_id"]: x["position_id"] for x in d["plan"]}
    assert by_user == {sids[0]: a.id, sids[1]: b.id}
    msgs = [e["message"] for e in d["errors"]]
    assert any("没有「丙」" in m for m in msgs)
    assert any("20990001" in m and "不在本场次" in m for m in msgs)
    assert any("「甲」没有设组长" in m for m in msgs)
    leader = db.query(Enrollment).join(User, Enrollment.user_id == User.id).filter(
        Enrollment.shift_id == g.id, User.student_id == sids[1]).one()
    assert d["leaders"] == [leader.id]

    # 提交走一键分配的接口；组长在人落到岗位之后再设
    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"plan": [{"enrollment_id": x["enrollment_id"],
                                    "position_id": x["position_id"]} for x in d["plan"]]})
    assert r.status_code == 200, r.text
    r = client.post(f"/api/staff/enrollments/{leader.id}/leader", headers=admin,
                    json={"is_leader": True})
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Enrollment, leader.id).is_leader


def test_导入只给截止后分配的场次_文件不对说清楚(client, admin, open_group):
    _act, pick = open_group(3)
    r = _upload(client, admin, pick, b"whatever")
    assert r.status_code == 400 and "不需要排班" in r.json()["detail"]
    _act, g = open_group(3, mode="assign")
    r = _upload(client, admin, g, b"not an xlsx")
    assert r.status_code == 400 and ".xlsx" in r.json()["detail"]


def test_调岗空出的位子候补马上顶上(client, admin, db, open_group, take_student):
    """调走的人空出来的位子要立刻给排队的人，不能等下一次有人退选。"""
    act, g = open_group(1)
    b = Position(id=uid("p"), shift_id=g.id, name="乙岗", quota=1, sort=1, **POSITION_TEXT)
    db.add(b)
    db.commit()
    ids = []
    for _ in range(2):
        r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                        headers=login(client, take_student()),
                        json={"position_id": g.test_position_id})
        ids.append(r.json()["enrollment_id"])
    assert db.get(Enrollment, ids[1]).status == "waitlisted"
    r = client.post(f"/api/staff/shifts/{g.id}/assign/apply", headers=admin,
                    json={"source": "manual", "plan": [
                        {"enrollment_id": ids[0], "position_id": b.id}]})
    assert r.status_code == 200 and r.json()["promoted"] == 1, r.text
    db.expire_all()
    assert db.get(Enrollment, ids[1]).status == "pending_confirm"
