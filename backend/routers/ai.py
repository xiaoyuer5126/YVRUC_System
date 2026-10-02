"""智能起草（预览版）的接口。只起草、不落库，见 backend/ai.py。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import ai, config, locks
from ..database import db_session
from ..deps import require_manager
from ..models import Activity, User
from ..perm import ensure_activity_manager
from .screening import EDITABLE_FORM_STATUSES

router = APIRouter(prefix="/api/staff/ai", tags=["ai"])


class DraftBody(BaseModel):
    text: str = ""


def _prepare(user: User, body: DraftBody, min_len: int = 10) -> str:
    """没开、原文太短太长、这一小时用得太多，都在打到模型之前拦下。

    出题可以不写原文（min_len=0）：活动简介和岗位已经在库里，按它们出就行。
    """
    if not ai.enabled():
        raise HTTPException(409, "智能起草还没有启用，请联系管理员")
    text = body.text.strip()
    if len(text) < min_len:
        raise HTTPException(400, "原文太短了，多贴一些活动信息再试")
    if len(text) > config.AI_INPUT_MAX:
        raise HTTPException(400, f"原文太长了（{len(text)} 字），请删到 "
                                 f"{config.AI_INPUT_MAX} 字以内")
    key = f"ai:{user.id}"
    if locks.rate_count(key) >= config.AI_HOURLY_LIMIT:
        raise HTTPException(429, "这一小时用得有点多了，过一会儿再试")
    locks.note_rate(key, 3600)
    return text


def _run(fn, *args) -> dict:
    try:
        return fn(*args)
    except ai.AiError as e:
        raise HTTPException(502, str(e))


@router.post("/activity-draft")
def activity_draft(body: DraftBody, user: User = Depends(require_manager)):
    """起草新建活动表单。草稿只回给前端填表，不建活动。"""
    return _run(ai.activity_draft, _prepare(user, body))


def _activity(db: OrmSession, user: User, activity_id: str) -> Activity:
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


@router.post("/activities/{activity_id}/structure-draft")
def structure_draft(activity_id: str, body: DraftBody,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """起草这场活动的场次与岗位（含培训、彩排）。草稿只回给前端预览，建不建、怎么改由管理者定。"""
    act = _activity(db, user, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动不设场次与岗位")
    return _run(ai.structure_draft, act, _prepare(user, body))


@router.post("/activities/{activity_id}/form-draft")
def form_draft(activity_id: str, body: DraftBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """起草报名问卷的题目。草稿只回给出题弹窗，加不加、怎么改、保存都由管理者定。"""
    act = _activity(db, user, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动不设报名问卷")
    if act.status not in EDITABLE_FORM_STATUSES:
        raise HTTPException(409, "活动已公开，报名问卷不能再改")
    return _run(ai.form_draft, act, _prepare(user, body, min_len=0))
