"""内部提示分。

只给青协工作人员看，**不对志愿者公开，也不拦任何人报名**——它唯一的作用是
在名单上给个感叹号，提醒排班的人点开档案多看一眼。所以分数不落库、不做流水，
每次按当下的记录现算：改了权重、销了一条违规记录，分数立刻跟着变，
不会留下一堆对不上的历史值。

从满分起算，**按活动计、按时间顺序逐条累计**，每一步都夹在
[SCORE_MIN, SCORE_BASE] 里——满分时加的分不会「存」起来抵后面的扣分：

- 违规：一条扣一次 VIOLATION_WEIGHTS[kind]，时间 = 登记时间；
- 临期退选：同一活动不管退了几个场次只扣一次 LATE_CANCEL_WEIGHT，
  时间 = 该活动最早一次临期退选；
- 顺利参与：该活动至少签到过一次（扫码或管理者补签都算）、没有违规记录、
  没有临期退选，加一次 SCORE_PER_ACTIVITY；时间 = 他报的主场次里最后
  一个的结束时间，还没到就先不加。
  补记录活动（不公开招募）没有报名和签到：**导入名单即视为参与**，没有违规
  记录就加一次；时间 = 活动结束时间（held_end）。它不可能有临期退选。

分数自己能长回来——不能让人因为一次失误就永远挂着感叹号。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session as OrmSession

from . import config
from .models import (Activity, CheckinRecord, Enrollment, HoursRecord, Shift,
                     User, ViolationRecord, iso, now)


@dataclass(frozen=True)
class _Event:
    at: datetime
    delta: int
    label: str            # 违规类型 / 临期退选 / 顺利参与
    activity_title: str
    ref: str              # 违规记录 id 或活动 id，只用来让排序稳定

    def sort_key(self):
        # 同一时刻扣分排在加分前面：先扣再加，加的分才补得回来；反过来满分时
        # 先加会被上限吞掉、再扣就白扣了。ref 兜底，不依赖查询返回的顺序
        return (self.at, self.delta > 0, self.ref)


def _events_many(db: OrmSession, user_ids: list[str]) -> dict[str, list[_Event]]:
    """一次查一批人的加扣分事件，{user_id: 按时间排好的事件}。

    名单上几十个人，一个一个查就是几十条 SQL（实测：29 人的名单 88 条，
    其中三分之二是这儿发的）。这里固定四条：违规、主场次报名、签到、
    补记录名单。
    """
    ids = list(dict.fromkeys(user_ids))       # 去重，别让同一批 id 撑大 IN
    out: dict[str, list[_Event]] = {uid: [] for uid in ids}
    if not ids:
        return out

    violated: set[tuple[str, str]] = set()
    for v in (db.query(ViolationRecord)
              .filter(ViolationRecord.user_id.in_(ids)).all()):
        violated.add((v.user_id, v.activity_id))
        out[v.user_id].append(_Event(
            v.created_at, -config.VIOLATION_WEIGHTS.get(v.kind, 0),
            v.kind_label(), v.activity_title, v.id))

    # 管理者补签也会落一条 CheckinRecord（method=manual），一并算到岗
    checked = set(db.query(CheckinRecord.user_id, CheckinRecord.activity_id)
                  .filter(CheckinRecord.user_id.in_(ids)).distinct().all())

    # (user, activity) → 活动名、最早一次临期退选、报的主场次里最晚的结束时间。
    # 只看主场次：培训/彩排跟着主场次报、跟着主场次退，算进来会重复
    titles: dict[tuple[str, str], str] = {}
    late: dict[tuple[str, str], datetime] = {}
    last_end: dict[tuple[str, str], datetime] = {}
    for e, end, title in (db.query(Enrollment, Shift.end_time, Activity.title)
                          .join(Shift, Enrollment.shift_id == Shift.id)
                          .join(Activity, Enrollment.activity_id == Activity.id)
                          .filter(Enrollment.user_id.in_(ids),
                                  Shift.kind == "service").all()):
        key = (e.user_id, e.activity_id)
        titles[key] = title
        if e.cancel_type == "late":
            at = e.cancelled_at or end       # 没记退选时间的，按场次结束兜底
            late[key] = min(at, late.get(key, at))
        if e.status == "enrolled":
            last_end[key] = max(end, last_end.get(key, end))

    for (uid, aid), at in late.items():
        out[uid].append(_Event(at, -config.LATE_CANCEL_WEIGHT, "临期退选",
                               titles[(uid, aid)], aid))
    t = now()
    for key, end in last_end.items():
        if end < t and key in checked and key not in violated and key not in late:
            out[key[0]].append(_Event(end, config.SCORE_PER_ACTIVITY, "顺利参与",
                                      titles[key], key[1]))
    # 补记录活动：签到这一条对它不适用，名单里有他就算参与过（导入即参与）
    for user_id, aid, title, end in (
            db.query(HoursRecord.user_id, Activity.id, Activity.title,
                     Activity.held_end)
            .join(Activity, HoursRecord.activity_id == Activity.id)
            .filter(HoursRecord.user_id.in_(ids),
                    Activity.recruit_mode == config.RECORD_MODE).distinct().all()):
        if end is not None and end < t and (user_id, aid) not in violated:
            out[user_id].append(_Event(end, config.SCORE_PER_ACTIVITY, "顺利参与",
                                       title, aid))
    for evs in out.values():
        evs.sort(key=_Event.sort_key)
    return out


def _replay(events: list[_Event]) -> list[int]:
    """从满分起逐条加减，每一步都夹在 [SCORE_MIN, SCORE_BASE]；返回每步之后的分。"""
    score, after = config.SCORE_BASE, []
    for ev in events:
        score = max(config.SCORE_MIN, min(config.SCORE_BASE, score + ev.delta))
        after.append(score)
    return after


def _brief_of_events(events: list[_Event]) -> dict:
    after = _replay(events)
    score = after[-1] if after else config.SCORE_BASE
    return {"score": score, "score_alert": score < config.SCORE_ALERT_BELOW}


def score_of(db: OrmSession, user_id: str) -> int:
    return brief(db, user_id)["score"]


def brief(db: OrmSession, user_id: str) -> dict:
    """名单上要的那点东西：分数，以及要不要挂感叹号。"""
    return _brief_of_events(_events_many(db, [user_id])[user_id])


def briefs(db: OrmSession, user_ids: list[str]) -> dict[str, dict]:
    """一批人的 {user_id: brief}。名单、导出这类一次看几十个人的地方用它——
    逐人调 brief() 就是每人三条 SQL，几十个人就是几十条。"""
    return {uid: _brief_of_events(evs)
            for uid, evs in _events_many(db, user_ids).items()}


def detail(db: OrmSession, user: User) -> dict:
    """档案里的明细：这个分是怎么一步步来的，按时间列出每次加扣和之后的分。

    delta 是规则上的加扣值；到了上下限实际变化会小一些，看 score 前后差。
    """
    events = _events_many(db, [user.id])[user.id]
    items = [{"at": iso(ev.at), "label": ev.label,
              "activity_title": ev.activity_title,
              "delta": ev.delta, "score": s}
             for ev, s in zip(events, _replay(events))]
    return {**_brief_of_events(events), "base": config.SCORE_BASE,
            "alert_below": config.SCORE_ALERT_BELOW,
            "per_activity": config.SCORE_PER_ACTIVITY, "items": items}
