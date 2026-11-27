"""上传文件按内容去重：同内容只存一份；没人用的文件留满保留期、而且库里登记过
才由定时任务删，绝不误删。

每个用例传的内容都带一个随机串：同内容只存一个文件，和别的用例撞上的话，
「文件删没删」就验不准了。
"""
from __future__ import annotations

import hashlib
import io
import threading
import time
from datetime import timedelta
from uuid import uuid4

from backend import config, filestore
from backend.database import SessionLocal
from backend.models import Attachment, StoredFile, now, uid
from conftest import dl_url, editing, file_released, login, make_draft, mk_post


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


def _age(db, name, days):
    """把「从哪天起没人用」往前拨 days 天，模拟时间过去。"""
    db.expire_all()
    row = db.get(StoredFile, name)
    row.orphaned_at = now() - timedelta(days=days)
    db.commit()


# ---------------------------------------------------------------- 去重

def test_同内容上传两次只存一份_返回里看不出命中(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    data = _content()
    first = _upload_material(client, admin, pid, data, "第一次.pdf")
    second = _upload_material(client, admin, pid, data, "第二次.pdf")

    a, b = db.get(Attachment, first["id"]), db.get(Attachment, second["id"])
    assert a.id != b.id and a.stored_name == b.stored_name
    assert a.stored_name == f"{hashlib.sha256(data).hexdigest()}.pdf"
    # 磁盘上就这一个文件，文件表里登记一行、有人在用
    assert [p.name for p in config.MATERIAL_DIR.glob(f"{a.stored_name[:64]}*")] \
        == [a.stored_name]
    row = db.get(StoredFile, a.stored_name)
    assert row is not None and row.orphaned_at is None and row.size == len(data)
    # 返回的字段一模一样，不透露「这份内容平台上已经有了」
    assert set(first) == set(second) == {"id", "title", "size"}


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

    other = login(client, take_student())
    assert _download(client, other, mat).status_code == 200
    assert _download(client, other, ans).status_code == 404

    _delete_material(client, admin, mat)
    r = _download(client, owner, ans)
    assert r.status_code == 200 and r.content == data
    assert not file_released(db, _path(db, ans).name)


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
    assert copied.stored_name == stored
    assert len(list(config.MATERIAL_DIR.iterdir())) == files_before

    _delete_material(client, admin, mat)
    assert not file_released(db, stored)
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
    assert not file_released(db, stored)


def test_换立项书传的还是同一份内容_文件不丢(client, admin, db):
    """「换」是先删旧记录再建新记录，两条指着同一个文件：不能进保留期。"""
    aid = make_draft(client, admin, proposal=False)
    url = f"/api/staff/activities/{aid}/project-files/proposal"
    data = _content()
    for name in ("v1.pdf", "v2.pdf"):
        r = client.post(url, headers=admin,
                        files={"file": (name, data, "application/pdf")})
        assert r.status_code == 200, r.text
    row = db.query(Attachment).filter(Attachment.owner_type == "proposal",
                                      Attachment.owner_id == aid).one()
    assert row.title == "v2.pdf" and not file_released(db, row.stored_name)
    r = _download(client, admin, row.id)
    assert r.status_code == 200 and r.content == data


# ---------------------------------------------------------------- 保留期与定时清理

def test_全删掉先进保留期_满7天定时任务才删(client, admin, db, enrolled_activity,
                                   take_student):
    _act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    data = _content()
    a = _upload_material(client, admin, pid, data)["id"]
    b = _upload_material(client, admin, pid, data)["id"]
    path = _path(db, a)
    name = path.name

    _delete_material(client, admin, a)
    assert not file_released(db, name)
    r = _download(client, login(client, take_student()), b)
    assert r.status_code == 200 and r.content == data

    _delete_material(client, admin, b)
    assert file_released(db, name) and path.is_file()

    _age(db, name, config.FILE_KEEP_DAYS - 1)
    filestore.sweep(db)
    assert path.is_file()

    _age(db, name, config.FILE_KEEP_DAYS + 1)
    assert filestore.sweep(db) >= 1
    db.expire_all()
    assert not path.exists() and db.get(StoredFile, name) is None


def test_没人用两天后又有人传同一份_不会被删(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    data = _content()
    a = _upload_material(client, admin, pid, data)["id"]
    path = _path(db, a)
    name = path.name
    _delete_material(client, admin, a)
    _age(db, name, 2)

    b = _upload_material(client, admin, pid, data)["id"]
    db.expire_all()
    assert db.get(StoredFile, name).orphaned_at is None
    # 再过 5 天（距第一次没人用满 7 天）也不删
    filestore.sweep(db)
    assert path.is_file()

    # 之后再没人用，保留期从这一次重新算
    _delete_material(client, admin, b)
    db.expire_all()
    assert now() - db.get(StoredFile, name).orphaned_at < timedelta(minutes=5)
    filestore.sweep(db)
    assert path.is_file()


def test_倒计时漏清了_定时任务删之前再数一遍(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    mat = _upload_material(client, admin, shift.positions[0].id, _content())["id"]
    path = _path(db, mat)
    _age(db, path.name, config.FILE_KEEP_DAYS + 30)      # 有人在用却被记成没人用
    filestore.sweep(db)
    db.expire_all()
    assert path.is_file()
    assert db.get(StoredFile, path.name).orphaned_at is None


def test_库里没登记的文件永远不删(db):
    """数据库连错（空库、测试库）时文件表里一行也没有：什么都不该删。"""
    name = f"{uuid4().hex}.pdf"
    config.MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    path = config.MATERIAL_DIR / name
    path.write_bytes(b"%PDF not registered")
    try:
        filestore.sweep(db)
        assert path.is_file()
    finally:
        path.unlink(missing_ok=True)


def test_文件表里的怪名字删不到目录外(db):
    outside = config.MATERIAL_DIR.parent / f"{uuid4().hex}.pdf"
    outside.write_bytes(b"keep me")
    try:
        db.add(StoredFile(name=f"../{outside.name}", size=1,
                          orphaned_at=now() - timedelta(days=365)))
        db.commit()
        filestore.sweep(db)
        assert outside.is_file()
    finally:
        outside.unlink(missing_ok=True)


# 所有会存文件的 owner_type。新加一种文件引用就加到这里，确认清理不会误删它
OWNER_TYPES = ("position", "application", "notify", "proposal", "stamped",
               "closing", "template", "feedback", "claim_proof", "claim_photo",
               "activity_qr")


def test_各类文件记录在用的文件_清理和删除都不碰(client, admin, db, enrolled_activity):
    """每种文件记录都和一份没人要的问卷附件、一份没发出去的通知附件共用文件。
    两个附件清理删掉那两条没人要的记录、学习材料也删掉之后，文件不进保留期；
    即使有人把它记成没人用满了保留期，定时任务也不删。"""
    from backend.scheduled import (purge_orphan_attachments, purge_unsent_notify_files,
                                   purge_unused_files)
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
    stale = [Attachment(id=uid("att"), owner_type="application", owner_id="nobody",
                        activity_id=act.id, kind="file", title="没人要.pdf",
                        stored_name=stored, size=1, uploaded_by="", created_at=old),
             Attachment(id=uid("att"), owner_type="notify", owner_id=act.id,
                        activity_id=act.id, kind="file", title="没发.pdf",
                        stored_name=stored, size=1, uploaded_by="", created_at=old)]
    db.add_all(stale)
    db.commit()
    stale_ids = [r.id for r in stale]

    purge_orphan_attachments(db)
    purge_unsent_notify_files(db)
    _delete_material(client, admin, mat)
    db.expire_all()
    assert all(db.get(Attachment, i) is None for i in stale_ids)
    assert all(db.get(Attachment, i) is not None for i in keep)
    assert not file_released(db, stored)

    _age(db, stored, config.FILE_KEEP_DAYS + 1)
    purge_unused_files(db)
    assert path.is_file()

    filestore.drop_attachments(db, [db.get(Attachment, i) for i in keep])
    db.commit()
    assert file_released(db, stored)


# ---------------------------------------------------------------- 删岗位、删草稿

def test_删草稿里的岗位_材料记录一起删_文件进保留期(client, admin, db):
    aid = make_draft(client, admin, proposal=False)
    r = mk_post(client, admin, aid, name="另一个岗位")
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    mat = _upload_material(client, admin, pid, _content())["id"]
    stored = _path(db, mat).name

    r = client.delete(f"/api/staff/posts/{pid}", headers=admin)
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.get(Attachment, mat) is None
    assert file_released(db, stored)


def test_编辑态删岗位_保存发布时才清材料记录(client, admin, db, enrolled_activity):
    """编辑态里删了岗位还能放弃改动、按原编号还原，材料得还挂在上面；
    保存发布之后才清。"""
    act, shift, _u = enrolled_activity
    pid = shift.positions[0].id
    mat = _upload_material(client, admin, pid, _content())["id"]
    stored = _path(db, mat).name

    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/posts/{pid}/copy", headers=admin,
                        json={"name": "留下的岗位"})
        assert r.status_code == 200, r.text
        r = client.delete(f"/api/staff/posts/{pid}?force=1", headers=admin)
        assert r.status_code == 200, r.text
        db.expire_all()
        assert db.get(Attachment, mat) is not None
    db.expire_all()
    assert db.get(Attachment, mat) is None
    # 复制出来的岗位还用着这个文件
    assert not file_released(db, stored)


def test_删活动草稿_挂在活动上的文件记录都删掉(client, admin, db):
    aid = make_draft(client, admin, proposal=False)
    pid = mk_post(client, admin, aid, name="又一个岗位").json()["id"]
    mat = _upload_material(client, admin, pid, _content())["id"]
    stored = _path(db, mat).name
    assert client.delete(f"/api/staff/activities/{aid}",
                         headers=admin).status_code == 200
    db.expire_all()
    assert not db.query(Attachment).filter(Attachment.activity_id == aid).count()
    assert file_released(db, stored)


# ---------------------------------------------------------------- 回滚与并发

def test_存入后事务回滚_文件按没人用登记(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    db.rollback()
    assert (config.MATERIAL_DIR / name).is_file()
    assert file_released(db, name)


def test_存入后事务回滚_别人在用的文件不动(client, admin, db, enrolled_activity):
    _act, shift, _u = enrolled_activity
    data = _content()
    mat = _upload_material(client, admin, shift.positions[0].id, data)["id"]
    path = _path(db, mat)
    db.commit()
    assert filestore.store(db, data, "pdf") == path.name
    db.rollback()
    assert path.is_file() and not file_released(db, path.name)


def test_删除方要等正在共用这个文件的上传提交后再数(client, admin, db,
                                       enrolled_activity):
    """上传命中已有文件、还没提交时，另一边删掉了最后一条旧记录：删除方数引用
    之前要等上传提交，数到新记录，不进保留期。"""
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
                filestore.drop_attachments(s, [s.get(Attachment, mat)])
                s.commit()            # 提交后数引用：要等 uploader 放锁
            finally:
                s.close()

        t = threading.Thread(target=delete_last)
        t.start()
        time.sleep(1.0)
        assert t.is_alive(), "删除方没有等上传方的锁"
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
    assert not file_released(db, path.name)


def test_定时清理要等正在共用这个文件的上传提交后再数(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    db.rollback()                                   # 没人用，进保留期
    _age(db, name, config.FILE_KEEP_DAYS + 1)

    uploader = SessionLocal()
    try:
        filestore.store(uploader, data, "pdf")      # 拿着锁，没提交
        def run_sweep():
            s = SessionLocal()
            try:
                filestore.sweep(s)
            finally:
                s.close()

        t = threading.Thread(target=run_sweep)
        t.start()
        time.sleep(1.0)
        assert t.is_alive(), "定时清理没有等上传方的锁"
        uploader.add(Attachment(id=uid("att"), owner_type="feedback", owner_id="fb",
                                activity_id="", kind="file", title="截图.pdf",
                                stored_name=name, size=len(data), uploaded_by=""))
        uploader.commit()
        t.join(timeout=30)
        assert not t.is_alive()
    finally:
        uploader.close()
    assert (config.MATERIAL_DIR / name).read_bytes() == data
    assert not file_released(db, name)


def test_文件已被清掉_随后的上传会重新写一份(db):
    data = _content()
    name = filestore.store(db, data, "pdf")
    db.rollback()
    _age(db, name, config.FILE_KEEP_DAYS + 1)
    filestore.sweep(db)
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
