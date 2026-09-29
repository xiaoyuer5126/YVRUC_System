"""活动阶段闸门：到了哪一步就不许再动哪些东西。

两条线，各管一段：

  · 活动结束（finished/vb_pending/completed）——安排定死了。时长是按结束那一刻的岗位与
    名单算出来的，事后再改岗位、改名额、改基本信息，改的是一份已经生效的
    记录，跟结出来的账对不上。
  · 时长封账——核准之后一旦提交校青协审核就冻结，锁定之后更不许动。审核期间
    也拦，是因为部长看的必须和最终封的是同一份账。要改就先申请解锁。

分两条而不是一条：活动结束之后、时长锁定之前还有一段，补签到、补招人、补录
时长都在这一段里发生——账还没结完，这些正是结账要用的工具。
"""
from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session as OrmSession

from . import hours as hours_flow
from .models import Activity


def ensure_not_finished(act: Activity) -> None:
    """活动还没结束才让改。"""
    if act.status in ("finished", "vb_pending", "completed"):
        raise HTTPException(
            409, f"活动{act.label_status()}，不能再修改；如确需更正请联系管理员")


def ensure_hours_editable(db: OrmSession, act: Activity) -> None:
    """时长这本账还开着才让改。"""
    if act.hours_locked():
        raise HTTPException(
            409, f"「{act.title}」的时长已于 "
                 f"{act.hours_locked_at:%Y-%m-%d %H:%M} 锁定，"
                 f"如需修改请先申请解锁并由校青协审批")
    if hours_flow.pending_lock(db, act.id) is not None:
        raise HTTPException(
            409, f"「{act.title}」的时长正在等校青协审批锁定，"
                 f"审批期间不能修改；如需改动请让审批人先驳回")
