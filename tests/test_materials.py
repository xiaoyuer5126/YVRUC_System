"""学习材料：上传限制、下载鉴权、草稿不外泄。"""
from __future__ import annotations

import io

from backend.models import Activity, Attachment
from conftest import login


def _upload(client, headers, shift_id, name="培训.pdf", size=1024):
    return client.post("/api/staff/materials", headers=headers,
                       data={"owner_type": "shift", "owner_id": shift_id,
                             "title": "岗前培训"},
                       files={"file": (name, io.BytesIO(b"x" * size),
                                       "application/pdf")})


def test_上传下载全流程(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    r = _upload(client, admin, shift.id)
    assert r.status_code == 200, r.text
    mid = r.json()["id"]

    # 活动详情里能看到这份材料
    detail = client.get(f"/api/activities/{act.id}", headers=admin).json()
    assert detail["shifts"][0]["attachments"][0]["id"] == mid

    # 志愿者用直链下载（<a href> 带不了 Authorization 头，只能挂 ?token=）
    stu_headers = login(client, take_student())
    token = stu_headers["Authorization"].split()[1]
    r = client.get(f"/api/materials/{mid}?token={token}")
    assert r.status_code == 200
    assert r.content == b"x" * 1024


def test_没有令牌下载不了(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift.id).json()["id"]
    assert client.get(f"/api/materials/{mid}").status_code == 401


def test_筹备中活动的材料不外泄(client, admin, db, enrolled_activity, take_student):
    act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift.id).json()["id"]
    db.get(Activity, act.id).status = "draft"
    db.commit()

    token = login(client, take_student())["Authorization"].split()[1]
    assert client.get(f"/api/materials/{mid}?token={token}").status_code == 404
    # 管理者自己仍然下得到
    admin_token = admin["Authorization"].split()[1]
    assert client.get(f"/api/materials/{mid}?token={admin_token}").status_code == 200


def test_超大文件与不支持的类型被拒(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    r = _upload(client, admin, shift.id, name="片子.mp4")
    assert r.status_code == 400 and "链接" in r.json()["detail"]

    from backend import config
    r = _upload(client, admin, shift.id,
                size=config.MAX_MATERIAL_BYTES + 1)
    assert r.status_code == 400 and "MB" in r.json()["detail"]


def test_非本活动管理者不能上传(client, teacher, enrolled_activity):
    _act, shift, _u = enrolled_activity
    assert _upload(client, teacher, shift.id).status_code == 403


def test_链接必须是http开头(client, admin, enrolled_activity):
    _act, shift, _u = enrolled_activity
    r = client.post("/api/staff/materials/link", headers=admin,
                    json={"owner_type": "shift", "owner_id": shift.id,
                          "title": "培训视频", "url": "javascript:alert(1)"})
    assert r.status_code == 400


def test_删除材料会把文件一起删掉(client, admin, db, enrolled_activity):
    from backend.routers.materials import MATERIAL_DIR
    _act, shift, _u = enrolled_activity
    mid = _upload(client, admin, shift.id).json()["id"]
    stored = db.get(Attachment, mid).stored_name
    assert (MATERIAL_DIR / stored).is_file()

    assert client.delete(f"/api/staff/materials/{mid}",
                         headers=admin).status_code == 200
    assert not (MATERIAL_DIR / stored).exists()
