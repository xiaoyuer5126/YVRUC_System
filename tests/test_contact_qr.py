"""志愿者联络群二维码：文件落在挂载卷里，读图要过鉴权。

2026-10-13 的丢文件事故：二维码的目录由 staff_activities.py 自己拼（少了一层），
落进了打进镜像的代码目录里——容器一重建，图就没了，而活动里存着的地址还在。
这组用例把那次的两个要害钉住：

  · 上传用的目录只能来自 config 的常量，且必须落在一个挂载卷上；
  · 读图不能匿名（管理者和报上名的志愿者除外），也不再有一条 /api/media 直链。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from backend import config
from backend.models import Attachment, Enrollment, now, uid
from conftest import login, student_row

PNG = b"\x89PNG\r\n\x1a\n" + "用例用的二维码图".encode()


def _upload(client, headers, data: bytes = PNG, name: str = "群二维码.png"):
    r = client.post("/api/staff/upload-image", headers=headers,
                    files={"file": (name, data, "image/png")})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _att_id(url: str) -> str:
    return url.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------- 目录

def test_上传目录等于_config_的常量且不在代码目录里():
    """这次事故的根子：业务文件自己拼路径，还拼错了一层。

    目录只能有一个来源（config.MATERIAL_DIR），它必须由 DATA_DIR 派生，
    而 DATA_DIR 不能落在代码目录里面——代码是打进镜像的，重建容器就没了。
    """
    code_dir = Path(config.__file__).resolve().parent
    assert config.MATERIAL_DIR == config.DATA_DIR / "files"
    assert config.MATERIAL_DIR.is_relative_to(config.DATA_DIR)
    assert not config.DATA_DIR.is_relative_to(code_dir)


def test_数据目录落在代码目录里就直接报错():
    """config 的启动检查：真算到代码目录里了就直接报错，不静默丢文件。"""
    code_dir = Path(config.__file__).resolve().parent
    with pytest.raises(RuntimeError, match="代码目录"):
        config.check_data_dir(code_dir / "data", code_dir)
    config.check_data_dir(config.DATA_DIR, code_dir)   # 现在这个是好的，不抛


def test_compose_把_data_下每个目录都挂上了卷():
    """挂载表是 compose 说了算，config 里的每个数据目录都得对着一条挂载，
    否则又是「写进容器可写层」那一类丢文件。"""
    text = (config.ROOT_DIR / "docker-compose.yml").read_text()
    mounts = dict(re.findall(r"^\s*-\s+([\w.-]+):(/app/data/[\w.-]+)\s*$",
                             text, re.M))          # 卷名 → 容器里的挂载点
    assert set(mounts.values()) == {"/app/data/files", "/app/data/tiles",
                                    "/app/data/oplog"}
    for d in (config.MATERIAL_DIR, config.TILE_CACHE_DIR, config.OPLOG_DIR):
        target = "/app/data/" + str(d.relative_to(config.DATA_DIR))
        assert target in mounts.values(), f"{d} 没有对应的挂载"
    # 上传件只有一个卷：备份额外挂了它（目录名沿用 materials，老备份才恢复得了）
    files_vol = next(v for v, t in mounts.items() if t == "/app/data/files")
    assert f"{files_vol}:/data/materials" in text
    # 合并掉的那个卷不该再出现
    assert "yvruc_uploads" not in text


# ---------------------------------------------------------------- 上传与读回

def test_上传的二维码落在挂载卷里_能原样读回(client, admin, db):
    url = _upload(client, admin)
    row = db.get(Attachment, _att_id(url))
    assert row is not None and row.owner_type == "activity_qr"

    path = config.MATERIAL_DIR / row.stored_name
    assert path.is_file() and path.read_bytes() == PNG
    # 代码目录下不该有这个文件——事故就是它落在了那里
    assert not list((config.ROOT_DIR / "backend").rglob(row.stored_name))

    r = client.get(url, headers=admin)
    assert r.status_code == 200, r.text
    assert r.content == PNG
    assert r.headers["content-type"].startswith("image/png")


def test_只收图片_且单份不超过_2MB(client, admin):
    r = client.post("/api/staff/upload-image", headers=admin,
                    files={"file": ("x.png", b"<html>", "image/png")})
    assert r.status_code == 400 and "不是图片" in r.json()["detail"]
    r = client.post("/api/staff/upload-image", headers=admin,
                    files={"file": ("x.pdf", b"%PDF-1.4", "application/pdf")})
    assert r.status_code == 400
    r = client.post("/api/staff/upload-image", headers=admin,
                    files={"file": ("big.png", PNG + b"\x00" * (2 << 20),
                                    "image/png")})
    assert r.status_code == 400 and "2MB" in r.json()["detail"]


# ---------------------------------------------------------------- 鉴权

def test_没人登录读不到(client, admin):
    url = _upload(client, admin)
    assert client.get(url).status_code == 401


def test_别的管理者也读不到(client, admin, initiator):
    """有管理身份、但不管这个活动的人，和陌生人一样只看到 404。"""
    url = _upload(client, admin)
    r = client.get(url, headers=initiator)
    assert r.status_code == 404 and "不存在" in r.json()["detail"]


def test_活动管理者和报上名的志愿者读得到(client, admin, db, open_group,
                                            take_student):
    act, g = open_group(5)
    act.contact_group_qr = _upload(client, admin)
    db.commit()

    assert client.get(act.contact_group_qr, headers=admin).status_code == 200

    sid = take_student()
    me = login(client, sid)
    # 没报名的人读不到
    assert client.get(act.contact_group_qr, headers=me).status_code == 404

    db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                      user_id=student_row(db, sid).id,
                      position_id=g.test_position_id, status="enrolled",
                      joined_at=now()))
    db.commit()
    assert client.get(act.contact_group_qr, headers=me).status_code == 200


def test_退了选就读不到(client, admin, db, open_group, take_student):
    act, g = open_group(5)
    act.contact_group_qr = _upload(client, admin)
    sid = take_student()
    me = login(client, sid)
    enr = Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                     user_id=student_row(db, sid).id,
                     position_id=g.test_position_id, status="enrolled",
                     joined_at=now())
    db.add(enr)
    db.commit()
    assert client.get(act.contact_group_qr, headers=me).status_code == 200

    enr.status = "cancelled"
    db.commit()
    assert client.get(act.contact_group_qr, headers=me).status_code == 404


def test_传了还没保存的图_只有传的人看得到(client, admin, initiator):
    """表单里先传图后保存：这时还没有活动指向它，只给传的人预览。"""
    url = _upload(client, admin)
    assert client.get(url, headers=admin).status_code == 200
    assert client.get(url, headers=initiator).status_code == 404


def test_别的附件不走这条路(client, admin, db):
    """contact-qr 只认二维码；拿一条别的附件（问卷附件、证明材料）的 id
    来读，也只能是 404。"""
    row = Attachment(id=uid("att"), owner_type="application", owner_id="",
                     activity_id="", kind="file", title="立项书.pdf",
                     stored_name="whatever.pdf", size=1, uploaded_by="")
    db.add(row)
    db.commit()
    assert client.get(f"/api/staff/contact-qr/{row.id}",
                      headers=admin).status_code == 404


def test_文件真丢了就说重新上传(client, admin, db):
    """卷被换掉、文件没跟着来时，别回一个空白图：说清要重新上传。"""
    url = _upload(client, admin)
    row = db.get(Attachment, _att_id(url))
    (config.MATERIAL_DIR / row.stored_name).unlink()
    r = client.get(url, headers=admin)
    assert r.status_code == 404 and "重新上传" in r.json()["detail"]


def test_匿名直链_media_已经没了(client):
    """老的那条 /api/media/{name} 是被这次事故掩盖过的匿名口子，删掉不许再开。"""
    assert client.get("/api/media/x.png").status_code == 404
