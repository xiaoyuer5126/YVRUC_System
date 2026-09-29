"""意见反馈：任何登录用户都能提平台问题，管理员处理并回复。

只收**平台本身**的问题。志愿工作上的问题前端直接引到校青协的志愿工作
反馈平台，不经过这里（见 models.Feedback）。

截图跟学习材料存在同一个目录、同一张 Attachment 表（owner_type="feedback"），
下载单独一个接口：材料的下载接口按活动判权，反馈不属于任何活动，塞进去只会
让那边的判断多一条岔路。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, locks
from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user, current_user_download, require_admin
from ..models import Attachment, Feedback, User, iso, now, uid
from ..notify import notify
from .materials import MATERIAL_DIR

router = APIRouter(prefix="/api", tags=["feedback"])

OWNER = "feedback"
RATE_WINDOW_SECONDS = 3600
IMAGE_TYPES = {"png": "image/png", "jpg": "image/jpeg",
               "jpeg": "image/jpeg", "webp": "image/webp"}


def _is_image(ext: str, data: bytes) -> bool:
    """看文件头，不光看扩展名：改个后缀的 html 当图片存下来，下载时按图片类型
    回给浏览器问题不大，但「仅图片」就该名副其实。"""
    if ext == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in ("jpg", "jpeg"):
        return data.startswith(b"\xff\xd8\xff")
    if ext == "webp":
        return data[:4] == b"RIFF" and data[8:12] == b"WEBP"
    return False


def _shots(db: OrmSession, ids: list[str]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == OWNER, Attachment.owner_id.in_(ids))
            .order_by(Attachment.created_at).all())
    for a in rows:
        out[a.owner_id].append({"id": a.id, "title": a.title, "size": a.size,
                                "url": f"/api/feedback/files/{a.id}"})
    return out


def _out(fb: Feedback, shots: list[dict], *, submitter: User | None = None,
         handler: User | None = None) -> dict:
    d = {"id": fb.id, "content": fb.content, "status": fb.status,
         "status_label": fb.status_label(), "reply": fb.reply or "",
         "handled_at": iso(fb.handled_at), "created_at": iso(fb.created_at),
         "images": shots}
    if submitter is not None:
        d["user"] = {"name": submitter.name, "student_id": submitter.student_id,
                     "college": submitter.college or ""}
    if handler is not None:
        d["handled_by_name"] = handler.name
    return d


# ======================= 提交与我的反馈 =======================

@router.post("/feedback")
async def submit_feedback(content: str = Form(""),
                          files: list[UploadFile] = File(default=[]),
                          db: OrmSession = Depends(db_session),
                          user: User = Depends(current_user)):
    """提交一条平台问题反馈，可附最多 3 张截图（一次表单提交，要么全进要么全不进）。"""
    text = content.strip()
    if not text:
        raise HTTPException(400, "请填写反馈内容")
    if len(text) > config.FEEDBACK_MAX_CHARS:
        raise HTTPException(400, f"反馈内容不能超过 {config.FEEDBACK_MAX_CHARS} 字")
    # 空文件名的是表单里没选文件时浏览器带上的空占位
    files = [f for f in files if f.filename]
    if len(files) > config.FEEDBACK_MAX_IMAGES:
        raise HTTPException(400, f"截图最多 {config.FEEDBACK_MAX_IMAGES} 张")
    rate_key = f"feedback:{user.id}"
    if locks.rate_count(rate_key) >= config.FEEDBACK_PER_HOUR:
        raise HTTPException(429, f"每小时最多提交 {config.FEEDBACK_PER_HOUR} 条反馈，"
                                 f"请稍后再试")

    # 先把每张图读完、校验完，再落盘：第三张不合格时前两张不该已经写在磁盘上
    images: list[tuple[str, str, bytes]] = []
    limit_mb = config.FEEDBACK_MAX_IMAGE_BYTES // (1024 * 1024)
    for f in files:
        ext = Path(f.filename or "").suffix.lower().lstrip(".")
        # 多读一个字节就够判断超没超，不必把一个超大文件整个读进内存
        data = await f.read(config.FEEDBACK_MAX_IMAGE_BYTES + 1)
        if ext not in IMAGE_TYPES or not _is_image(ext, data):
            raise HTTPException(400, f"截图只支持 {'/'.join(IMAGE_TYPES)} 格式的图片")
        if len(data) > config.FEEDBACK_MAX_IMAGE_BYTES:
            raise HTTPException(400, f"单张截图不能超过 {limit_mb}MB")
        images.append((ext, (f.filename or f"截图.{ext}")[:160], data))

    fb = Feedback(id=uid("fb"), user_id=user.id, content=text)
    db.add(fb)
    if images:
        MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    for ext, title, data in images:
        stored = f"{uuid4().hex}.{ext}"
        (MATERIAL_DIR / stored).write_bytes(data)
        # activity_id 留空：反馈不属于任何活动
        db.add(Attachment(id=uid("att"), owner_type=OWNER, owner_id=fb.id,
                          activity_id="", kind="file", title=title,
                          stored_name=stored, size=len(data), uploaded_by=user.id))
    audit_log(db, actor=user, action="feedback", target_type="feedback",
              target_id=fb.id, target_label=text[:40],
              summary=f"提交意见反馈（{len(text)} 字，截图 {len(images)} 张）")
    db.commit()
    locks.note_rate(rate_key, RATE_WINDOW_SECONDS)
    return _out(fb, _shots(db, [fb.id])[fb.id])


@router.get("/feedback/mine")
def my_feedback(db: OrmSession = Depends(db_session),
                user: User = Depends(current_user)):
    """我提交过的反馈与回复，最新的在前。"""
    rows = (db.query(Feedback).filter(Feedback.user_id == user.id)
            .order_by(Feedback.created_at.desc()).limit(50).all())
    shots = _shots(db, [r.id for r in rows])
    return [_out(r, shots[r.id]) for r in rows]


@router.get("/feedback/files/{attachment_id}")
def feedback_file(attachment_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(current_user_download)):
    """看截图：提交人本人和管理员。别人一律 404，不透露这张图存不存在。"""
    row = db.get(Attachment, attachment_id)
    if row is None or row.owner_type != OWNER:
        raise HTTPException(404, "文件不存在")
    if row.uploaded_by != user.id and not user.is_admin():
        raise HTTPException(404, "文件不存在")
    path = MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失")
    ext = row.stored_name.rsplit(".", 1)[-1]
    # 内联返回：前端直接当图片显示，不弹下载
    return FileResponse(path, media_type=IMAGE_TYPES.get(ext, "application/octet-stream"),
                        filename=row.title, content_disposition_type="inline")


# ======================= 管理员：列表与处理 =======================

@router.get("/staff/feedback")
def list_feedback(status: str = "", page: int = 1, size: int = 20,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_admin)):
    q = db.query(Feedback)
    if status:
        if status not in Feedback.STATUS_LABELS:
            raise HTTPException(400, "状态不合法")
        q = q.filter(Feedback.status == status)
    page, size = max(page, 1), min(max(size, 1), 100)
    total = q.count()
    rows = (q.order_by(Feedback.created_at.desc())
            .offset((page - 1) * size).limit(size).all())
    shots = _shots(db, [r.id for r in rows])
    people = {u.id: u for u in db.query(User).filter(User.id.in_(
        {r.user_id for r in rows} | {r.handled_by for r in rows if r.handled_by}))}
    items = [_out(r, shots[r.id], submitter=people.get(r.user_id),
                  handler=people.get(r.handled_by or "")) for r in rows]
    open_count = db.query(Feedback).filter(Feedback.status == "open").count()
    return {"total": total, "page": page, "size": size, "items": items,
            "open": open_count}


class HandleBody(BaseModel):
    reply: str = ""


@router.post("/staff/feedback/{feedback_id}/handle")
def handle_feedback(feedback_id: str, body: HandleBody,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_admin)):
    """标记已处理并回复。已处理的可以再改回复，每次都通知提交人。"""
    fb = db.get(Feedback, feedback_id)
    if fb is None:
        raise HTTPException(404, "反馈不存在")
    reply = body.reply.strip()
    if len(reply) > config.FEEDBACK_MAX_CHARS:
        raise HTTPException(400, f"回复不能超过 {config.FEEDBACK_MAX_CHARS} 字")
    again = fb.status == "handled"
    fb.status, fb.reply = "handled", reply
    fb.handled_by, fb.handled_at = user.id, now()
    submitter = db.get(User, fb.user_id)
    if submitter is not None:
        brief = fb.content if len(fb.content) <= 60 else fb.content[:60] + "…"
        notify(db, submitter, "意见反馈已处理",
               f"您提交的意见反馈「{brief}」已处理。"
               + (f"\n回复：{reply}" if reply else "")
               + "\n可在右上角菜单「意见反馈 → 我的反馈」查看。",
               data={"feedback_id": fb.id})
    audit_log(db, actor=user, action="feedback", target_type="feedback",
              target_id=fb.id, target_label=fb.content[:40],
              summary=("修改反馈回复" if again else "处理意见反馈")
              + (f"：{reply[:200]}" if reply else ""))
    db.commit()
    shots = _shots(db, [fb.id])
    return _out(fb, shots[fb.id], submitter=submitter, handler=user)
