"""上传文件按内容去重：同内容只存一份，没有记录再指着它才删文件，绝不误删。

每个用例传的内容都带一个随机串：同内容只存一个文件，和别的用例撞上的话，
「文件删没删」就验不准了。
"""
from __future__ import annotations

import hashlib
import io
import threading
import time
from datetime import timedelta

import pytest

from backend import config, filestore
from backend.database import SessionLocal
from backend.models import Attachment, now, uid
from conftest import dl_url, editing, login


def _content() -> bytes:
    return f"%PDF-1.4 {uid('t')}".encode()


def _upload_material(client, headers, position_id, data, name="手册.pdf"):
    r = client.post("/api/staff/materials", headers=headers,
                    data={"owner_type": "position", "owner_id": position_id,
                          "title": name},
                    files={"file": (name, io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def _download(client, headers, att_id):
    return client.get(dl_url(client, headers, f"/api/materials/{att_id}"))


def _path(db, att_id):
    db.expire_all()
    return config.MATERIAL_DIR / db.get(Attachment, att_id).stored_name


def _delete_material(client, headers, att_id):
    r = client.delete(f"/api/staff/materials/{att_id}", headers=headers)
    assert r.status_code == 200, r.text


def test_同内容上传两次只存一份_返回里看不出命中(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    data = _content()
    first = _upload_material(client, admin, pid, data, "第一次.pdf")
    second = _upload_material(client, admin, pid, data, "第二次.pdf")

    a, b = db.get(Attachment, first["id"]), db.get(Attachment, second["id"])
    assert a.id != b.id and a.stored_name == b.stored_name
    assert a.stored_name == f"{hashlib.sha256(data).hexdigest()}.pdf"
    # 磁盘上就这一个文件
    assert [p.name for p in config.MATERIAL_DIR.glob(f"{a.stored_name[:64]}*")] \
        == [a.stored_name]
    # 返回的字段一模一样，不透露「这份内容平台上已经有了」
    assert set(first) == set(second) == {"id", "title", "size"}
    assert first["size"] == second["size"] == len(data)


def test_删一条另一条仍能下载_全删掉文件才清(client, admin, db, enrolled_activity,
                                    take_student):
    _act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    data = _content()
    a = _upload_material(client, admin, pid, data)["id"]
    b = _upload_material(client, admin, pid, data)["id"]
    path = _path(db, a)

    _delete_material(client, admin, a)
    assert path.is_file()
    stu = login(client, take_student())
    r = _download(client, stu, b)
    assert r.status_code == 200 and r.content == data

    _delete_material(client, admin, b)
    assert not path.exists()


def test_不同入口传同一份内容_各自鉴权互不牵连(client, admin, db, enrolled_activity,
                                    take_student):
    """学习材料和志愿者问卷附件内容相同：共用一个文件，但问卷附件仍只给本人和
    管理者看；删掉学习材料，问卷附件照样打得开。"""
    act, shift, _u = enrolled_activity
    data = _content()
    mat = _upload_material(client, admin, shift.positions[0].id, data)["id"]
    owner = login(client, take_student())
    r = client.post(f"/api/activities/{act.id}/apply/upload", headers=owner,
                    files={"file": ("证明.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    ans = r.json()["id"]
    assert _path(db, mat) == _path(db, ans)

    # 别的志愿者能下学习材料，却下不到同内容的问卷附件：权限跟着记录走
    other = login(client, take_student())
    assert _download(client, other, mat).status_code == 200
    assert _download(client, other, ans).status_code == 404

    _delete_material(client, admin, mat)
    r = _download(client, owner, ans)
    assert r.status_code == 200 and r.content == data


def test_复制岗位共用文件_删原岗位材料副本仍能下载(client, admin, db,
                                        enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    src = shift.positions[0]
    data = _content()
    mat = _upload_material(client, admin, src.id, data)["id"]
    stored = _path(db, mat).name
    files_before = len(list(config.MATERIAL_DIR.iterdir()))

    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/posts/{src.id}/copy", headers=admin,
                        json={"name": f"{src.name} 副本"})
        assert r.status_code == 200, r.text
        new_id = r.json()["id"]
    db.expire_all()
    copied = db.query(Attachment).filter(Attachment.owner_type == "position",
                                         Attachment.owner_id == new_id).one()
    # 只多了一条记录，没多文件
    assert copied.stored_name == stored
    assert len(list(config.MATERIAL_DIR.iterdir())) == files_before

    _delete_material(client, admin, mat)
    r = _download(client, login(client, take_student()), copied.id)
    assert r.status_code == 200 and r.content == data


def test_复制活动共用文件(client, admin, db, enrolled_activity):
    from conftest import upload_qr
    act, shift, _u = enrolled_activity
    data = _content()
    mat = _upload_material(client, admin, shift.positions[0].id, data)["id"]
    stored = _path(db, mat).name
    r = client.post(f"/api/staff/activities/{act.id}/copy", headers=admin,
                    json={"title": f"复制 {uid('t')}",
                          "contact_group_qr": upload_qr(client, admin)})
    assert r.status_code == 200, r.text
    db.expire_all()
    rows = db.query(Attachment).filter(Attachment.activity_id == r.json()["id"],
                                       Attachment.owner_type == "position").all()
    assert [a.stored_name for a in rows] == [stored]
    _delete_material(client, admin, mat)
    assert (config.MATERIAL_DIR / stored).is_file()


def test_换立项书传的还是同一份内容_文件不丢(client, admin, db):
    """「换」是先删旧记录再建新记录，两条指着同一个文件：不能把文件删掉。"""
    from conftest import make_draft
    aid = make_draft(client, admin, proposal=False)
    url = f"/api/staff/activities/{aid}/project-files/proposal"
    data = _content()
    for name in ("v1.pdf", "v2.pdf"):
        r = client.post(url, headers=admin,
                        files={"file": (name, data, "application/pdf")})
        assert r.status_code == 200, r.text
    row = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                      Attachment.owner_id == aid).one()
    assert row.title == "v2.pdf"
    r = _download(client, admin, row.id)
    assert r.status_code == 200 and r.content == data


# 所有会存文件的 owner_type。新加一种文件引用就加到这里，确认清理不会误删它
OWNER_TYPES = ("position", "application", "notify", "proposal", "stamped",
               "closing", "template", "feedback", "claim_proof", "claim_photo",
               "activity_qr")


def test_每日清理与删除都不误删别的记录在用的文件(client, admin, db,
                                       enrolled_activity):
    """每种文件记录都和一份没人要的问卷附件、一份没发出去的通知附件共用文件。
    两个定时清理删掉那两条没人要的记录，学习材料也删掉，别的记录都还在、文件也在；
    最后一条记录删掉时文件才清掉。"""
    from backend.scheduled import purge_orphan_attachments, purge_unsent_notify_files
    act, shift, _u = enrolled_activity
    mat = _upload_material(client, admin, shift.positions[0].id, _content())["id"]
    path = _path(db, mat)
    stored = path.name

    keep = []
    for t in OWNER_TYPES:
        row = Attachment(id=uid("att"), owner_type=t, owner_id=f"own_{t}"[:32],
                         activity_id=act.id, kind="file", title=f"{t}.pdf",
                         stored_name=stored, size=path.stat().st_size,
                         uploaded_by="")
        db.add(row)
        keep.append(row.id)
    old = now() - timedelta(days=config.ORPHAN_ATTACHMENT_DAYS + 1)
    stale_answer = Attachment(id=uid("att"), owner_type="application",
                              owner_id="nobody", activity_id=act.id, kind="file",
                              title="没人要.pdf", stored_name=stored, size=1,
                              uploaded_by="", created_at=old)
    stale_notify = Attachment(id=uid("att"), owner_type="notify", owner_id=act.id,
                              activity_id=act.id, kind="file", title="没发.pdf",
                              stored_name=stored, size=1, uploaded_by="",
                              created_at=old)
    db.add_all([stale_answer, stale_notify])
    db.commit()
    stale = (stale_answer.id, stale_notify.id)

    purge_orphan_attachments(db)
    purge_unsent_notify_files(db)
    _delete_material(client, admin, mat)
    db.expire_all()
    assert all(db.get(Attachment, i) is None for i in stale)
    assert all(db.get(Attachment, i) is not None for i in keep)
    assert path.is_file()

    for i in keep:
        row = db.get(Attachment, i)
        filestore.release(db, row.stored_name)
        db.delete(row)
    db.commit()
    assert not path.exists()


def test_存入后事务回滚_没人用的文件不留下(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    assert (config.MATERIAL_DIR / name).is_file()
    db.rollback()
    assert not (config.MATERIAL_DIR / name).exists()


def test_存入后事务回滚_别人在用的文件不动(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    data = _content()
    mat = _upload_material(client, admin, shift.positions[0].id, data)["id"]
    path = _path(db, mat)
    db.commit()
    assert filestore.store(db, data, "pdf") == path.name
    db.rollback()
    assert path.is_file()


def test_删除方提交_要等正在共用这个文件的上传提交后再数(client, admin, db,
                                         enrolled_activity):
    """上传命中已有文件、还没提交时，另一边删掉了最后一条旧记录：删除方数引用
    之前要等上传提交，数到新记录，文件不能删。"""
    act, shift, _u = enrolled_activity
    data = _content()
    mat = _upload_material(client, admin, shift.positions[0].id, data)["id"]
    path = _path(db, mat)

    uploader = SessionLocal()
    try:
        name = filestore.store(uploader, data, "pdf")     # 拿着文件锁，没提交
        assert name == path.name

        def delete_last():
            s = SessionLocal()
            try:
                row = s.get(Attachment, mat)
                filestore.release(s, row.stored_name)
                s.delete(row)
                s.commit()            # 提交后数引用：要等 uploader 放锁
            finally:
                s.close()

        t = threading.Thread(target=delete_last)
        t.start()
        time.sleep(1.0)
        assert t.is_alive(), "删除方没有等上传方的锁"
        assert path.is_file()
        uploader.add(Attachment(id=uid("att"), owner_type="position",
                                owner_id=shift.positions[0].id,
                                activity_id=act.id, kind="file", title="新的.pdf",
                                stored_name=name, size=len(data), uploaded_by=""))
        uploader.commit()
        t.join(timeout=30)
        assert not t.is_alive()
    finally:
        uploader.close()
    assert path.is_file() and path.read_bytes() == data


def test_删除方先删掉了文件_随后的上传会重新写一份(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    db.rollback()                                   # 没人用，文件被删
    assert not (config.MATERIAL_DIR / name).exists()
    assert filestore.store(db, data, "pdf") == name
    assert (config.MATERIAL_DIR / name).read_bytes() == data
    db.rollback()


def test_坏掉的同名文件会被这次上传修好(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    db.rollback()
    (config.MATERIAL_DIR / name).write_bytes(data[:5])      # 模拟被截断
    filestore.store(db, data, "pdf")
    assert (config.MATERIAL_DIR / name).read_bytes() == data
    db.rollback()


@pytest.mark.parametrize("name", ["../x.pdf", "a/b.pdf", ".tmp-x", ""])
def test_清理只认目录下一层的普通文件名(name):
    assert filestore.purge([name]) == 0
