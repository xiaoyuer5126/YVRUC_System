"""内部提示分。

只给青协工作人员看，**不对志愿者公开，也不拦任何人报名**——它唯一的作用是
在名单上给个感叹号，提醒排班的人点开档案多看一眼。所以分数不落库、不做流水，
每次按当下的记录现算：改了权重、销了一条违规记录，分数立刻跟着变，
不会留下一堆对不上的历史值。

从满分起算：违规按类型扣分，临期退选也扣；顺利参与一次活动加分，
分数自己能长回来——不能让人因为一次失误就永远挂着感叹号。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from . import config
from .models import Enrollment, Shift, User, ViolationRecord, now


def _counts_many(db: OrmSession, user_ids: list[str]
                 ) -> dict[str, tuple[dict[str, int], int, int]]:
    """一次算一批人的计数：{user_id: (各类违规, 临期退选, 顺利参与)}。

    名单上几十个人，一个一个查就是几十条 SQL（实测：29 人的名单 88 条，
    其中三分之二是这儿发的）。
    """
    ids = list(dict.fromkeys(user_ids))       # 去重，别让同一批 id 撑大 IN
    out = {uid: ({}, 0, 0) for uid in ids}
    if not ids:
        return out
    for v in (db.query(ViolationRecord)
              .filter(ViolationRecord.user_id.in_(ids)).all()):
        out[v.user_id][0][v.kind] = out[v.user_id][0].get(v.kind, 0) + 1
    t = now()
    late: dict[str, int] = {}
    done: dict[str, int] = {}
    for e, g in (db.query(Enrollment, Shift)
                 .join(Shift, Enrollment.shift_id == Shift.id)
                 .filter(Enrollment.user_id.in_(ids),
                         Shift.parent_id.is_(None)).all()):
        if e.cancel_type == "late":
            late[e.user_id] = late.get(e.user_id, 0) + 1
        # 「顺利参与」= 正式报名 + 场次已经过去。没去成的（违规）另有扣分抵
        if e.status == "enrolled" and g.end_time < t:
            done[e.user_id] = done.get(e.user_id, 0) + 1
    for uid in ids:
        out[uid] = (out[uid][0], late.get(uid, 0), done.get(uid, 0))
    return out


def _score_of_counts(violations: dict[str, int], late: int, done: int) -> int:
    score = config.SCORE_BASE
    for kind, n in violations.items():
        score -= config.VIOLATION_WEIGHTS.get(kind, 0) * n
    score -= config.LATE_CANCEL_WEIGHT * late
    score += config.SCORE_PER_ACTIVITY * done
    return max(config.SCORE_MIN, min(config.SCORE_BASE, score))


def _brief_of_counts(violations: dict[str, int], late: int, done: int) -> dict:
    score = _score_of_counts(violations, late, done)
    return {"score": score, "score_alert": score < config.SCORE_ALERT_BELOW}


def _counts(db: OrmSession, user_id: str) -> tuple[dict[str, int], int, int]:
    """(各类违规次数, 临期退选次数, 顺利参与次数)。"""
    return _counts_many(db, [user_id])[user_id]


def score_of(db: OrmSession, user_id: str) -> int:
    return _score_of_counts(*_counts(db, user_id))


def brief(db: OrmSession, user_id: str) -> dict:
    """名单上要的那点东西：分数，以及要不要挂感叹号。"""
    return _brief_of_counts(*_counts(db, user_id))


def briefs(db: OrmSession, user_ids: list[str]) -> dict[str, dict]:
    """一批人的 {user_id: brief}。名单、导出这类一次看几十个人的地方用它——
    逐人调 brief() 就是每人两条 SQL，几十个人就是几十条。"""
    return {uid: _brief_of_counts(*c)
            for uid, c in _counts_many(db, user_ids).items()}


def detail(db: OrmSession, user: User) -> dict:
    """档案里的明细：这个分是怎么来的，每一项各扣/加了多少。"""
    violations, late, done = _counts(db, user.id)
    items = [{"label": "起始分", "count": 1, "delta": config.SCORE_BASE}]
    for kind, n in sorted(violations.items()):
        items.append({"label": ViolationRecord.KINDS.get(kind, kind), "count": n,
                      "delta": -config.VIOLATION_WEIGHTS.get(kind, 0) * n})
    if late:
        items.append({"label": "临期退选", "count": late,
                      "delta": -config.LATE_CANCEL_WEIGHT * late})
    if done:
        items.append({"label": "顺利参与活动", "count": done,
                      "delta": config.SCORE_PER_ACTIVITY * done})
    return {**brief(db, user.id), "alert_below": config.SCORE_ALERT_BELOW,
            "per_activity": config.SCORE_PER_ACTIVITY, "items": items}
