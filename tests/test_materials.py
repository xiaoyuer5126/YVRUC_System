"""学习材料：上传限制、下载鉴权、草稿不外泄。"""
from __future__ import annotations

import io

from backend.models import Activity, Attachment
from conftest import POSITION_TEXT, dl_url, editing, login


def _position_of(shift):
    return shift.positions[0].id


def _upload(client, headers, shift, name="培训.pdf", size=1024):
    return client.post("/api/staff/materials", headers=headers,
                       data={"owner_type": "position", "owner_id": _position_of(shift),
                             "title": "岗前培训"},
                       files={"file": (name, io.BytesIO(b"x" * size),
                                       "application/pdf")})


def test_上传下载全流程(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    r = _upload(client, admin, shift)
    assert r.status_code == 200, r.text
    mid = r.json()["id"]

    # 活动详情里岗位上能看到这份材料
    detail = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert detail["shifts"][0]["positions"][0]["attachments"][0]["id"] == mid

    # 志愿者用直链下载（<a href> 带不了请求头，先换一张一次性票据）
    stu_headers = login(client, take_student())
    r = client.get(dl_url(client, stu_headers, f"/api/materials/{mid}"))
    assert r.status_code == 200
    assert r.content == b"x" * 1024


def test_没有令牌下载不了(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift).json()["id"]
    assert client.get(f"/api/materials/{mid}").status_code == 401


def test_筹备中活动的材料不外泄(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift).json()["id"]
    db.get(Activity, act.id).status = "draft"
    db.commit()

    stu = login(client, take_student())
    assert client.get(
        dl_url(client, stu, f"/api/materials/{mid}")).status_code == 404
    # 管理者自己仍然下得到
    assert client.get(
        dl_url(client, admin, f"/api/materials/{mid}")).status_code == 200


def test_超大文件与不支持的类型被拒(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    r = _upload(client, admin, shift, name="片子.mp4")
    assert r.status_code == 400 and "链接" in r.json()["detail"]

    from backend import config
    r = _upload(client, admin, shift,
                size=config.MAX_MATERIAL_BYTES + 1)
    assert r.status_code == 400 and "MB" in r.json()["detail"]


def test_非本活动管理者不能上传(client, initiator, enrolled_activity):
    _act, shift, _u = enrolled_activity
    assert _upload(client, initiator, shift).status_code == 403


def test_链接必须是http开头(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    r = client.post("/api/staff/materials/link", headers=admin,
                    json={"owner_type": "position", "owner_id": _position_of(shift),
                          "title": "培训视频", "url": "javascript:alert(1)"})
    assert r.status_code == 400


def test_删除材料会把文件一起删掉(client, admin, db, enrolled_activity):
    from backend.routers.materials import MATERIAL_DIR
    _act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift).json()["id"]
    stored = db.get(Attachment, mid).stored_name
    assert (MATERIAL_DIR / stored).is_file()

    assert client.delete(f"/api/staff/materials/{mid}",
                         headers=admin).status_code == 200
    assert not (MATERIAL_DIR / stored).exists()


def test_材料挂不到场次上(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    r = client.post("/api/staff/materials", headers=admin,
                    data={"owner_type": "shift", "owner_id": shift.id, "title": "x"},
                    files={"file": ("a.pdf", io.BytesIO(b"x"), "application/pdf")})
    assert r.status_code == 400 and "岗位" in r.json()["detail"]


def test_场次内复制岗位带上学习材料(client, admin, db, enrolled_activity):
    """复制一份再改：材料跟着走，名字自己填，同场次不会撞重名。"""
    act, shift, _u = enrolled_activity
    src = shift.positions[0]
    r = client.post("/api/staff/materials/link", headers=admin,
                    json={"owner_type": "position", "owner_id": src.id,
                          "title": "岗前须知", "url": "https://example.com/x"})
    assert r.status_code == 200, r.text
    with editing(client, admin, act.id):
        r = client.post(f"/api/staff/shifts/{shift.id}/positions", headers=admin,
                        json={"name": f"{src.name} 副本", "quota": 2,
                              "copy_from": src.id, **POSITION_TEXT})
        assert r.status_code == 200, r.text
        new_id = r.json()["id"]

    copied = db.query(Attachment).filter(Attachment.owner_type == "position",
                                         Attachment.owner_id == new_id).all()
    assert [a.title for a in copied] == ["岗前须知"]
