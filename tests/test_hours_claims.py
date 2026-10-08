"""校外时长补登：提交与校验、审批与撤销、通知、附件权限、导出。

这条账不并进 HoursRecord（校外活动没有场次/报名/签到），所以这里也顺带钉住
「不碰活动那套」：补登进出不影响任何活动的时长状态。
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import load_workbook

from backend import config
from backend.models import Attachment, AuditLog, HoursClaim
from conftest import LIAISON_HEAD, dl_url, inbox, login, student_row

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
PDF = b"%PDF-1.4\n" + b"\x00" * 64
DESC = "在社区服务中心做垃圾分类引导，上午两小时、下午两小时，负责两个投放点"


def _submit(client, headers, *, title="社区环保志愿服务", start="2026-03-05 09:00",
            end="2026-03-05 12:00", hours="3", desc=DESC, note="",
            proofs=None, photos=None):
    files = [("proofs", p) for p in (proofs if proofs is not None
                                     else [("证明.pdf", PDF, "application/pdf")])]
    files += [("photos", p) for p in (photos if photos is not None
                                      else [("现场.png", PNG, "image/png")])]
    return client.post("/api/me/hours-claims", headers=headers,
                       data={"title": title, "start_at": start, "end_at": end,
                             "hours": hours, "service_desc": desc, "note": note},
                       files=files)


def test_提交成功并通知对外联络部(client, db, liaison, take_student):
    sid = take_student()
    me = login(client, sid)
    r = _submit(client, me)
    assert r.status_code == 200, r.text
    claim = r.json()
    assert claim["status"] == "pending" and claim["hours"] == 3
    assert len(claim["files"]["proofs"]) == 1 and len(claim["files"]["photos"]) == 1
    assert claim["can_withdraw"] is True

    # 证明材料与照片挂在 Attachment 上，且不属于任何活动
    rows = (db.query(Attachment)
            .filter(Attachment.owner_id == claim["id"]).all())
    assert {x.owner_type for x in rows} == {"claim_proof", "claim_photo"}
    assert all(x.activity_id == "" for x in rows)

    # 待审批通知发给对外联络部部长（值班人优先，见 liaison.vb_contacts）
    assert len(inbox(db, LIAISON_HEAD, f"[时长补登] {claim['title']}")) == 1
    # 留痕
    assert db.query(AuditLog).filter(AuditLog.target_type == "hours_claim",
                                     AuditLog.target_id == claim["id"]).count() == 1


def test_字段校验(client, take_student):
    me = login(client, take_student())
    # 活动时间不能填将来
    assert _submit(client, me, end="2099-01-01 10:00").status_code == 400
    # 结束早于开始
    assert _submit(client, me, start="2026-03-05 12:00",
                   end="2026-03-05 09:00").status_code == 400
    # 时长：0、超过上限、不是 0.5 的倍数
    for bad in ("0", "-1", str(config.CLAIM_HOURS_MAX + 1), "1.2"):
        assert _submit(client, me, hours=bad).status_code == 400, bad
    # 名称与服务描述
    assert _submit(client, me, title="短").status_code == 400
    assert _submit(client, me, desc="太短").status_code == 400
    assert _submit(client, me, note="备注" * 200).status_code == 400
    # 证明材料与照片都必须有
    assert _submit(client, me, proofs=[]).status_code == 400
    assert _submit(client, me, photos=[]).status_code == 400
    # 材料的格式与张数
    assert _submit(client, me, proofs=[("x.png", b"<html>", "image/png")]).status_code == 400
    assert _submit(client, me, photos=[("x.pdf", PDF, "application/pdf")]).status_code == 400
    too_many = [(f"{i}.png", PNG, "image/png")
                for i in range(config.CLAIM_MAX_PHOTOS + 1)]
    assert _submit(client, me, photos=too_many).status_code == 400
    # 被拒的请求什么都没落库
    assert client.get("/api/me/hours-claims", headers=me).json()["items"] == []


def test_资料不完整不给提(client, db, take_student):
    """导出要身份证号与学院，缺了就录不进志愿北京，先让人补档案。"""
    from backend.models import User

    sid = take_student()
    u = db.query(User).filter(User.student_id == sid).one()
    u.id_card = ""
    u.no_mainland_id = False
    db.commit()
    r = _submit(client, login(client, sid))
    assert r.status_code == 400 and "档案" in r.json()["detail"]


def test_同名同天不能重复提交(client, take_student):
    me = login(client, take_student())
    assert _submit(client, me).status_code == 200
    r = _submit(client, me, hours="2")
    assert r.status_code == 400 and "已经登记过" in r.json()["detail"]
    # 隔一天、或换个名字，可以再提
    assert _submit(client, me, start="2026-03-06 09:00",
                   end="2026-03-06 12:00").status_code == 200


def test_每小时条数上限(client, take_student):
    me = login(client, take_student())
    for i in range(config.CLAIM_PER_HOUR):
        r = _submit(client, me, title=f"社区环保志愿服务第{i}场")
        assert r.status_code == 200, r.text
    r = _submit(client, me, title="再来一场")
    assert r.status_code == 429


def test_非对外联络部的人审批不了(client, db, take_student):
    me = login(client, take_student())
    cid = _submit(client, me).json()["id"]
    head = login(client, LIAISON_HEAD)
    # 本人与无关的人看不到队列，也拍不了板
    assert client.get("/api/staff/hours-claims", headers=me).status_code == 403
    assert client.post(f"/api/staff/hours-claims/{cid}/decide", headers=me,
                       json={"approve": True}).status_code == 403
    # 部长能看
    assert client.get("/api/staff/hours-claims", headers=head).status_code == 200


def test_通过可核定不一样的时长(client, db, liaison, take_student):
    sid = take_student()
    me = login(client, sid)
    cid = _submit(client, me, hours="6").json()["id"]
    head = login(client, LIAISON_HEAD)
    r = client.post(f"/api/staff/hours-claims/{cid}/decide", headers=head,
                    json={"approve": True, "hours": 4, "reply": "按服务记录核减 2 小时"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "approved" and out["counted_hours"] == 4

    assert len(inbox(db, sid, "时长补登已通过")) == 1
    mine = client.get("/api/me/hours-claims", headers=me).json()
    assert mine["counted_total"] == 4
    # 核定超上限也不行
    cid2 = _submit(client, me, title="第二场社区环保志愿服务").json()["id"]
    assert client.post(f"/api/staff/hours-claims/{cid2}/decide", headers=head,
                       json={"approve": True,
                             "hours": config.CLAIM_HOURS_MAX + 1}).status_code == 400


def test_驳回要写理由并通知本人(client, db, liaison, take_student):
    sid = take_student()
    me = login(client, sid)
    cid = _submit(client, me).json()["id"]
    head = login(client, LIAISON_HEAD)
    assert client.post(f"/api/staff/hours-claims/{cid}/decide", headers=head,
                       json={"approve": False, "reply": ""}).status_code == 400
    r = client.post(f"/api/staff/hours-claims/{cid}/decide", headers=head,
                    json={"approve": False, "reply": "证明材料看不清活动时间，请重传"})
    assert r.status_code == 200 and r.json()["status"] == "rejected"
    assert len(inbox(db, sid, "时长补登未通过")) == 1
    assert client.get("/api/me/hours-claims", headers=me).json()["counted_total"] == 0
    # 驳回后可以补充材料重新登记
    assert _submit(client, me, start="2026-03-07 09:00",
                   end="2026-03-07 12:00").status_code == 200


def test_待审批可撤回_通过后只能由外联撤销(client, db, liaison, take_student):
    sid = take_student()
    me = login(client, sid)
    cid = _submit(client, me).json()["id"]
    assert client.post(f"/api/me/hours-claims/{cid}/withdraw",
                       headers=me).status_code == 200
    assert db.query(HoursClaim).filter(HoursClaim.id == cid).count() == 0
    assert db.query(Attachment).filter(Attachment.owner_id == cid).count() == 0

    cid2 = _submit(client, me, title="第二场社区环保志愿服务").json()["id"]
    head = login(client, LIAISON_HEAD)
    assert client.post(f"/api/staff/hours-claims/{cid2}/revoke", headers=head,
                       json={"reason": "还没通过"}).status_code == 409
    client.post(f"/api/staff/hours-claims/{cid2}/decide", headers=head,
                json={"approve": True})
    assert client.post(f"/api/me/hours-claims/{cid2}/withdraw",
                       headers=me).status_code == 409
    # 撤销：写理由、通知本人、时长不再计入
    assert client.post(f"/api/staff/hours-claims/{cid2}/revoke", headers=head,
                       json={"reason": ""}).status_code == 400
    r = client.post(f"/api/staff/hours-claims/{cid2}/revoke", headers=head,
                    json={"reason": "材料系伪造，已与主办方核实"})
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert len(inbox(db, sid, "时长补登已撤销")) == 1
    assert client.get("/api/me/hours-claims", headers=me).json()["counted_total"] == 0


def test_材料只有本人与审批线上的人能看(client, db, liaison, take_student):
    me_sid, other_sid = take_student(), take_student()
    me, other = login(client, me_sid), login(client, other_sid)
    claim = _submit(client, me).json()
    url = claim["files"]["proofs"][0]["url"]
    assert client.get(url, headers=me).status_code == 200
    assert client.get(url, headers=other).status_code == 404
    assert client.get(url, headers=login(client, LIAISON_HEAD)).status_code == 200
    # 通用的材料下载接口不给补登的附件：拿到 id 也绕不过上面这套权限
    assert client.get(f"/api/materials/{claim['files']['proofs'][0]['id']}",
                      headers=me).status_code == 404


def test_导出把通过的补登写成一行(client, db, liaison, take_student):
    sid = take_student()
    me = login(client, sid)
    head = login(client, LIAISON_HEAD)
    cid = _submit(client, me, title="校外图书馆整理志愿服务", hours="4").json()["id"]
    client.post(f"/api/staff/hours-claims/{cid}/decide", headers=head,
                json={"approve": True, "hours": 3.5})
    # 撤销掉的那条不导出
    gone = _submit(client, me, title="撤销掉的那一场",
                   start="2026-03-08 09:00", end="2026-03-08 12:00").json()["id"]
    client.post(f"/api/staff/hours-claims/{gone}/decide", headers=head,
                json={"approve": True})
    client.post(f"/api/staff/hours-claims/{gone}/revoke", headers=head,
                json={"reason": "重复登记"})

    r = client.get(dl_url(client, head, "/api/staff/hours-claims.xlsx"))
    assert r.status_code == 200
    ws = load_workbook(BytesIO(r.content)).active
    rows = list(ws.values)
    assert rows[0] == ("姓名", "学号", "学院", "身份证号", "活动名称",
                       "活动时间", "时长", "服务描述", "备注")
    # 表是全库通过的那些：只看这个学生自己的行（别的用例也提交过）
    mine = [row for row in rows[1:] if row[1] == sid]
    assert [row[4] for row in mine] == ["校外图书馆整理志愿服务"]
    assert mine[0][6] == 3.5
    # 没有权限的拿不到这张表
    assert client.get(dl_url(client, me, "/api/staff/hours-claims.xlsx"),
                      follow_redirects=False).status_code == 403


def test_超时催办只催一次(client, db, liaison, take_student, monkeypatch):
    from datetime import timedelta

    from backend import config, scheduled
    from backend.models import now

    sid = take_student()
    me = login(client, sid)
    cid = _submit(client, me).json()["id"]
    claim = db.get(HoursClaim, cid)
    claim.submitted_at = now() - timedelta(hours=config.APPROVAL_REMIND_HOURS + 1)
    db.commit()

    assert scheduled.remind_stale_claims(db) == 1
    assert len(inbox(db, LIAISON_HEAD, "时长补登已等待")) == 1
    # 催过就不再催
    assert scheduled.remind_stale_claims(db) == 0
