"""导出表的列：志愿者信息带上性别、年级、校区、学段。"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from conftest import dl_url


INFO_HEADER = ["姓名", "学号", "性别", "学院", "年级", "校区", "学段",
               "身份证号", "手机号", "微信号"]


def test_志愿者信息页按顺序带性别年级校区学段(client, admin, db,
                                            enrolled_activity):
    """排班表的志愿者信息页给报保险、报入校用，个人字段要与档案一致。"""
    act, _shift, u = enrolled_activity
    u.gender = "女"
    u.grade = "2023"
    u.campus = "中关村校区"
    u.stage = "硕士研究生"
    db.commit()

    r = client.get(dl_url(client, admin,
                          f"/api/staff/activities/{act.id}/export.xlsx"))
    assert r.status_code == 200
    wb = load_workbook(BytesIO(r.content))
    info = wb["志愿者信息"]
    assert [c.value for c in info[1]] == INFO_HEADER

    row = next(row for row in info.iter_rows(min_row=2, values_only=True)
               if row[1] == u.student_id)
    assert list(row[2:7]) == [u.gender, u.college, u.grade, u.campus, u.stage]
