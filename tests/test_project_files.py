"""立项书与盖章立项书：提交对外联络部审批要立项书，通过后锁死、驳回即作废；
对外活动的时长报批要盖章立项书，自动报批缺它就停下来提醒一次。"""
from __future__ import annotations

from datetime import timedelta

from backend import scheduled
from backend.models import (Activity, ActivityManager, Attachment, Department,
                            HoursRequest, Notification, now, uid)
from backend.routers.materials import MATERIAL_DIR
from conftest import (ADMIN, INITIATOR, LIAISON_CORE, LIAISON_HEAD, dl_url, inbox,
                      login, make_draft, student_row, upload_proposal)


def _files(client, headers, aid):
    r = client.get(f"/api/staff/activities/{aid}/project-files", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def _upload(client, headers, aid, slot, name="x.pdf"):
    return client.post(f"/api/staff/activities/{aid}/project-files/{slot}",
                       headers=headers,
                       files={"file": (name, b"%PDF content", "application/pdf")})


def _submit(client, headers, aid, dept_id):
    return client.post(f"/api/staff/activities/{aid}/submit", headers=headers,
                       json={"department_id": dept_id})


# ======================= 立项书 =======================

def test_提交对外联络部必须带立项书(client, admin, db, liaison):
    aid = make_draft(client, admin, proposal=False)
    r = _submit(client, admin, aid, liaison.id)
    assert r.status_code == 400 and "立项书" in r.json()["detail"]

    # 只有管理者能传
    assert _upload(client, login(client, INITIATOR), aid, "proposal").status_code == 403
    assert _upload(client, admin, aid, "proposal", "a.exe").status_code == 400
    assert _upload(client, admin, aid, "proposal", "v1.pdf").status_code == 200
    # 再传就是替换，只留一份，旧文件从磁盘上删掉
    old = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                      Attachment.owner_id == aid).one().stored_name
    assert _upload(client, admin, aid, "proposal", "v2.pdf").status_code == 200
    db.expire_all()
    rows = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                       Attachment.owner_id == aid).all()
    assert [r.title for r in rows] == ["v2.pdf"]
    assert not (MATERIAL_DIR / old).exists()
    assert _submit(client, admin, aid, liaison.id).status_code == 200


def test_别的部门不要立项书(client, admin, db, liaison):
    """只对对外联络部要求。办公室没有部长，临时给它一个再撤掉。"""
    other = db.query(Department).filter(Department.name == "办公室").one()
    admin_user = student_row(db, ADMIN)
    old = (admin_user.department_id, admin_user.qingxie_post)
    admin_user.department_id, admin_user.qingxie_post = other.id, "head"
    db.commit()
    try:
        aid = make_draft(client, admin, proposal=False)
        assert _submit(client, admin, aid, other.id).status_code == 200
    finally:
        db.expire_all()
        admin_user = student_row(db, ADMIN)
        admin_user.department_id, admin_user.qingxie_post = old
        db.commit()


def test_审批期间能换_通过后锁死(client, admin, db, liaison):
    aid = make_draft(client, admin)
    assert _submit(client, admin, aid, liaison.id).status_code == 200
    assert _upload(client, admin, aid, "proposal", "改过.pdf").status_code == 200
    head = login(client, LIAISON_HEAD)
    assert client.post(f"/api/staff/activities/{aid}/decide", headers=head,
                       json={"approve": True}).status_code == 200
    r = _upload(client, admin, aid, "proposal")
    assert r.status_code == 409 and "不能再更换" in r.json()["detail"]
    d = _files(client, admin, aid)
    assert d["proposal"]["title"] == "改过.pdf" and d["can_upload_proposal"] is False


def test_驳回即作废立项书(client, admin, db, liaison):
    aid = make_draft(client, admin)
    stored = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                         Attachment.owner_id == aid).one().stored_name
    assert _submit(client, admin, aid, liaison.id).status_code == 200
    head = login(client, LIAISON_HEAD)
    assert client.post(f"/api/staff/activities/{aid}/decide", headers=head,
                       json={"approve": False, "comment": "立项书缺预算"}).status_code == 200
    assert _files(client, admin, aid)["proposal"] is None
    assert not (MATERIAL_DIR / stored).exists()
    title = db.get(Activity, aid).title
    assert "上传新的立项书" in inbox(db, ADMIN, f"审批未通过｜{title}")[0].content
    # 重新提交得再传一份
    assert _submit(client, admin, aid, liaison.id).status_code == 400
    upload_proposal(client, admin, aid)
    assert _submit(client, admin, aid, liaison.id).status_code == 200


def test_转进对外联络部没有立项书就批不了(client, admin, db, liaison):
    aid = make_draft(client, admin, proposal=False)
    act = db.get(Activity, aid)
    act.status, act.approval_dept_id = "pending", liaison.id
    act.approval_assignee_id = student_row(db, LIAISON_HEAD).id
    db.commit()
    head = login(client, LIAISON_HEAD)
    r = client.post(f"/api/staff/activities/{aid}/decide", headers=head,
                    json={"approve": True})
    assert r.status_code == 400 and "立项书" in r.json()["detail"]


def test_立项书只给管理者和审批线上的人下载(client, admin, db, liaison, take_student):
    lin = student_row(db, LIAISON_CORE)
    aid = make_draft(client, admin)
    assert _submit(client, admin, aid, liaison.id).status_code == 200
    fid = _files(client, admin, aid)["proposal"]["id"]
    path = f"/api/materials/{fid}"

    assert client.get(dl_url(client, admin, path)).status_code == 200
    head = login(client, LIAISON_HEAD)
    assert client.get(dl_url(client, head, path)).status_code == 200
    # 不在班的骨干看不到；上了班就看得到
    core = login(client, LIAISON_CORE)
    assert client.get(dl_url(client, core, path)).status_code == 404
    assert client.get(f"/api/staff/activities/{aid}/project-files",
                      headers=core).status_code == 403
    assert client.post(f"/api/staff/duty/{liaison.id}", headers=head,
                       json={"user_id": lin.id}).status_code == 200
    assert client.get(dl_url(client, core, path)).status_code == 200
    assert _files(client, core, aid)["can_upload_proposal"] is False
    # 学生看不到，活动公开了也看不到
    student = login(client, take_student())
    assert client.get(dl_url(client, student, path)).status_code == 404
    assert client.post(f"/api/staff/activities/{aid}/decide", headers=head,
                       json={"approve": True}).status_code == 200
    assert client.get(dl_url(client, student, path)).status_code == 404
    # 学习材料的删除接口删不掉立项书
    assert client.delete(f"/api/staff/materials/{fid}", headers=admin).status_code == 404


# ======================= 盖章立项书 =======================

def _external_due(db, liaison) -> Activity:
    """对外联络部归口、已结束、核准过、申诉期已满、没有申诉的活动。"""
    admin_user = student_row(db, ADMIN)
    act = Activity(id=uid("act"), title=f"对外结账 {uid('t')}", organizer="x",
                   status="finished", approval_dept_id=liaison.id,
                   hours_approved_at=now() - timedelta(hours=2),
                   appeal_deadline=now() - timedelta(minutes=5),
                   created_by=admin_user.id)
    db.add(act)
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=admin_user.id, added_by=admin_user.id))
    db.commit()
    return act


def test_盖章立项书只用于对外活动(client, admin, db, liaison):
    aid = make_draft(client, admin)          # 还没归口任何部门
    r = _upload(client, admin, aid, "stamped")
    assert r.status_code == 409 and "对外联络部" in r.json()["detail"]
    d = _files(client, admin, aid)
    assert d["external"] is False and d["can_upload_stamped"] is False


def test_手动报批时长要盖章立项书(client, admin, db, liaison):
    act = _external_due(db, liaison)
    url = f"/api/staff/activities/{act.id}/hours/lock-request"
    assert client.get(f"/api/staff/activities/{act.id}/hours",
                      headers=admin).json()["stamped_missing"] is True
    r = client.post(url, headers=admin, json={})
    assert r.status_code == 400 and "盖章立项书" in r.json()["detail"]
    assert _upload(client, admin, act.id, "stamped", "盖章v1.pdf").status_code == 200
    assert _upload(client, admin, act.id, "stamped", "盖章v2.pdf").status_code == 200
    assert _files(client, admin, act.id)["stamped"]["title"] == "盖章v2.pdf"
    assert client.post(url, headers=admin, json={}).status_code == 200
    # 审批期间不能换
    r = _upload(client, admin, act.id, "stamped")
    assert r.status_code == 409 and "审核期间" in r.json()["detail"]
    # 锁定之后也不能换
    head = login(client, LIAISON_HEAD)
    req = (db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).one())
    assert client.post(f"/api/staff/hours-requests/{req.id}/decide", headers=head,
                       json={"approve": True}).status_code == 200
    r = _upload(client, admin, act.id, "stamped")
    assert r.status_code == 409 and "已锁定" in r.json()["detail"]


def test_自动报批缺盖章立项书就停下提醒一次(client, admin, db, liaison):
    act = _external_due(db, liaison)
    title = f"请上传盖章立项书｜{act.title}"

    def requests():
        db.expire_all()
        return db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).count()

    scheduled.submit_lock_when_due(db)
    assert requests() == 0
    assert len(inbox(db, ADMIN, title)) == 1
    # 下一轮不再提醒
    scheduled.submit_lock_when_due(db)
    assert requests() == 0
    assert len(inbox(db, ADMIN, title)) == 1

    # 补传之后下一轮照常自动提交，也不再多发提醒
    assert _upload(client, admin, act.id, "stamped").status_code == 200
    scheduled.submit_lock_when_due(db)
    assert requests() == 1
    assert len(inbox(db, ADMIN, title)) == 1
    assert inbox(db, LIAISON_HEAD, f"时长锁定审批｜{act.title}")


def test_非对外活动自动报批不受影响(client, admin, db, liaison):
    act = _external_due(db, liaison)
    act.approval_dept_id = db.query(Department).filter(
        Department.name == "办公室").one().id
    db.commit()
    scheduled.submit_lock_when_due(db)
    db.expire_all()
    assert db.query(HoursRequest).filter(HoursRequest.activity_id == act.id).count() == 1
    assert not (db.query(Notification)
                .filter(Notification.title.startswith(f"请上传盖章立项书｜{act.title}"))
                .count())


def test_删草稿连立项书一起删(client, admin, db, liaison):
    aid = make_draft(client, admin)
    stored = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                         Attachment.owner_id == aid).one().stored_name
    client.post(f"/api/staff/activities/{aid}/vb-registrations", headers=admin,
                json={"vb_name": "建错了"})
    assert client.delete(f"/api/staff/activities/{aid}", headers=admin).status_code == 200
    db.expire_all()
    assert not db.query(Attachment).filter(Attachment.owner_id == aid).count()
    assert not (MATERIAL_DIR / stored).exists()
