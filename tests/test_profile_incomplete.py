"""资料没填完的学生报名被拦：报错带 code，前端按它弹出资料表单。

提示语在英文界面下会被翻译，前端不能拿中文原句判断是不是这一种拦截。
"""
from __future__ import annotations

from conftest import login, student_row


def test_资料不全报名回带code的403(client, db, open_group, take_student):
    act, g = open_group(2)
    sid = take_student()
    student_row(db, sid).wechat = ""
    db.commit()

    r = client.post(f"/api/activities/{act.id}/shifts/{g.id}/enroll",
                    headers=login(client, sid),
                    json={"position_id": g.test_position_id})
    assert r.status_code == 403, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "PROFILE_INCOMPLETE"
    assert "完善个人信息" in detail["message"]
