"""志愿时长的结账：核准 → 本人确认或申诉 → 锁定。

一场活动的账一起结，状态记在活动上（hours_approved_at / appeal_deadline /
hours_locked_at）；每个人的表态记在自己那条 HoursRecord 上（confirmed_at /
reward）。这里放接口与定时任务都要用的那几步，免得两边各写一份。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from .audit import log as audit_log
from .integrations import wecom
from .models import (Activity, Enrollment, HoursRecord, Position, User, now)


def default_appeal_deadline() -> datetime:
    """默认申诉截止：第二天中午。大家一般当天就自己看了，留一夜足够。"""
    t = now() + timedelta(days=1)
    return t.replace(hour=12, minute=0, second=0, microsecond=0)


def pay_position(db: OrmSession, activity_id: str, user_id: str) -> Position | None:
    """这个人在本活动里正式报名的岗位中，第一个标了「可选劳务」的。
    没有就是只能记志愿时长。"""
    rows = (db.query(Position)
            .join(Enrollment, Enrollment.position_id == Position.id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.user_id == user_id,
                    Enrollment.status == "enrolled",
                    Position.pay_optional.is_(True))
            .order_by(Position.sort).all())
    return rows[0] if rows else None


def result_text(rec: HoursRecord) -> str:
    """通知里的一句话：记几小时，还是按劳务计。"""
    if rec.counts_hours():
        return f"本次记 {rec.hours:g} 小时"
    return "本次按劳务计，不记入志愿时长"


def lock_hours(db: OrmSession, act: Activity, actor: User | None,
               reason: str) -> int:
    """锁定这场活动的时长，活动落「已完成」，逐个通知本人。不提交事务。

    没表态的人按「没问题 + 志愿时长」处理，锁定时补上确认时间；
    这样锁定之后每条记录都有本人（或视同本人）的确认。
    """
    t = now()
    rows = db.query(HoursRecord).filter(HoursRecord.activity_id == act.id).all()
    for r in rows:
        if r.confirmed_at is None:
            r.confirmed_at = t
            r.reward = "hours"
    act.hours_locked_at = t
    act.status = "completed"
    audit_log(db, actor=actor, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"锁定志愿时长，活动标记为已完成（{reason}）")
    for r in rows:
        u = db.get(User, r.user_id)
        if u is not None:
            wecom.send_user(db, u, "hours_locked", data={"activity_id": act.id},
                            activity=act.title, result=result_text(r))
    return len(rows)
