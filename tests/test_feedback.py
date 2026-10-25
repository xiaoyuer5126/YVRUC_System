"""意见反馈：提交、限频、管理员处理与通知、截图的类型与下载权限。"""
from __future__ import annotations

from backend import config
from backend.models import AuditLog, Attachment
from conftest import INITIATOR, dl_url, inbox, login

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


def _submit(client, headers, content="页面按钮点了没反应", files=None):
    return client.post("/api/feedback", headers=headers,
                       data={"content": content}, files=files or [])


def test_提交成功且本人看得到(client, take_student, db):
    me = login(client, take_student())
    r = _submit(client, me, "报名页在手机上排版错位",
                files=[("files", ("a.png", PNG, "image/png")),
                       ("files", ("b.jpg", JPG, "image/jpeg"))])
    assert r.status_code == 200, r.text
    fb = r.json()
    assert fb["status"] == "open" and len(fb["images"]) == 2

    mine = client.get("/api/feedback/mine", headers=me).json()
    assert [x["id"] for x in mine] == [fb["id"]]
    assert mine[0]["content"] == "报名页在手机上排版错位"
    # 留了审计
    assert db.query(AuditLog).filter(AuditLog.target_type == "feedback",
                                     AuditLog.target_id == fb["id"]).count() == 1


def test_内容必填与超长(client, take_student):
    me = login(client, take_student())
    assert _submit(client, me, "   ").status_code == 400
    too_long = "字" * (config.FEEDBACK_MAX_CHARS + 1)
    assert _submit(client, me, too_long).status_code == 400
    assert _submit(client, me, "字" * config.FEEDBACK_MAX_CHARS).status_code == 200


def test_截图类型与张数限制(client, take_student, db):
    me = login(client, take_student())
    # 不是图片的扩展名
    r = _submit(client, me, files=[("files", ("x.pdf", b"%PDF-1.4", "application/pdf"))])
    assert r.status_code == 400
    # 扩展名是图片、内容不是
    r = _submit(client, me, files=[("files", ("x.png", b"<html>", "image/png"))])
    assert r.status_code == 400
    # 超过 3 张
    r = _submit(client, me, files=[("files", (f"{i}.png", PNG, "image/png"))
                                   for i in range(config.FEEDBACK_MAX_IMAGES + 1)])
    assert r.status_code == 400
    # 被拒的请求什么都没落库
    assert client.get("/api/feedback/mine", headers=me).json() == []


def test_频率限制(client, take_student):
    me = login(client, take_student())
    for i in range(config.FEEDBACK_PER_HOUR):
        assert _submit(client, me, f"第 {i} 条").status_code == 200
    r = _submit(client, me, "再来一条")
    assert r.status_code == 429
    assert len(client.get("/api/feedback/mine", headers=me).json()) == config.FEEDBACK_PER_HOUR


def test_限频先占后判(client, take_student, monkeypatch):
    """先自增再比：上限 1 时第 2 条进来计数已经是 2，直接 429。

    先判后占的话两个请求会双双看到「还没到上限」，一起写进去。
    """
    monkeypatch.setattr(config, "FEEDBACK_PER_HOUR", 1)
    me = login(client, take_student())
    assert _submit(client, me, "第一条").status_code == 200
    assert _submit(client, me, "第二条").status_code == 429
    assert len(client.get("/api/feedback/mine", headers=me).json()) == 1


def test_非管理员看不到全部反馈(client, take_student, admin):
    me = login(client, take_student())
    fid = _submit(client, me).json()["id"]
    assert client.get("/api/staff/feedback", headers=me).status_code == 403
    assert client.get("/api/staff/feedback",
                      headers=login(client, INITIATOR)).status_code == 403
    assert client.post(f"/api/staff/feedback/{fid}/handle", headers=me,
                       json={"reply": "x"}).status_code == 403
    # 管理员切到别的身份视角也不行：权限跟着当前身份走
    as_initiator = {**admin, "X-Identity": "initiator"}
    assert client.get("/api/staff/feedback", headers=as_initiator).status_code == 403
    # 管理员能看
    r = client.get("/api/staff/feedback?status=open", headers=admin)
    assert r.status_code == 200
    assert fid in [x["id"] for x in r.json()["items"]]


def test_管理员处理后提交人收到通知且看到回复(client, take_student, admin, db):
    sid = take_student()
    me = login(client, sid)
    fid = _submit(client, me, "导出按钮报错").json()["id"]

    r = client.post(f"/api/staff/feedback/{fid}/handle", headers=admin,
                    json={"reply": "已修复，请刷新后再试"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "handled"

    notes = inbox(db, sid, "意见反馈已处理")
    assert len(notes) == 1 and "已修复，请刷新后再试" in notes[0].content
    # 点开要能落到「我的反馈」：站内和企业微信用同一个带查询串的地址
    assert notes[0].data["route"] == "feedback_mine"
    msgs = client.get("/api/me/notifications", headers=me).json()["items"]
    note = [m for m in msgs if m["title"] == "意见反馈已处理"][0]
    assert note["page"] == "/messages?feedback=mine"

    mine = client.get("/api/feedback/mine", headers=me).json()
    assert mine[0]["status"] == "handled"
    assert mine[0]["reply"] == "已修复，请刷新后再试"

    # 已处理的不在待处理筛选里
    open_ids = [x["id"] for x in client.get("/api/staff/feedback?status=open&size=100",
                                            headers=admin).json()["items"]]
    assert fid not in open_ids
    assert db.query(AuditLog).filter(AuditLog.target_id == fid).count() == 2


def test_截图下载权限(client, take_student, admin, db):
    me = login(client, take_student())
    other = login(client, take_student())
    fb = _submit(client, me, files=[("files", ("s.png", PNG, "image/png"))]).json()
    path = fb["images"][0]["url"]

    # 本人：带头与换票直链都能看
    r = client.get(path, headers=me)
    assert r.status_code == 200 and r.content == PNG
    assert r.headers["content-type"] == "image/png"
    assert client.get(dl_url(client, me, path)).status_code == 200
    # 管理员能看
    assert client.get(dl_url(client, admin, path)).status_code == 200
    # 别的用户、非管理员身份、未登录都不行
    assert client.get(path, headers=other).status_code == 404
    assert client.get(path, headers=login(client, INITIATOR)).status_code == 404
    assert client.get(path).status_code == 401
    # 通用的材料下载接口不给反馈截图：拿到 id 也绕不过上面这套权限
    att_id = path.rsplit("/", 1)[-1]
    assert client.get(dl_url(client, other, f"/api/materials/{att_id}")).status_code == 404
    # 下载接口只认反馈截图，别的附件 id 走不通
    att = db.query(Attachment).filter(Attachment.owner_type != "feedback").first()
    if att is not None:
        assert client.get(f"/api/feedback/files/{att.id}",
                          headers=admin).status_code == 404


def _admin_notes(client, headers, fid):
    items = client.get("/api/me/notifications", headers=headers).json()["items"]
    return [n for n in items if (n["data"] or {}).get("feedback_id") == fid]


def test_新反馈通知管理员且点开进意见反馈页(client, take_student, admin, initiator):
    sid = take_student()
    fid = _submit(client, login(client, sid), "导出的表格少了一列" * 10).json()["id"]

    notes = _admin_notes(client, admin, fid)
    assert len(notes) == 1
    n = notes[0]
    assert n["title"] == "新的意见反馈"
    assert sid in n["content"] and "导出的表格少了一列" in n["content"]
    assert n["content"].endswith("…」")          # 正文是摘要，不是全文
    assert n["page"] == "/feedback"
    # 活动发起方不是管理员，收不到
    assert _admin_notes(client, initiator, fid) == []


def test_管理员自己提的反馈不通知自己(client, admin):
    fid = _submit(client, admin, "管理员自己发现的问题").json()["id"]
    assert _admin_notes(client, admin, fid) == []


def test_通知失败不影响提交(client, take_student, admin, monkeypatch):
    from backend.routers import feedback

    def boom(*_a, **_kw):
        raise RuntimeError("消息中心挂了")
    monkeypatch.setattr(feedback, "notify", boom)
    me = login(client, take_student())
    r = _submit(client, me, "通知发不出去时也要能提交")
    assert r.status_code == 200, r.text
    fid = r.json()["id"]
    assert [x["id"] for x in client.get("/api/feedback/mine", headers=me).json()] == [fid]
    assert _admin_notes(client, admin, fid) == []
