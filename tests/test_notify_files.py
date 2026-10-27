"""发通知可以带附件：收到通知的人和活动管理者能下载，别人 404。"""
from __future__ import annotations

from conftest import dl_url, login


def _upload(client, headers, act_id, name="集合须知.pdf"):
    return client.post(f"/api/staff/activities/{act_id}/notify-files", headers=headers,
                       files={"file": (name, b"%PDF-1.4 notice", "application/pdf")})


def _send(client, admin, act_id, file_ids):
    return client.post(f"/api/staff/activities/{act_id}/notify", headers=admin,
                       json={"scope": "all", "title": "集合须知",
                             "content": "请看附件", "file_ids": file_ids})


def test_通知附件只给收到的人和管理者(client, admin, enrolled_activity, take_student):
    act, _shift, u = enrolled_activity
    r = _upload(client, admin, act.id)
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    path = f"/api/materials/{fid}"

    # 还没发出去：报了名的志愿者也拿不到
    student = login(client, u.student_id)
    assert client.get(dl_url(client, student, path)).status_code == 404

    r = _send(client, admin, act.id, [fid])
    assert r.status_code == 200 and r.json()["count"] == 1

    item = client.get("/api/me/notifications", headers=student).json()["items"][0]
    assert item["data"]["files"] == [{"id": fid, "title": "集合须知.pdf",
                                      "size": len(b"%PDF-1.4 notice")}]
    # 推送带不了文件，正文里说一声
    assert "附件：集合须知.pdf" in item["content"]
    assert client.get(dl_url(client, student, path)).status_code == 200
    assert client.get(dl_url(client, admin, path)).status_code == 200

    other = login(client, take_student())
    assert client.get(dl_url(client, other, path)).status_code == 404


def test_附件只认本活动传的(client, admin, db, enrolled_activity, take_student):
    from conftest import make_enrolled_activity
    act, _shift, _u = enrolled_activity
    act2, _s2, _u2 = make_enrolled_activity(db, take_student)
    fid = _upload(client, admin, act2.id).json()["id"]
    r = _send(client, admin, act.id, [fid])
    assert r.status_code == 400 and "附件" in r.json()["detail"]
    r = _send(client, admin, act.id, ["att_nope"])
    assert r.status_code == 400


def test_附件类型不对拒收(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    r = _upload(client, admin, act.id, name="病毒.exe")
    assert r.status_code == 400 and "不支持的文件类型" in r.json()["detail"]
