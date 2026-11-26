"""活动上新提醒：志愿者订阅每周空闲时段、关注的主单位和活动名关键词，新活动上广场时提醒。

订阅存在 ActivityWatch（一人一行）。活动第一次上广场（审批通过即发布，或者
停在「待发布」后由发起方点发布，两条路都走 staff_activities.publish_approved）
时按订阅匹配一轮，同一个活动只发这一次（Activity.new_alert_at）。

匹配规则：
  · 有一个服务场次的某个时段落在勾选的格子里，或者主单位在关注列表里，或者
    活动名包含某个关键词，满足一样就发，一人只发一条；
  · 关键词匹配忽略大小写和首尾空格（存的时候已去掉首尾空格）；
  · 只发给招募范围内的人；老师不参与报名，不发；
  · 补记录活动不上广场，不发。

发送放在请求之后的后台任务里（publish_queue → send_bg）：订阅的人多时逐个
落库要好几秒，审批人点「通过」不该等这个。
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta

from fastapi import BackgroundTasks
from sqlalchemy.orm import Session as OrmSession

from . import config
from .integrations import msgcenter
from .models import Activity, ActivityWatch, User, day_text, now
from .notify import rows_by_id

log = logging.getLogger(__name__)

# 一天分三段：(键, 名称, 起始钟点, 结束钟点)，左闭右开。
# 上午从 0 点算起、晚上算到 24 点，一天里每个钟点都归到某一段：凌晨出发的
# 活动（比如 5 点集合去长城）算上午，不然勾满了整周也匹配不上它。
# 12 点、18 点是大家说「下午」「晚上」的自然分界。
PARTS = (("am", "上午", 0, 12), ("pm", "下午", 12, 18), ("night", "晚上", 18, 24))
PART_KEYS = tuple(p[0] for p in PARTS)
SLOT_KEYS = tuple(f"{d}-{p}" for d in range(7) for p in PART_KEYS)
# 关注的主单位最多几个、每个多长（主单位列宽 64）
MAX_ORGANIZERS = 50
# 活动名关键词：每人最多几个、每个最长几个字。活动名一般十来个字，
# 关键词比 20 字还长基本就是整个标题，匹配不上什么新活动。
MAX_KEYWORDS = 5
MAX_KEYWORD_LEN = 20


def clean_keywords(raw: list[str]) -> list[str]:
    """去掉首尾空格和空串，忽略大小写去重（留第一次的写法），顺序不变。"""
    out: list[str] = []
    seen: set[str] = set()
    for k in raw:
        k = (k or "").strip()
        if k and k.casefold() not in seen:
            seen.add(k.casefold())
            out.append(k)
    return out


def keyword_hit(title: str, keywords: list[str]) -> str | None:
    """活动名命中的第一个关键词（订阅里的原写法），没命中返回 None。"""
    t = (title or "").casefold()
    for k in keywords or []:
        k = (k or "").strip()
        if k and k.casefold() in t:
            return k
    return None


def _day_slots(day, start: datetime, end: datetime, whole_day: bool) -> set[str]:
    """[start, end) 在 day 这一天落到哪几段。whole_day 时这一天三段都算。"""
    base = datetime.combine(day, time.min)
    out = set()
    for key, _label, h0, h1 in PARTS:
        lo, hi = base + timedelta(hours=h0), base + timedelta(hours=h1)
        if whole_day or (start < hi and end > lo):
            out.add(f"{day.weekday()}-{key}")
    return out


def period_slots(start: datetime, end: datetime, flexible: bool = False) -> set[str]:
    """一个时段沾到每周的哪几格。

    时间灵活的场次钟点只是个大概，和广场「按时段筛选」一样按日子算：
    它沾到的那几天，三段都算。跨了整整一周的时段每一格都沾到。
    """
    if end - start >= timedelta(days=7):
        return set(SLOT_KEYS)
    out: set[str] = set()
    day = start.date()
    # 结束在午夜整点的，不算沾到第二天
    last = (end - timedelta(microseconds=1)).date()
    while day <= last:
        out |= _day_slots(day, start, end, flexible)
        day += timedelta(days=1)
    return out


def activity_slots(act: Activity) -> set[str]:
    """活动的服务场次沾到的格子。培训/彩排不算，已经过去的时段不算。"""
    t = now()
    out: set[str] = set()
    for s in act.main_shifts():
        for p in s.periods:
            if p.end_time > t:
                out |= period_slots(p.start_time, p.end_time, bool(s.flexible_time))
    return out


def organizer_options(db: OrmSession) -> list[str]:
    """订阅时可选的主单位：已公开活动的主单位去重。手填的原样列出，不合并写法。"""
    from .routers.activities import PUBLIC_STATUSES
    return [o for (o,) in db.query(Activity.organizer)
            .filter(Activity.status.in_(PUBLIC_STATUSES),
                    Activity.recruit_mode != config.RECORD_MODE,
                    Activity.organizer != "")
            .distinct().order_by(Activity.organizer).all()]


def claim(act: Activity) -> bool:
    """活动上广场时调用：这个活动还没发过上新提醒就记下此刻，返回 True。

    在发布的同一个事务里记，发布回滚了就当没发生；发过的不再发。
    """
    if act.is_record() or act.new_alert_at is not None:
        return False
    act.new_alert_at = now()
    return True


def queue(bg: BackgroundTasks, act: Activity, claimed: bool) -> None:
    """发布接口提交事务之后，把这一轮提醒交给后台任务。"""
    if claimed:
        bg.add_task(send_bg, act.id)


def _reg_line(act: Activity) -> str:
    """报名开始那一行。还没开始的顺带指一下活动页上的开抢提醒。"""
    if act.reg_start is None or act.reg_start <= now():
        return "现在已可报名，报名先到先得。"
    return (f"报名开始：{day_text(act.reg_start)} {act.reg_start:%H:%M}\n"
            f"可在活动页订阅「报名开始前 {config.REG_REMINDER_MINUTES} 分钟提醒」。")


def _reason(slot_hit: bool, org_hit: bool, organizer: str,
            keyword: str | None = None) -> str:
    parts = []
    if slot_hit:
        parts.append("有岗位的时段落在你订阅的空闲时段里")
    if org_hit:
        parts.append(f"由你关注的「{organizer}」举办")
    if keyword:
        parts.append(f"活动名包含你订阅的关键词「{keyword}」")
    return "，".join(parts)


def send(db: OrmSession, act: Activity) -> int:
    """按订阅给这个活动发一轮上新提醒，返回发了几人。不提交事务。"""
    if act.is_record() or act.status != "published":
        return 0
    from .enroll_logic import unmet_recruit_scope

    hits = activity_slots(act)
    organizer = (act.organizer or "").strip()
    matched: dict[str, tuple[bool, bool, str | None]] = {}
    for w in db.query(ActivityWatch).all():
        slot_hit = bool(hits & set(w.slots or []))
        org_hit = bool(organizer) and organizer in (w.organizers or [])
        kw = keyword_hit(act.title, w.keywords)
        if slot_hit or org_hit or kw:
            matched[w.user_id] = (slot_hit, org_hit, kw)
    if not matched:
        return 0
    users = rows_by_id(db, User, matched)
    reg_line = _reg_line(act)
    sent = 0
    for user_id, (slot_hit, org_hit, kw) in matched.items():
        u: User | None = users.get(user_id)
        if u is None or u.user_type == "teacher" or unmet_recruit_scope(u, act):
            continue
        msgcenter.send_user(db, u, "new_activity", data={"activity_id": act.id},
                            activity=act.title,
                            reason=_reason(slot_hit, org_hit, organizer, kw),
                            organizer=act.organizer_text() or "未填写",
                            time=act.headline_time() or "待定",
                            reg_line=reg_line)
        sent += 1
    return sent


def send_bg(activity_id: str) -> None:
    """后台任务入口：自带数据库会话。"""
    from .database import SessionLocal
    db = SessionLocal()
    try:
        act = db.get(Activity, activity_id)
        if act is not None:
            n = send(db, act)
            db.commit()
            log.info("活动上新提醒「%s」发送 %d 人", act.title, n)
    finally:
        db.close()
