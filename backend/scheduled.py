"""定时任务：候补清扫、活动状态自动流转、签到提醒、人员变动日报、编辑解冻。

由 main.py 的协程每分钟驱动一次。几件事互不依赖，一件出错不影响其余——
任何一件长期不跑都没有外部表现，直到有人发现名额空转或提醒没发出去。
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session as OrmSession

from . import config
from .audit import log as audit_log
from .enroll_logic import expire_stale_promotions
from .integrations import wecom
from .models import Activity, Shift, User, now
from .notify import notify_checkin, notify_enrolled
from .perm import activity_manager_ids

logger = logging.getLogger(__name__)

REMINDER_LEAD = timedelta(minutes=config.CHECKIN_REMINDER_MINUTES)


def run_once(db: OrmSession) -> dict[str, int]:
    """跑一轮。每件事单独兜异常，返回各自处理的条数。"""
    result = {}
    for name, job in (("promotions", expire_stale_promotions),
                      ("advanced", auto_advance_activities),
                      ("reminders", send_checkin_reminders),
                      ("digests", send_roster_digests),
                      ("unlocked", release_stale_edit_locks)):
        try:
            result[name] = job(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("定时任务 %s 出错", name)
            result[name] = 0
    return result


# ======================= 活动状态自动流转 =======================

def _advance(db: OrmSession, act: Activity, target: str, action: str,
             summary: str) -> None:
    act.status = target
    audit_log(db, actor=None, action=action, target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)


def auto_advance_activities(db: OrmSession) -> int:
    """报名截止 → 已截止；全部场次结束 → 已结束。

    没有这一步的话，活动会永远停在「报名中」，广场上靠展示层按时间猜状态，
    而报名接口只认数据库里的状态——于是页面显示「已结束」却还能报名。
    筹备中的活动不自动流转：它还没公开，进度由老师自己掌握。
    """
    t = now()
    changed = 0
    rows = (db.query(Activity)
            .filter(Activity.status.in_(("published", "closed"))).all())
    for act in rows:
        span = act.time_span()
        if act.status == "published":
            deadline = act.reg_end or (span[0] if span else None)
            if deadline and t > deadline:
                _advance(db, act, "closed", "close",
                         f"报名截止时间（{deadline:%m-%d %H:%M}）已到，系统自动截止报名")
                notify_managers_to_assign(db, act)
                changed += 1
        # 同一轮里接着判：截止后场次也已结束的，一次到位，不用等下一分钟
        if act.status == "closed" and span and t > span[1]:
            _advance(db, act, "finished", "finish",
                     f"全部场次已于 {span[1]:%m-%d %H:%M} 结束，系统自动结束活动")
            notify_enrolled(db, act, "activity_changed", activity=act.title,
                            change_summary="活动已结束，感谢您的参与！时长将陆续录入。")
            changed += 1
    if changed:
        db.commit()
    return changed


def staffing_gaps(db: OrmSession, act: Activity) -> list[str]:
    """报名截止时还没排好的场次，各写一句人话。

    两种缺口：岗位一个都没建，和岗位建了但还有人没分到。
    只看前者的话，「建了 3 个岗位、报了 40 个人」这种最常见的漏排会漏掉。
    """
    from .models import Enrollment

    out = []
    for s in act.shifts:
        if not s.positions:
            out.append(f"「{s.name}」还没有岗位"
                       + ("（截止后统一分配，现在正是建岗位的时候：先改回"
                          "「报名即选岗位」）" if s.needs_assign()
                          else "（学生此前无法报名）"))
            continue
        n = (db.query(Enrollment)
             .filter(Enrollment.shift_id == s.id,
                     Enrollment.status == "enrolled",
                     Enrollment.position_id.is_(None)).count())
        if n:
            out.append(f"「{s.name}」还有 {n} 人没分到岗位")
    return out


def notify_managers_to_assign(db: OrmSession, act: Activity) -> None:
    """报名一截止就提醒管理者去排岗。

    报名截止才是「截止后统一分配」真正开始干活的时候；没人盯着的话，
    这批人会一直挂在「待分配」，直到活动当天才发现没人排班。
    """
    gaps = staffing_gaps(db, act)
    if not gaps:
        return
    lines = "\n".join(f"· {g}" for g in gaps)
    for uid_ in activity_manager_ids(db, act.id):
        who = db.get(User, uid_)
        if who is None:
            continue
        wecom.send_user(db, who, "custom",
                        title=f"报名已截止，请排岗｜{act.title}",
                        content=f"「{act.title}」报名已截止，还有场次没排完：\n"
                                f"{lines}\n"
                                f"请进入活动管理的「岗位树」补齐岗位，"
                                f"再到「报名与调配」运行一键分配。")
    logger.info("活动「%s」已截止，提醒管理者排岗：%s", act.title, "；".join(gaps))


# ======================= 编辑冻结兜底 =======================

def release_stale_edit_locks(db: OrmSession) -> int:
    """超时没点「保存并发布」就自动解冻。

    管理者点了「调整安排」转头去开会，学生一下午都报不了名，而这件事
    没有任何外部表现——除了学生以为活动黄了。
    """
    deadline = now() - timedelta(hours=config.EDIT_LOCK_HOURS)
    rows = (db.query(Activity)
            .filter(Activity.editing_since.isnot(None),
                    Activity.editing_since < deadline).all())
    for act in rows:
        who = db.get(User, act.editing_by) if act.editing_by else None
        act.editing_since = None
        act.editing_by = None
        audit_log(db, actor=None, action="update", target_type="activity",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=f"编辑态超过 {config.EDIT_LOCK_HOURS} 小时未保存，"
                          f"系统自动解冻并恢复报名")
        if who is not None:
            wecom.send_user(db, who, "custom",
                            title=f"报名已自动恢复｜{act.title}",
                            content=f"您对「{act.title}」的编辑态超过 "
                                    f"{config.EDIT_LOCK_HOURS} 小时未保存，"
                                    f"系统已自动解冻并恢复报名。\n"
                                    f"如仍需调整，请重新点击「调整安排」。")
        logger.info("活动「%s」编辑态超时，已自动解冻", act.title)
    if rows:
        db.commit()
    return len(rows)


# ======================= 签到提醒 =======================

def send_checkin_reminders(db: OrmSession) -> int:
    """场次开始前 CHECKIN_REMINDER_MINUTES 分钟推一次签到提醒。

    只推一次（reminder_sent_at 落库），且只推还没开始的场次——服务停过一段时间
    再起来，不该把早就结束的场次的提醒补发出去。
    """
    t = now()
    shifts = (db.query(Shift)
              .join(Activity, Shift.activity_id == Activity.id)
              .filter(Shift.reminder_sent_at.is_(None),
                      Shift.start_time > t,
                      Shift.start_time <= t + REMINDER_LEAD,
                      Activity.status.in_(("published", "closed"))).all())
    sent = 0
    for g in shifts:
        n = notify_checkin(db, g)
        g.reminder_sent_at = t
        sent += n
        logger.info("场次「%s」签到提醒已发送 %d 人", g.name, n)
    if shifts:
        db.commit()
    return sent


# ======================= 人员变动日报 =======================

def send_roster_digests(db: OrmSession) -> int:
    """活动前几天，每天给管理者一条人员变动摘要。

    逐条推变动会被无视（报名期一天几十条），完全不推又会让老师在现场
    才发现少人。折中：临近活动时每天一条「昨日 +2 −1，当前 18/20」，
    真正紧急的（距开始不足 24 小时的减员）另有即时提醒。
    """
    from .enroll_logic import ROSTER_ACTIONS, shift_enrolled_count
    from .models import AuditLog
    from .notify import notify_managers

    t = now()
    today = t.replace(hour=0, minute=0, second=0, microsecond=0)
    lead = timedelta(days=config.ROSTER_DIGEST_DAYS)
    shifts = (db.query(Shift)
              .join(Activity, Shift.activity_id == Activity.id)
              .filter(Shift.start_time > t,
                      Shift.start_time <= t + lead,
                      Activity.status.in_(("published", "closed")))
              .all())
    sent = 0
    for g in shifts:
        if g.digest_sent_on and g.digest_sent_on >= today:
            continue
        since = t - timedelta(days=1)
        rows = (db.query(AuditLog)
                .filter(AuditLog.activity_id == g.activity_id,
                        AuditLog.action.in_(tuple(ROSTER_ACTIONS)),
                        AuditLog.created_at >= since).all())
        g.digest_sent_on = t
        if not rows:
            continue          # 没有变动就不打扰
        joined = sum(1 for r in rows if r.action in ("enroll", "promote"))
        left = sum(1 for r in rows if r.action in ("cancel", "abandon"))
        enrolled = shift_enrolled_count(db, g.id)
        capacity = g.total_capacity()
        act = db.get(Activity, g.activity_id)
        gap = max(0, capacity - enrolled)
        notify_managers(
            db, act.id, f"[人员变动] {act.title} · {g.name}",
            f"过去 24 小时：新增 {joined} 人，退出 {left} 人。\n"
            f"当前 {enrolled}/{capacity} 人"
            + (f"，缺 {gap} 人。" if gap else "，人数已满。")
            + f"\n场次开始：{g.start_time:%m-%d %H:%M}")
        sent += 1
    if shifts:
        db.commit()
    return sent
