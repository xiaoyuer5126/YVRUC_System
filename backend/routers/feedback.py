"""意见反馈：任何登录用户都能提平台问题，管理员处理并回复。

只收**平台本身**的问题。志愿工作上的问题前端直接引到校青协的志愿工作
反馈平台，不经过这里（见 models.Feedback）。

截图跟学习材料存在同一个目录、同一张 Attachment 表（owner_type="feedback"），
下载单独一个接口：材料的下载接口按活动判权，反馈不属于任何活动，塞进去只会
让那边的判断多一条岔路。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy import Text, cast
from sqlalchemy.orm import Session as OrmSession

from .. import config, downloads, locks, uploads
from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user, current_user_download, require_admin
from ..models import Attachment, Feedback, User, iso, now, uid
from ..notify import notify
from ..uploads import IMAGE_TYPES

router = APIRouter(prefix="/api", tags=["feedback"])
log = logging.getLogger(__name__)

OWNER = "feedback"
RATE_WINDOW_SECONDS = 3600
MATERIAL_DIR = config.MATERIAL_DIR


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
    # 先占后判：先自增再比，两个请求同时交进来不会双双看到「还没到上限」。
    # 放在读截图之前——被限流拦下的请求不该先把图读一遍
    rate_key = f"feedback:{user.id}"
    locks.note_rate(rate_key, RATE_WINDOW_SECONDS)
    if locks.rate_count(rate_key) > config.FEEDBACK_PER_HOUR:
        raise HTTPException(429, f"每小时最多提交 {config.FEEDBACK_PER_HOUR} 条反馈，"
                                 f"请稍后再试")

    images = await uploads.read_files(files, label="截图", exts=IMAGE_TYPES,
                                     max_count=config.FEEDBACK_MAX_IMAGES,
                                     max_bytes=config.FEEDBACK_MAX_IMAGE_BYTES)

    fb = Feedback(id=uid("fb"), user_id=user.id, content=text)
    db.add(fb)
    # activity_id 留空：反馈不属于任何活动
    uploads.save_files(db, images, owner_type=OWNER, owner_id=fb.id,
                       uploaded_by=user.id)
    audit_log(db, actor=user, action="feedback", target_type="feedback",
              target_id=fb.id, target_label=text[:40],
              summary=f"提交意见反馈（{len(text)} 字，截图 {len(images)} 张）")
    db.commit()
    # 提交之后的推送是同步外呼（一条最多等 8 秒），放到线程池里做：
    # 这是 async 处理函数，直接调会把整个事件循环卡住，同一进程的请求全跟着等
    await run_in_threadpool(_notify_admins, db, fb, user)
    return _out(fb, _shots(db, [fb.id])[fb.id])


def _notify_admins(db: OrmSession, fb: Feedback, submitter: User) -> None:
    """新反馈告诉每位管理员，点开进管理端「意见反馈」页。

    反馈已经提交在先、通知单独一个事务：通知出了岔子只记日志，不能让用户
    看到「提交失败」再重提一遍。管理员按真实身份找（别人的身份是事实，不看
    视角）；提交人自己是管理员时不发给他——这条就是他刚写的。
    """
    brief = fb.content if len(fb.content) <= 60 else fb.content[:60] + "…"
    who = f"{submitter.name}（{submitter.student_id}）"
    try:
        # roles 是 JSON 不是 JSONB，没有包含运算符；按序列化文本匹配带引号的
        # "admin"，粗筛之后再用 has_role 精确判一遍
        rows = (db.query(User)
                .filter(cast(User.roles, Text).like('%"admin"%'),
                        User.id != submitter.id).all())
        for u in rows:
            if u.has_role("admin", real=True):
                notify(db, u, "新的意见反馈",
                       f"{who} 提交了一条意见反馈：「{brief}」",
                       data={"feedback_id": fb.id, "route": "feedback"})
        db.commit()
    except Exception:
        db.rollback()
        log.exception("意见反馈 %s 已提交，通知管理员失败", fb.id)


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
    return downloads.file_response(
        path, row.title, kind="inline",
        media_type=IMAGE_TYPES.get(ext, "application/octet-stream"))


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
               # route="feedback_mine"：落消息中心并把「我的反馈」弹窗打开
               # （弹窗没有自己的路由），站内和企业微信用同一个地址
               data={"feedback_id": fb.id, "route": "feedback_mine"})
    audit_log(db, actor=user, action="feedback", target_type="feedback",
              target_id=fb.id, target_label=fb.content[:40],
              summary=("修改反馈回复" if again else "处理意见反馈")
              + (f"（：{reply[:200]}）" if reply else ""))
    db.commit()
    shots = _shots(db, [fb.id])
    return _out(fb, shots[fb.id], submitter=submitter, handler=user)
