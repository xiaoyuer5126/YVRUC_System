"""智能起草（预览版）的接口。只起草、不落库，见 backend/ai.py；附件见 ai_files.py。

三个接口都收表单：text 是贴进来的原文，files 是附件（可以没有）。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session as OrmSession

from .. import ai, ai_files, config, locks
from ..database import db_session
from ..deps import require_manager
from ..models import Activity, User
from ..perm import ensure_activity_manager
from .screening import EDITABLE_FORM_STATUSES

router = APIRouter(prefix="/api/staff/ai", tags=["ai"])


def _prepare(user: User, text: str, files: list[UploadFile],
             min_len: int = 10) -> tuple[str, ai_files.Extracted]:
    """没开、原文太短太长、这一小时用得太多，都在打到模型之前拦下。

    原文 = 贴的字 + 附件转出来的字。只传了图片的，字少也放行（内容在图里）；
    出题可以什么都不给（min_len=0）：活动简介和岗位已经在库里，按它们出就行。
    """
    if not ai.enabled():
        raise HTTPException(409, "智能起草还没有启用，请联系管理员")
    # 先占后判：先自增再比，两个请求同时进来不会双双看到「还没到上限」。
    # 放在解析附件之前——附件转文字要读图、花时间，限流拦下来的请求不该先做这些
    key = f"ai:{user.id}"
    locks.note_rate(key, 3600)
    if locks.rate_count(key) > config.AI_HOURLY_LIMIT:
        raise HTTPException(429, "这一小时用得有点多了，过一会儿再试")
    ex = ai_files.extract(files)
    full = "\n\n".join(x for x in [text.strip(), *ex.texts] if x)
    if len(full) < min_len and not ex.images:
        raise HTTPException(400, "原文太短了，多贴一些活动信息或者传个文件再试")
    if len(full) > config.AI_INPUT_MAX:
        raise HTTPException(400, f"原文太长了（{len(full)} 字），请删到 "
                                 f"{config.AI_INPUT_MAX} 字以内，或者少传几个文件")
    return full, ex


def _run(fn, ex: ai_files.Extracted, *args) -> dict:
    try:
        out = fn(*args, ex.images)
    except ai.AiError as e:
        raise HTTPException(502, str(e))
    out["notes"] = out["notes"] + ex.notes      # 读不了的图、超了数的图也要告诉人
    return out


def _activity(db: OrmSession, user: User, activity_id: str) -> Activity:
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


@router.post("/activity-draft")
def activity_draft(text: str = Form(""), files: list[UploadFile] = File(default=[]),
                   user: User = Depends(require_manager)):
    """起草新建活动表单。草稿只回给前端填表，不建活动。"""
    full, ex = _prepare(user, text, files)
    return _run(ai.activity_draft, ex, full)


@router.post("/activities/{activity_id}/structure-draft")
def structure_draft(activity_id: str, text: str = Form(""),
                    files: list[UploadFile] = File(default=[]),
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """起草这场活动的场次与岗位（含培训、彩排）。草稿只回给前端预览，建不建、怎么改由管理者定。"""
    act = _activity(db, user, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动不设场次与岗位")
    full, ex = _prepare(user, text, files)
    return _run(ai.structure_draft, ex, act, full)


@router.post("/activities/{activity_id}/form-draft")
def form_draft(activity_id: str, text: str = Form(""),
               files: list[UploadFile] = File(default=[]),
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """起草报名问卷的题目。草稿只回给出题弹窗，加不加、怎么改、保存都由管理者定。"""
    act = _activity(db, user, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动不设报名问卷")
    if act.status not in EDITABLE_FORM_STATUSES:
        raise HTTPException(409, "活动已公开，报名问卷不能再改")
    full, ex = _prepare(user, text, files, min_len=0)
    return _run(ai.form_draft, ex, act, full)
