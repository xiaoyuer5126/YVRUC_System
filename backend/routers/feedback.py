"""意见反馈：任何登录用户都能提，分平台问题与志愿反馈两类（见 models.Feedback）。

- 平台问题：管理员处理并回复，新反馈通知全体管理员。
- 志愿反馈：对外联络部（志愿北京对接部门）的骨干、部长，会长团和管理员处理。通知照
  活动审批：新反馈发值班人、没人值班发部长；放太久没处理由定时任务催一次并抄送
  会长团（scheduled.remind_stale_feedback）；列进部长的每晚日报。可以匿名：处理方
  的接口一律不返回提交人，通知与通知记录里也不出现（见 _notify_vb_handlers）。

截图跟学习材料存在同一个目录、同一张 Attachment 表（owner_type="feedback"），
下载单独一个接口：材料的下载接口按活动判权，反馈不属于任何活动，塞进去只会
让那边的判断多一条岔路。
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, downloads, locks, uploads
from ..audit import log as audit_log
from ..database import db_session
from .. import liaison
from ..deps import current_user, current_user_download, require_admin
from ..models import (Activity, Application, Attachment, Enrollment, Feedback, User,
                      iso, like_pattern, now, uid)
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


ANON_NAME = "匿名志愿者"


def _activity_brief(db: OrmSession, activity_id: str | None) -> dict | None:
    act = db.get(Activity, activity_id) if activity_id else None
    return {"id": act.id, "title": act.title} if act is not None else None


def _out(db: OrmSession, fb: Feedback, shots: list[dict], *,
         submitter: User | None = None, handler: User | None = None) -> dict:
    """一条反馈摊成前端要的字段。

    submitter 只在处理方的列表里传；匿名的志愿反馈哪怕传了也不带出去——
    处理方看不到是谁，这一条在这里兜底，不靠调用方记得。
    """
    d = {"id": fb.id, "category": fb.category, "content": fb.content,
         "status": fb.status, "status_label": fb.status_label(),
         "reply": fb.reply or "", "handled_at": iso(fb.handled_at),
         "created_at": iso(fb.created_at), "images": shots}
    if fb.category == "volunteer":
        d.update(kind=fb.kind, kind_label=fb.kind_label(), anonymous=fb.anonymous,
                 contact=fb.contact or "",
                 activity=_activity_brief(db, fb.activity_id))
    if submitter is not None:
        d["user"] = (None if fb.anonymous else
                     {"name": submitter.name, "student_id": submitter.student_id,
                      "college": submitter.college or ""})
    if handler is not None:
        d["handled_by_name"] = handler.name
    return d


def _brief(text: str) -> str:
    return text if len(text) <= 60 else text[:60] + "…"


def reply_kind_text(kind: str) -> str:
    """回复模板里【】中的意见类型。「提出对于……」去掉「提出」：模板里已经是
    「同学提出的【……】」，不去掉就读成「提出的【提出……】」。"""
    label = Feedback.KIND_LABELS.get(kind, kind)
    return label.removeprefix("提出")


def reply_draft(fb: Feedback) -> str:
    """志愿反馈点「回复」时预填的文字，处理人把【解决方法】换成具体内容。"""
    today = now()
    return ("同学你好！\n"
            f"协会已收到平台上同学提出的【{reply_kind_text(fb.kind)}】。\n"
            "【解决方法】\n"
            "感谢同学对志愿服务活动的支持。让有爱者有力，让奉献者发光，"
            "期待与同学一起参与更多丰富多彩的志愿活动！\n"
            "中国人民大学青年志愿者协会\n"
            f"{today.year}年{today.month}月{today.day}日")


# ======================= 提交与我的反馈 =======================

@router.post("/feedback")
async def submit_feedback(content: str = Form(""),
                          category: str = Form("platform"),
                          kind: str = Form(""),
                          activity_id: str = Form(""),
                          anonymous: bool = Form(False),
                          contact: str = Form(""),
                          files: list[UploadFile] = File(default=[]),
                          db: OrmSession = Depends(db_session),
                          user: User = Depends(current_user)):
    """提交一条反馈，可附最多 3 张截图（一次表单提交，要么全进要么全不进）。

    category=platform 是平台问题；volunteer 是志愿反馈，意见类型必选，关联活动、
    联系方式、匿名选填。两类走同一个接口：操作日志只记接口不记正文，分不出
    这一次提的是哪一类，匿名的人不会因为调了「志愿反馈」接口被认出来。
    """
    if category not in Feedback.CATEGORIES:
        raise HTTPException(400, "反馈类别不合法")
    volunteer = category == "volunteer"
    text = content.strip()
    if not text:
        raise HTTPException(400, "请填写意见内容" if volunteer else "请填写反馈内容")
    if len(text) > config.FEEDBACK_MAX_CHARS:
        raise HTTPException(400, f"反馈内容不能超过 {config.FEEDBACK_MAX_CHARS} 字")
    act = None
    contact = contact.strip()
    if volunteer:
        if kind not in Feedback.KIND_LABELS:
            raise HTTPException(400, "请选择意见类型")
        if len(contact) > config.FEEDBACK_CONTACT_MAX:
            raise HTTPException(400, f"联系方式不能超过 {config.FEEDBACK_CONTACT_MAX} 字")
        if activity_id:
            act = db.get(Activity, activity_id)
            if act is None or not _pickable(db, act, user):
                raise HTTPException(400, "关联的活动不存在")
    # 空文件名的是表单里没选文件时浏览器带上的空占位
    files = [f for f in files if f.filename]
    # 先占后判：先自增再比，两个请求同时交进来不会双双看到「还没到上限」。
    # 放在读截图之前——被限流拦下的请求不该先把图读一遍。两类共用一个额度
    rate_key = f"feedback:{user.id}"
    locks.note_rate(rate_key, RATE_WINDOW_SECONDS)
    if locks.rate_count(rate_key) > config.FEEDBACK_PER_HOUR:
        raise HTTPException(429, f"每小时最多提交 {config.FEEDBACK_PER_HOUR} 条反馈，"
                                 f"请稍后再试")

    images = await uploads.read_files(files, label="截图", exts=IMAGE_TYPES,
                                     max_count=config.FEEDBACK_MAX_IMAGES,
                                     max_bytes=config.FEEDBACK_MAX_IMAGE_BYTES)

    fb = Feedback(id=uid("fb"), user_id=user.id, content=text, category=category)
    if volunteer:
        fb.kind, fb.contact, fb.anonymous = kind, contact, anonymous
        fb.activity_id = act.id if act is not None else None
    db.add(fb)
    # activity_id 留空：截图不属于任何活动，挂上活动会被活动的材料清理和权限判断带走
    uploads.save_files(db, images, owner_type=OWNER, owner_id=fb.id,
                       uploaded_by=user.id)
    if not fb.anonymous:
        # 匿名的不留审计：审计的操作人就是提交人，会长团在审计日志里看得到。
        # 志愿反馈的摘要只写类型不写正文，也不挂活动——挂了活动，这场活动的
        # 管理者在「变更记录」里就看得到谁提了意见
        audit_log(db, actor=user, action="feedback", target_type="feedback",
                  target_id=fb.id,
                  target_label=fb.kind_label() if volunteer else text[:40],
                  summary=(f"提交志愿反馈（{fb.kind_label()}，截图 {len(images)} 张）"
                           if volunteer else
                           f"提交意见反馈（{len(text)} 字，截图 {len(images)} 张）"))
    db.commit()
    # 提交之后的推送是同步外呼（一条最多等 8 秒），放到线程池里做：
    # 这是 async 处理函数，直接调会把整个事件循环卡住，同一进程的请求全跟着等
    if volunteer:
        await run_in_threadpool(_notify_vb_handlers, db, fb, user)
    else:
        await run_in_threadpool(_notify_admins, db, fb, user)
    return _out(db, fb, _shots(db, [fb.id])[fb.id])


def _pickable(db: OrmSession, act: Activity, user: User) -> bool:
    """志愿反馈能关联的活动：已发布、公开招募的活动，或者他报过名的。"""
    if act.recruit_mode != config.RECORD_MODE and liaison.is_published(act):
        return True
    return act.id in _my_activity_ids(db, user)


def _my_activity_ids(db: OrmSession, user: User) -> set[str]:
    return ({a for (a,) in db.query(Enrollment.activity_id)
             .filter(Enrollment.user_id == user.id)}
            | {a for (a,) in db.query(Application.activity_id)
               .filter(Application.user_id == user.id)})


@router.get("/feedback/activities")
def feedback_activities(q: str = "", db: OrmSession = Depends(db_session),
                        user: User = Depends(current_user)):
    """志愿反馈「关联活动」的候选。不搜时列他报过名的活动；搜就按标题搜
    已发布的公开活动，他报过名的排在前面。"""
    mine = _my_activity_ids(db, user)
    word = q.strip()
    if not word:
        rows = (db.query(Activity).filter(Activity.id.in_(mine or ["__none__"]))
                .order_by(Activity.created_at.desc()).limit(30).all())
    else:
        rows = (db.query(Activity)
                .filter(Activity.title.like(like_pattern(word), escape="\\"),
                        Activity.status.in_(liaison.PUBLISHED_STATES),
                        Activity.recruit_mode != config.RECORD_MODE)
                .order_by(Activity.created_at.desc()).limit(30).all())
        rows.sort(key=lambda a: a.id not in mine)
    return [{"id": a.id, "title": a.title, "mine": a.id in mine} for a in rows]


def _notify_admins(db: OrmSession, fb: Feedback, submitter: User) -> None:
    """新反馈告诉每位管理员，点开进管理端「意见反馈」页。

    反馈已经提交在先、通知单独一个事务：通知出了岔子只记日志，不能让用户
    看到「提交失败」再重提一遍。管理员按真实身份找（别人的身份是事实，不看
    视角）；提交人自己是管理员时不发给他——这条就是他刚写的。
    """
    brief = _brief(fb.content)
    who = f"{submitter.name}（{submitter.student_id}）"
    try:
        # roles 是 JSONB，@> 走 GIN 索引找出持管理员身份的人
        rows = (db.query(User)
                .filter(User.roles.contains(["admin"]),
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


def vb_feedback_contacts(db: OrmSession) -> list[User]:
    """新的志愿反馈通知谁：对外联络部的值班人，没人值班发部长（同活动审批）。
    对外联络部没设、或者没有部长时兜底发分管会长，不然这条反馈没人知道。"""
    from .staff_ops import dept_council
    dept = liaison.vb_dept(db)
    contacts = liaison.approval_contacts(db, dept.id) if dept is not None else []
    return contacts or dept_council(db, dept.id if dept is not None else None)


def _notify_vb_handlers(db: OrmSession, fb: Feedback, submitter: User) -> None:
    """新的志愿反馈通知对外联络部值班人（没人值班发部长），点开进「活动审批」。

    匿名的：正文写「匿名志愿者」，并让这条通知不记触发人（anon_trigger），
    否则管理端「通知记录」的触发人一栏就是提交人。提交在先、通知单独一个事务，
    通知出岔子只记日志。
    """
    who = ANON_NAME if fb.anonymous else f"{submitter.name}（{submitter.student_id}）"
    data = {"feedback_id": fb.id, "route": "approvals", "tab": "feedback"}
    if fb.anonymous:
        data["anon_trigger"] = True
    try:
        for u in vb_feedback_contacts(db):
            if u.id == submitter.id:
                continue
            notify(db, u, f"新的志愿者反馈｜{fb.kind_label()}",
                   f"{who} 提交了一条志愿者反馈【{fb.kind_label()}】：「{_brief(fb.content)}」\n"
                   f"请到「活动审批 → 志愿者反馈」回复。", data=data)
        db.commit()
    except Exception:
        db.rollback()
        log.exception("志愿反馈 %s 已提交，通知对外联络部失败", fb.id)


@router.get("/feedback/mine")
def my_feedback(db: OrmSession = Depends(db_session),
                user: User = Depends(current_user)):
    """我提交过的反馈与回复（两类都在），最新的在前。"""
    rows = (db.query(Feedback).filter(Feedback.user_id == user.id)
            .order_by(Feedback.created_at.desc()).limit(50).all())
    shots = _shots(db, [r.id for r in rows])
    return [_out(db, r, shots[r.id]) for r in rows]


@router.get("/feedback/files/{attachment_id}")
def feedback_file(attachment_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(current_user_download)):
    """看截图：提交人本人、管理员；志愿反馈的截图另加对外联络部骨干、部长。
    别人一律 404，不透露这张图存不存在。"""
    row = db.get(Attachment, attachment_id)
    if row is None or row.owner_type != OWNER:
        raise HTTPException(404, "文件不存在")
    if row.uploaded_by != user.id and not user.is_admin():
        fb = db.get(Feedback, row.owner_id)
        if fb is None or fb.category != "volunteer" \
                or not liaison.feedback_handler(db, user):
            raise HTTPException(404, "文件不存在")
    path = MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失")
    ext = row.stored_name.rsplit(".", 1)[-1]
    # 内联返回：前端直接当图片显示，不弹下载
    return downloads.file_response(
        path, row.title, kind="inline",
        media_type=IMAGE_TYPES.get(ext, "application/octet-stream"))


# ======================= 管理员：平台问题 =======================

def _get(db: OrmSession, feedback_id: str, category: str) -> Feedback:
    fb = db.get(Feedback, feedback_id)
    if fb is None or fb.category != category:
        raise HTTPException(404, "反馈不存在")
    return fb


@router.get("/staff/feedback")
def list_feedback(status: str = "", page: int = 1, size: int = 20,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_admin)):
    """平台问题列表（志愿反馈另走 /staff/volunteer-feedback）。"""
    q = db.query(Feedback).filter(Feedback.category == "platform")
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
    items = [_out(db, r, shots[r.id], submitter=people.get(r.user_id),
                  handler=people.get(r.handled_by or "")) for r in rows]
    open_count = db.query(Feedback).filter(Feedback.category == "platform",
                                           Feedback.status == "open").count()
    return {"total": total, "page": page, "size": size, "items": items,
            "open": open_count}


class HandleBody(BaseModel):
    reply: str = ""


@router.post("/staff/feedback/{feedback_id}/handle")
def handle_feedback(feedback_id: str, body: HandleBody,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_admin)):
    """标记已处理并回复。已处理的可以再改回复，每次都通知提交人。"""
    fb = _get(db, feedback_id, "platform")
    reply = body.reply.strip()
    if len(reply) > config.FEEDBACK_MAX_CHARS:
        raise HTTPException(400, f"回复不能超过 {config.FEEDBACK_MAX_CHARS} 字")
    again = fb.status == "handled"
    fb.status, fb.reply = "handled", reply
    fb.handled_by, fb.handled_at = user.id, now()
    submitter = db.get(User, fb.user_id)
    if submitter is not None:
        notify(db, submitter, "意见反馈已处理",
               f"您提交的意见反馈「{_brief(fb.content)}」已处理。"
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
    return _out(db, fb, shots[fb.id], submitter=submitter, handler=user)


# ======================= 对外联络部、会长团与管理员：志愿反馈 =======================

def require_vb_feedback(user: User = Depends(current_user),
                        db: OrmSession = Depends(db_session)) -> User:
    if not liaison.feedback_handler(db, user):
        raise HTTPException(403, f"需要 {liaison.vb_dept_name(db)}骨干、部长或会长团、管理员 身份")
    return user


@router.get("/staff/volunteer-feedback")
def list_volunteer_feedback(status: str = "", kind: str = "", activity_id: str = "",
                            page: int = 1, size: int = 20,
                            db: OrmSession = Depends(db_session),
                            user: User = Depends(require_vb_feedback)):
    """志愿反馈列表，可按状态、意见类型、关联活动筛。匿名的不带提交人。

    每条带 reply_draft：点「回复」时预填的模板，意见类型和日期已经填好。
    """
    q = db.query(Feedback).filter(Feedback.category == "volunteer")
    if status:
        if status not in Feedback.STATUS_LABELS:
            raise HTTPException(400, "状态不合法")
        q = q.filter(Feedback.status == status)
    if kind:
        if kind not in Feedback.KIND_LABELS:
            raise HTTPException(400, "意见类型不合法")
        q = q.filter(Feedback.kind == kind)
    if activity_id:
        q = q.filter(Feedback.activity_id == activity_id)
    page, size = max(page, 1), min(max(size, 1), 100)
    total = q.count()
    rows = (q.order_by(Feedback.created_at.desc())
            .offset((page - 1) * size).limit(size).all())
    shots = _shots(db, [r.id for r in rows])
    # 匿名的提交人不查：查都不查，就不会有哪条路径把他带出去
    want = ({r.user_id for r in rows if not r.anonymous}
            | {r.handled_by for r in rows if r.handled_by})
    people = {u.id: u for u in db.query(User).filter(User.id.in_(want or ["__none__"]))}
    items = []
    for r in rows:
        d = _out(db, r, shots[r.id],
                 submitter=people.get(r.user_id) if not r.anonymous else None,
                 handler=people.get(r.handled_by or ""))
        if r.anonymous:
            d["user"] = None
        d["reply_draft"] = reply_draft(r)
        items.append(d)
    open_count = db.query(Feedback).filter(Feedback.category == "volunteer",
                                           Feedback.status == "open").count()
    # 筛选用的活动下拉：只列被关联过的活动
    acts = (db.query(Activity.id, Activity.title)
            .join(Feedback, Feedback.activity_id == Activity.id)
            .filter(Feedback.category == "volunteer")
            .distinct().all())
    return {"total": total, "page": page, "size": size, "items": items,
            "open": open_count,
            "kinds": [{"value": k, "label": v} for k, v in Feedback.KIND_LABELS.items()],
            "activities": [{"id": a, "title": t} for a, t in acts]}


@router.post("/staff/volunteer-feedback/{feedback_id}/reply")
def reply_volunteer_feedback(feedback_id: str, body: HandleBody,
                             db: OrmSession = Depends(db_session),
                             user: User = Depends(require_vb_feedback)):
    """回复志愿反馈：状态变已处理，通知提交人。已处理的可以再改回复，每次都通知。

    提交人的通知不记收件人身份给通知记录看（anon_recipient）：匿名的一条
    要是在「通知记录」里显示收件人，等于告诉管理员是谁提的。
    """
    fb = _get(db, feedback_id, "volunteer")
    reply = body.reply.strip()
    if not reply:
        raise HTTPException(400, "请填写回复内容")
    if len(reply) > config.FEEDBACK_MAX_CHARS:
        raise HTTPException(400, f"回复不能超过 {config.FEEDBACK_MAX_CHARS} 字")
    again = fb.status == "handled"
    fb.status, fb.reply = "handled", reply
    fb.handled_by, fb.handled_at = user.id, now()
    submitter = db.get(User, fb.user_id)
    if submitter is not None:
        data = {"feedback_id": fb.id, "route": "feedback_mine"}
        if fb.anonymous:
            data["anon_recipient"] = True
        notify(db, submitter, "志愿者反馈已回复",
               f"您提交的志愿者反馈【{fb.kind_label()}】已回复：\n{reply}"
               "\n可在右上角菜单「意见反馈 → 我的反馈」查看。", data=data)
    audit_log(db, actor=user, action="feedback", target_type="feedback",
              target_id=fb.id, target_label=fb.kind_label(),
              summary="修改志愿反馈回复" if again else "回复志愿反馈")
    db.commit()
    shots = _shots(db, [fb.id])
    d = _out(db, fb, shots[fb.id], handler=user,
             submitter=None if fb.anonymous else submitter)
    if fb.anonymous:
        d["user"] = None
    d["reply_draft"] = reply_draft(fb)
    return d
