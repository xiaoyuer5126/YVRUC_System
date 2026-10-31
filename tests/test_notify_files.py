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


def test_传了没发的附件一天后清掉(client, admin, db, enrolled_activity):
    from datetime import timedelta
    from backend import config
    from backend.models import Attachment, now
    from backend.scheduled import purge_unsent_notify_files

    act, _shift, _u = enrolled_activity
    sent = _upload(client, admin, act.id).json()["id"]
    unsent = _upload(client, admin, act.id, name="没发的.pdf").json()["id"]
    assert _send(client, admin, act.id, [sent]).status_code == 200

    db.expire_all()
    old = now() - timedelta(hours=config.NOTIFY_FILE_KEEP_HOURS + 1)
    for att in db.query(Attachment).filter(Attachment.id.in_([sent, unsent])).all():
        att.created_at = old
    db.commit()
    assert purge_unsent_notify_files(db) >= 1
    db.expire_all()
    assert db.get(Attachment, unsent) is None
    assert db.get(Attachment, sent) is not None


def test_发过的附件不能再随别的通知发(client, admin, enrolled_activity):
    act, _shift, _u = enrolled_activity
    fid = _upload(client, admin, act.id).json()["id"]
    assert _send(client, admin, act.id, [fid]).status_code == 200
    r = _send(client, admin, act.id, [fid])
    assert r.status_code == 400 and "重新上传" in r.json()["detail"]


def test_没有收件人时不发也不动附件(client, admin, db, open_group):
    """一个人都没报的活动：报错，附件仍算「传了没发」，定时任务照常清。"""
    from backend.models import Attachment
    act, _g = open_group(3)
    fid = _upload(client, admin, act.id).json()["id"]
    r = _send(client, admin, act.id, [fid])
    assert r.status_code == 400 and "没有可通知" in r.json()["detail"]
    db.expire_all()
    assert db.get(Attachment, fid).owner_id == act.id
