"""内部提示分。

只给青协工作人员看，**不对志愿者公开，也不拦任何人报名**——它唯一的作用是
在名单上给个感叹号，提醒排班的人点开档案多看一眼。所以分数不落库、不做流水，
每次按当下的记录现算：改了权重、销了一条违规记录，分数立刻跟着变，
不会留下一堆对不上的历史值。

从满分起算：违规按类型扣分，临近退选也扣；顺利参与一次活动加分，
分数自己能长回来——不能让人因为一次失误就永远挂着感叹号。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from . import config
from .models import Enrollment, Shift, User, ViolationRecord, now


def _counts(db: OrmSession, user_id: str) -> tuple[dict[str, int], int, int]:
    """(各类违规次数, 临近退选次数, 顺利参与次数)。"""
    violations: dict[str, int] = {}
    for v in (db.query(ViolationRecord)
              .filter(ViolationRecord.user_id == user_id).all()):
        violations[v.kind] = violations.get(v.kind, 0) + 1

    rows = (db.query(Enrollment, Shift)
            .join(Shift, Enrollment.shift_id == Shift.id)
            .filter(Enrollment.user_id == user_id,
                    Shift.parent_id.is_(None)).all())
    late = sum(1 for e, _g in rows if e.cancel_type == "late")
    # 「顺利参与」= 正式报名 + 场次已经过去。没去成的（违规）另有扣分抵掉这一次
    done = sum(1 for e, g in rows
               if e.status == "enrolled" and g.end_time < now())
    return violations, late, done


def score_of(db: OrmSession, user_id: str) -> int:
    violations, late, done = _counts(db, user_id)
    score = config.SCORE_BASE
    for kind, n in violations.items():
        score -= config.VIOLATION_WEIGHTS.get(kind, 0) * n
    score -= config.LATE_CANCEL_WEIGHT * late
    score += config.SCORE_PER_ACTIVITY * done
    return max(config.SCORE_MIN, min(config.SCORE_BASE, score))


def brief(db: OrmSession, user_id: str) -> dict:
    """名单上要的那点东西：分数，以及要不要挂感叹号。"""
    score = score_of(db, user_id)
    return {"score": score, "score_alert": score < config.SCORE_ALERT_BELOW}


def detail(db: OrmSession, user: User) -> dict:
    """档案里的明细：这个分是怎么来的，每一项各扣/加了多少。"""
    violations, late, done = _counts(db, user.id)
    items = [{"label": "起始分", "count": 1, "delta": config.SCORE_BASE}]
    for kind, n in sorted(violations.items()):
        items.append({"label": ViolationRecord.KINDS.get(kind, kind), "count": n,
                      "delta": -config.VIOLATION_WEIGHTS.get(kind, 0) * n})
    if late:
        items.append({"label": "临近退选", "count": late,
                      "delta": -config.LATE_CANCEL_WEIGHT * late})
    if done:
        items.append({"label": "顺利参与活动", "count": done,
                      "delta": config.SCORE_PER_ACTIVITY * done})
    return {**brief(db, user.id), "alert_below": config.SCORE_ALERT_BELOW,
            "per_activity": config.SCORE_PER_ACTIVITY, "items": items}
