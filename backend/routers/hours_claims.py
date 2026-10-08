"""校外时长补登：志愿者本人申报，对外联络部审批。

校外自行报名的活动在平台上没有场次、没有报名、没有签到，所以这条账不并进
HoursRecord——那条路是「一场活动一起结账」，核准、确认/申诉、锁定、外联确认
每一步都按活动判，为一个没有活动的场景到处开岔路不划算。补登自己一条台账，
通过的条目按时间范围导出给志愿北京录入（见 exporter.build_claims_workbook）。

审批人是「外联那摊事」的同一批：对外联络部的值班人与部长，会长团与管理员
（liaison.vb_decider），逐条通知也按 vb_contacts 那套找人——同一件事换个人做，
不再长出第二套权限表。队列和证明材料也只给这批人看：材料里有手机号、本人照片。
审批人自己的补登要由别人批，通知轮到他时往上找一层（见 decide_claim、
liaison.claim_contacts）。证明材料与活动照片挂 Attachment（owner_type=claim_proof /
claim_photo，activity_id 留空），跟反馈截图一样不属于任何活动。
"""
from __future__ import annotations

import logging
import math
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, liaison, locks, uploads
from ..audit import log as audit_log
from ..database import db_session
from ..deps import (current_user, current_user_download, require_approver,
                    require_claim_decider, require_claim_decider_download)
from ..exporter import build_claims_workbook
from ..models import (Attachment, HoursClaim, User, iso, now, parse_local_dt,
                      span_text, uid)
from ..notify import notify

router = APIRouter(prefix="/api", tags=["hours_claims"])
log = logging.getLogger(__name__)

PROOF = "claim_proof"
PHOTO = "claim_photo"
PROOF_EXTS = {**uploads.IMAGE_TYPES, "pdf": "application/pdf"}
RATE_WINDOW_SECONDS = 3600
OPEN_STATUSES = ("pending", "approved")   # 这两态算「已经报过」，同一条不能再提


def _files(db: OrmSession, ids: list[str]) -> dict[str, dict[str, list[dict]]]:
    out: dict[str, dict[str, list[dict]]] = {
        i: {"proofs": [], "photos": []} for i in ids}
    if not ids:
        return out
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type.in_((PROOF, PHOTO)),
                    Attachment.owner_id.in_(ids))
            .order_by(Attachment.created_at).all())
    for a in rows:
        key = "proofs" if a.owner_type == PROOF else "photos"
        out[a.owner_id][key].append(
            {"id": a.id, "title": a.title, "size": a.size,
             "url": f"/api/me/hours-claims/files/{a.id}"})
    return out


def _out(claim: HoursClaim, files: dict, *, viewer: User | None = None,
         submitter: User | None = None) -> dict:
    d = {"id": claim.id, "title": claim.title,
         "start_at": iso(claim.start_at), "end_at": iso(claim.end_at),
         "time_text": span_text(claim.start_at, claim.end_at),
         "hours": claim.hours, "approved_hours": claim.approved_hours,
         "counted_hours": claim.counted_hours(),
         "service_desc": claim.service_desc, "note": claim.note,
         "status": claim.status, "status_label": claim.status_label(),
         "reply": claim.reply, "submitted_at": iso(claim.submitted_at),
         "handled_at": iso(claim.handled_at),
         "files": files}
    if viewer is not None:
        d["mine"] = claim.user_id == viewer.id
        d["can_withdraw"] = claim.status == "pending" and d["mine"]
    if submitter is not None:
        d["user"] = {"name": submitter.name, "student_id": submitter.student_id,
                     "college": submitter.college or "",
                     "campus": submitter.campus or "", "phone": submitter.phone}
    return d


def _parse_range(start_raw: str, end_raw: str) -> tuple[datetime, datetime]:
    try:
        start = parse_local_dt(start_raw)
        end = parse_local_dt(end_raw)
    except ValueError:
        raise HTTPException(400, "活动时间格式不对") from None
    if end < start:
        raise HTTPException(400, "结束时间不能早于开始时间")
    if end > now():
        raise HTTPException(400, "活动时间不能填将来——这里登记的是已经参加过的活动")
    return start, end


def _parse_hours(raw) -> float:
    try:
        hours = round(float(raw), 2)
    except (TypeError, ValueError):
        raise HTTPException(400, "时长请填数字") from None
    # nan / inf 要先挡住：它们和 0、上限的比较恒为假，会一路混到下面的 round() 上炸成 500
    if not math.isfinite(hours):
        raise HTTPException(400, "时长请填数字")
    if hours <= 0 or hours > config.CLAIM_HOURS_MAX:
        raise HTTPException(400, f"时长要在 0～{config.CLAIM_HOURS_MAX} 小时之间")
    if abs(hours * 2 - round(hours * 2)) > 1e-6:
        raise HTTPException(400, "时长按 0.5 小时填")
    return hours


def _notify_pending(db: OrmSession, claim: HoursClaim, submitter: User) -> None:
    """新提交的补登告诉对外联络部：通知单独一个事务，出了岔子只记日志。

    补登已经提交在先，不能让本人看到「提交失败」再填一遍。
    """
    try:
        for u in liaison.claim_contacts(db, submitter):
            notify(db, u, f"[时长补登] {claim.title}",
                   f"{submitter.name}（{submitter.student_id}）提交了一条校外时长补登：\n"
                   f"活动时间：{span_text(claim.start_at, claim.end_at)}\n"
                   f"申报时长：{claim.hours:g} 小时\n"
                   f"服务描述：{claim.service_desc}\n"
                   f"请到「活动审批」核对证明材料后通过或驳回。",
                   data={"route": "approvals"})
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("时长补登 %s 已提交，通知对外联络部失败", claim.id)


def _notify_owner(db: OrmSession, claim: HoursClaim, title: str, body: str,
                  user_id: str) -> None:
    try:
        u = db.get(User, user_id)
        if u is not None:
            notify(db, u, title, body, data={"route": "hours"})
        db.commit()
    except Exception:  # noqa: BLE001
        db.rollback()
        log.exception("时长补登 %s 的结果通知失败", claim.id)


# ======================= 本人：提交、我的补登、撤回 =======================

@router.post("/me/hours-claims")
async def submit_claim(title: str = Form(""), start_at: str = Form(""),
                       end_at: str = Form(""), hours: str = Form(""),
                       service_desc: str = Form(""), note: str = Form(""),
                       proofs: list[UploadFile] = File(default=[]),
                       photos: list[UploadFile] = File(default=[]),
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(current_user)):
    """提交一条校外时长补登（一次表单提交，要么全进要么全不进）。

    要资料完整才能提：这张台账最终是导出给志愿北京录时长的，缺身份证号、
    学院就没法录，先让人补齐再来。
    """
    if not user.profile_complete():
        raise HTTPException(400, "请先补全档案（手机号、微信号、校区、身份证号）"
                                 "再登记校外时长")
    title = title.strip()
    if len(title) < 2 or len(title) > 128:
        raise HTTPException(400, "活动名称请填 2～128 个字")
    desc = service_desc.strip()
    if not config.CLAIM_DESC_MIN <= len(desc) <= config.CLAIM_DESC_MAX:
        raise HTTPException(400, f"服务描述请填 {config.CLAIM_DESC_MIN}～"
                                 f"{config.CLAIM_DESC_MAX} 个字")
    note = note.strip()
    if len(note) > 256:
        raise HTTPException(400, "备注不能超过 256 个字")
    start, end = _parse_range(start_at, end_at)
    hours_value = _parse_hours(hours)

    # 空文件名的是表单里没选文件时浏览器带上的空占位
    proof_files = [f for f in proofs if f.filename]
    photo_files = [f for f in photos if f.filename]
    if not proof_files:
        raise HTTPException(400, "请上传证明材料")
    if not photo_files:
        raise HTTPException(400, "请上传本人活动照片")

    # 同一天、同名称的已经报过就不再收：多半是点重了，或者改了几个字重提
    same_day = [c for c in db.query(HoursClaim)
                .filter(HoursClaim.user_id == user.id,
                        HoursClaim.status.in_(OPEN_STATUSES)).all()
                if c.title == title and c.start_at.date() == start.date()]
    if same_day:
        raise HTTPException(400, "这条校外活动已经登记过（同名称、同一天），"
                                 "请在下面的列表里看进度或撤回后重填")

    rate_key = f"claims:{user.id}"
    if locks.rate_count(rate_key) >= config.CLAIM_PER_HOUR:
        raise HTTPException(429, f"每小时最多提交 {config.CLAIM_PER_HOUR} 条，"
                                 f"请稍后再试")

    # 先全部读完、校验完再落盘：第三份不合格时前两份不该已经写在磁盘上
    proofs_read = await uploads.read_files(
        proof_files, label="证明材料", exts=PROOF_EXTS,
        max_count=config.CLAIM_MAX_PROOFS, max_bytes=config.CLAIM_MAX_FILE_BYTES)
    photos_read = await uploads.read_files(
        photo_files, label="活动照片", exts=uploads.IMAGE_TYPES,
        max_count=config.CLAIM_MAX_PHOTOS, max_bytes=config.CLAIM_MAX_FILE_BYTES)

    claim = HoursClaim(id=uid("hcl"), user_id=user.id, title=title,
                       start_at=start, end_at=end, hours=hours_value,
                       service_desc=desc, note=note)
    db.add(claim)
    uploads.save_files(db, proofs_read, owner_type=PROOF, owner_id=claim.id,
                       uploaded_by=user.id)
    uploads.save_files(db, photos_read, owner_type=PHOTO, owner_id=claim.id,
                       uploaded_by=user.id)
    audit_log(db, actor=user, action="hours_claim", target_type="hours_claim",
              target_id=claim.id, target_label=title,
              summary=f"提交校外时长补登「{title}」"
                      f"（{span_text(start, end)}，{hours_value:g}h，"
                      f"证明 {len(proofs_read)} 份、照片 {len(photos_read)} 张）")
    db.commit()
    locks.note_rate(rate_key, RATE_WINDOW_SECONDS)
    # 提交之后的推送是同步外呼（一条最多等 8 秒），放到线程池里做：
    # 这是 async 处理函数，直接调会把整个事件循环卡住，同一进程的请求全跟着等
    await run_in_threadpool(_notify_pending, db, claim, user)
    return _out(claim, _files(db, [claim.id])[claim.id], viewer=user)


@router.get("/me/hours-claims")
def my_claims(db: OrmSession = Depends(db_session),
              user: User = Depends(current_user)):
    """我登记过的校外时长，最新的在前。"""
    rows = (db.query(HoursClaim).filter(HoursClaim.user_id == user.id)
            .order_by(HoursClaim.submitted_at.desc()).limit(100).all())
    files = _files(db, [r.id for r in rows])
    counted = sum(r.counted_hours() for r in rows)
    return {"items": [_out(r, files[r.id], viewer=user) for r in rows],
            "counted_total": round(counted, 2)}


@router.post("/me/hours-claims/{claim_id}/withdraw")
def withdraw_claim(claim_id: str, db: OrmSession = Depends(db_session),
                   user: User = Depends(current_user)):
    """撤回还没审批的那条（连证明材料一起删）。审批过的不能自己撤——改数要走
    外联，留痕在审计里。

    行锁和审批互斥：不锁的话撤回撞上通过，会把刚通过的那条连材料一起删掉。"""
    claim = db.get(HoursClaim, claim_id, with_for_update=True)
    if claim is None or claim.user_id != user.id:
        raise HTTPException(404, "补登记录不存在")
    if claim.status != "pending":
        raise HTTPException(409, "已经审批过的补登不能自己撤回，请联系对外联络部")
    for row in (db.query(Attachment)
                .filter(Attachment.owner_type.in_((PROOF, PHOTO)),
                        Attachment.owner_id == claim.id).all()):
        liaison.drop_attachment(db, row)
    audit_log(db, actor=user, action="hours_claim", target_type="hours_claim",
              target_id=claim.id, target_label=claim.title,
              summary=f"撤回校外时长补登「{claim.title}」")
    db.delete(claim)
    db.commit()
    return {"ok": True}


@router.get("/me/hours-claims/files/{attachment_id}")
def claim_file(attachment_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(current_user_download)):
    """看证明材料与照片：提交人本人和审批补登的人（liaison.vb_decider）。别人一律 404。"""
    row = db.get(Attachment, attachment_id)
    if row is None or row.owner_type not in (PROOF, PHOTO):
        raise HTTPException(404, "文件不存在")
    if row.uploaded_by != user.id and not liaison.vb_decider(db, user):
        raise HTTPException(404, "文件不存在")
    path = config.MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失，请联系对外联络部")
    ext = row.stored_name.rsplit(".", 1)[-1]
    # 图片内联：前端当缩略图显示。PDF 走下载：内联返回会让浏览器把当前页面换成
    # PDF 阅读器，材料看到一半就找不回审批页了（材料下载接口也是这个口径）
    return FileResponse(path, media_type=uploads.EXT_TYPES.get(
        ext, "application/octet-stream"), filename=row.title,
        content_disposition_type="attachment" if ext == "pdf" else "inline")


# ======================= 对外联络部：队列、审批、撤销、导出 =======================

@router.get("/staff/hours-claims")
def list_claims(status: str = "", page: int = 1, size: int = 20,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_approver)):
    """待审批的补登，挂在「活动审批」页上；只给审批补登的人看（同 /api/vb-confirms），
    别的能进审批页的人拿到空表。"""
    if not liaison.vb_decider(db, user):
        return {"items": [], "total": 0, "page": max(1, page),
                "size": max(1, min(size, 100)), "pending": 0, "can_decide": False}
    # 分页参数照别的列表接口夹一下：page=0 会拼出 OFFSET -20，Postgres 直接报错 500
    page = max(1, page)
    size = max(1, min(size, 100))
    q = db.query(HoursClaim)
    if status:
        if status not in HoursClaim.STATUS_LABELS:
            raise HTTPException(400, "状态不合法")
        q = q.filter(HoursClaim.status == status)
    total = q.count()
    rows = (q.order_by(HoursClaim.submitted_at.desc())
            .offset((page - 1) * size).limit(size).all())
    people = {u.id: u for u in db.query(User)
              .filter(User.id.in_([r.user_id for r in rows] or [""])).all()}
    files = _files(db, [r.id for r in rows])
    pending = (db.query(HoursClaim).filter(HoursClaim.status == "pending").count())
    return {"items": [_out(r, files[r.id], viewer=user,
                           submitter=people.get(r.user_id)) for r in rows],
            "total": total, "page": page, "size": size, "pending": pending,
            "can_decide": True}


class DecideBody(BaseModel):
    approve: bool
    hours: float | None = None      # 核定的时长，不给就按本人申报的算
    reply: str = ""


@router.post("/staff/hours-claims/{claim_id}/decide")
def decide_claim(claim_id: str, body: DecideBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_claim_decider)):
    """通过（可核定一个不一样的时长）或驳回（必须写理由）。

    行锁：两个审批人同时点开同一条，一个通过一个驳回，不锁的话两边都提交，
    本人先后收到「已通过」「未通过」。审批人自己的补登要由别人批。
    """
    claim = db.get(HoursClaim, claim_id, with_for_update=True)
    if claim is None:
        raise HTTPException(404, "补登记录不存在")
    if claim.user_id == user.id:
        raise HTTPException(403, "本人提交的补登要由对外联络部的其他人审批")
    if claim.status != "pending":
        raise HTTPException(409, f"这条已经「{claim.status_label()}」了")
    reply = body.reply.strip()
    if not body.approve and len(reply) < 2:
        raise HTTPException(400, "驳回要写明理由，本人照着补材料或核对")
    claim.reply = reply[:512]
    claim.handled_by = user.id
    claim.handled_at = now()
    if body.approve:
        hours = claim.hours if body.hours is None else _parse_hours(body.hours)
        # 只能往下核：往上加等于给「给自己人加时长」留一条路，要加就走驳回让本人改后重提
        if hours > claim.hours:
            raise HTTPException(400, f"核定不能高于本人申报的 {claim.hours:g} 小时，"
                                     f"确实更多请驳回让他改后重新提交")
        claim.status, claim.approved_hours = "approved", hours
        summary = f"通过校外时长补登「{claim.title}」，核定 {hours:g} 小时"
        title, text = (f"时长补登已通过｜{claim.title}",
                       f"您登记的「{claim.title}」"
                       f"（{span_text(claim.start_at, claim.end_at)}）"
                       f"已通过对外联络部审批，计入志愿时长 {hours:g} 小时。")
    else:
        claim.status = "rejected"
        summary = f"驳回校外时长补登「{claim.title}」：{reply}"
        title, text = (f"时长补登未通过｜{claim.title}",
                       f"您登记的「{claim.title}」"
                       f"（{span_text(claim.start_at, claim.end_at)}）未通过审批。\n"
                       f"理由：{reply}\n"
                       f"补充材料后可以重新登记。")
    audit_log(db, actor=user, action="hours_claim", target_type="hours_claim",
              target_id=claim.id, target_label=claim.title, summary=summary)
    db.commit()
    _notify_owner(db, claim, title, text, claim.user_id)
    return _out(claim, _files(db, [claim.id])[claim.id], submitter=None)


class RevokeBody(BaseModel):
    reason: str


@router.post("/staff/hours-claims/{claim_id}/revoke")
def revoke_claim(claim_id: str, body: RevokeBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_claim_decider)):
    """撤销一条已经通过的补登：材料不实、重复登记、时长填错。

    已经计进时长的部分随之作废（这条台账只有 admin 与审批线上的人看得到），
    本人会收到通知，理由进审计。
    """
    claim = db.get(HoursClaim, claim_id, with_for_update=True)
    if claim is None:
        raise HTTPException(404, "补登记录不存在")
    if claim.status != "approved":
        raise HTTPException(409, f"只有已通过的能撤销，这条是「{claim.status_label()}」")
    reason = body.reason.strip()
    if len(reason) < 2:
        raise HTTPException(400, "撤销要写明理由")
    counted = claim.counted_hours()     # 改成已撤销之后就是 0 了，先记下
    claim.status, claim.reply = "revoked", reason[:512]
    claim.handled_by, claim.handled_at = user.id, now()
    audit_log(db, actor=user, action="hours_claim", target_type="hours_claim",
              target_id=claim.id, target_label=claim.title,
              summary=f"撤销校外时长补登「{claim.title}」：{reason}")
    db.commit()
    _notify_owner(db, claim, f"时长补登已撤销｜{claim.title}",
                  f"您登记的「{claim.title}」"
                  f"（{span_text(claim.start_at, claim.end_at)}）已被撤销，"
                  f"原计入的 {counted:g} 小时不再计入。\n理由：{reason}",
                  claim.user_id)
    return _out(claim, _files(db, [claim.id])[claim.id])


@router.get("/staff/hours-claims.xlsx")
def export_claims(start: str = "", end: str = "",
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_claim_decider_download)):
    """导出通过的补登给志愿北京录入；可按活动时间范围筛（默认全部）。

    已撤销的不在里头：它们不再计入时长。
    """
    q = db.query(HoursClaim).filter(HoursClaim.status == "approved")
    if start:
        try:
            q = q.filter(HoursClaim.start_at >= parse_local_dt(start))
        except ValueError:
            raise HTTPException(400, "开始时间格式不对") from None
    if end:
        try:
            q = q.filter(HoursClaim.start_at <= parse_local_dt(end))
        except ValueError:
            raise HTTPException(400, "截止时间格式不对") from None
    rows = q.order_by(HoursClaim.start_at).all()
    buf = build_claims_workbook(db, rows)
    filename = f"校外时长补登_{now():%Y%m%d_%H%M}.xlsx"
    audit_log(db, actor=user, action="export", target_type="hours_claim",
              target_id="", target_label="校外时长补登",
              summary=f"导出校外时长补登《{filename}》（{len(rows)} 条）")
    db.commit()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})
