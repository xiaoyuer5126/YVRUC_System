"""定时任务：候补清扫、活动状态自动流转、签到提醒、人员变动日报、编辑解冻、
值班日报、审批超时催办、志愿北京外联确认催办与重提提醒。

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
from .integrations import msgcenter
from .models import (Activity, ApprovalEvent, AuditLog, Department,
                     Enrollment, HoursRequest, Shift, User, VbConfirm, now)
from .notify import (notify_checkin, notify_edit_changes, notify_enrolled,
                     notify_managers)
from .perm import activity_manager_ids

logger = logging.getLogger(__name__)

REMINDER_LEAD = timedelta(minutes=config.CHECKIN_REMINDER_MINUTES)


def run_once(db: OrmSession) -> dict[str, int]:
    """跑一轮。每件事单独兜异常，返回各自处理的条数。"""
    result = {}
    for name, job in (("promotions", expire_stale_promotions),
                      # 排在状态流转之前：停机再起来时，先给没处理的申请收口
                      ("undecided", expire_undecided_applications),
                      ("advanced", auto_advance_activities),
                      ("reminders", send_checkin_reminders),
                      ("digests", send_roster_digests),
                      ("approvals", remind_stale_approvals),
                      ("hours_approvals", remind_stale_hours_requests),
                      ("vb_confirms", remind_stale_vb_confirms),
                      ("vb_resubmit", remind_vb_resubmit),
                      ("duty_digests", send_duty_digests),
                      ("hours_lock_requests", submit_lock_when_due),
                      ("unlocked", release_stale_edit_locks),
                      ("sessions", purge_expired_sessions),
                      ("orphan_files", purge_orphan_attachments),
                      ("oplogs", purge_oplogs)):
        try:
            result[name] = job(db)
        except Exception:  # noqa: BLE001
            db.rollback()
            logger.exception("定时任务 %s 出错", name)
            result[name] = 0
    return result


# ======================= 报名后筛选：开场自动落选 =======================

def expire_undecided_applications(db: OrmSession) -> int:
    """意向场次开始时仍没处理的申请自动落选，逻辑见 screening.expire_undecided。"""
    from .routers.screening import expire_undecided
    return expire_undecided(db)


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
                            change_summary="活动已结束，感谢您的参与。时长将陆续录入。")
            changed += 1
    if changed:
        db.commit()
    return changed


def submit_lock_when_due(db: OrmSession) -> int:
    """申诉截止时间到了、又没人申诉的活动，自动提交时长锁定审批。

    锁不锁由校青协拍板，系统只负责把账送上去。有人正在申诉就先放着——
    管理者收到过通知了，处理完再核准一次。
    被驳回的不会再自动送一次：重新核准（hours_approved_at 往后走）之后才算
    新一轮，否则驳回的下一分钟又提交，等于没驳。
    对外活动缺盖章立项书或盖章结项书的不送，提醒管理者一次；补传之后下一轮照常送。
    还没填志愿北京活动名称的也不送（锁定后要推给外联核对，没有名称就推不出去），
    同样每轮提醒管理者一次；填上之后下一轮照常送。
    """
    from . import liaison
    from .hours import submit_lock
    from .models import HoursAppeal, HoursRequest
    from .routers.staff_ops import _notify_lock_approvers

    t = now()
    rows = (db.query(Activity)
            .filter(Activity.hours_locked_at.is_(None),
                    Activity.appeal_deadline.isnot(None),
                    Activity.appeal_deadline < t).all())
    sent = reminded = 0
    for row in rows:
        # 与补记录名单写入、人工核准和人工报批串行；排锁后重读，
        # 名单刚变更并撤销核准的活动不能再按旧截止时间自动报批。
        act = (db.query(Activity).filter(Activity.id == row.id)
               .populate_existing().with_for_update().one())
        if (act.hours_locked_at is not None or act.hours_approved_at is None
                or act.appeal_deadline is None or act.appeal_deadline >= t):
            continue
        pending = (db.query(HoursAppeal)
                   .filter(HoursAppeal.activity_id == act.id,
                           HoursAppeal.status == "pending").count())
        if pending:
            continue
        since = act.hours_approved_at or act.appeal_deadline
        if (db.query(HoursRequest)
                .filter(HoursRequest.activity_id == act.id,
                        HoursRequest.kind == "lock",
                        HoursRequest.created_at >= since).first()):
            continue
        missing = liaison.missing_stamped(db, act)
        if missing:
            # 每一轮自动报批提醒一次：时长每核准一次算新的一轮（since 往后走）。
            # 定时任务每分钟一趟，不记下来就是每分钟一条
            if act.stamped_reminded_at is None or act.stamped_reminded_at < since:
                act.stamped_reminded_at = t
                notify_managers(
                    db, act.id, f"志愿活动请上传材料｜{act.title}",
                    f"「{act.title}」的确认/申诉期已满，本该自动提交时长审核，"
                    f"但这是对外联络部归口的活动，还没有上传{'、'.join(missing)}。\n"
                    f"请到活动管理的「审核」页签上传，上传后系统会自动提交。",
                    duty="hours", tab="review")
                reminded += 1
            continue
        if not act.vb_names:
            if act.vb_names_reminded_at is None or act.vb_names_reminded_at < since:
                act.vb_names_reminded_at = t
                notify_managers(
                    db, act.id, f"请填写志愿北京活动名称｜{act.title}",
                    f"「{act.title}」的确认/申诉期已满，本该自动提交时长审核，"
                    f"但还没有填写志愿北京上的活动名称。\n"
                    f"请到活动管理的「审核」页签填写（可填多个），"
                    f"保存后系统会自动提交；也可以在「时长管理」直接提交时长审核。",
                    duty="hours", tab="review")
                reminded += 1
            continue
        reason = (f"确认/申诉截止（{act.appeal_deadline:%m-%d %H:%M}）无人申诉，"
                  f"系统自动提交；未确认的按志愿时长记")
        if submit_lock(db, act, None, reason) is None:
            continue
        _notify_lock_approvers(db, act, "系统", reason)
        sent += 1
    # 即使这一轮没有报批，也要释放上面拿到的活动行锁。
    db.commit()
    return sent


def staffing_gaps(db: OrmSession, act: Activity) -> list[str]:
    """报名截止时还没排好的场次，各写一句人话。

    两种缺口：真岗位一个都没建，和岗位建了但还有人没分到。
    只看前者的话，「建了 3 个岗位、报了 40 个人」这种最常见的漏排会漏掉。
    「截止后统一分配」的场次自带占位岗位，报名的人全落在它上面——
    占位不算岗位，待在里面的人也不算分到了（口径同管理端的「待分配」人数）。
    """
    from .serializers import _unassigned_count

    out = []
    for s in act.main_shifts():          # 培训/彩排不排岗位
        n = _unassigned_count(db, s)
        if not s.real_positions():
            if s.needs_assign():
                # 没人报就没什么可排的，不打扰
                if n:
                    out.append(f"「{s.name}」还没有岗位，{n} 人等待分配"
                               "（在本场次下建好岗位，再一键分配或导入排班）")
            else:
                out.append(f"「{s.name}」还没有岗位（报名期间学生无法报名该场次）")
            continue
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
    notify_managers(db, act.id, f"报名已截止，请排岗｜{act.title}",
                    f"「{act.title}」报名已截止，还有场次没排完：\n"
                    f"{lines}\n"
                    f"请进入活动管理的「岗位设置」补齐岗位，"
                    f"再到「志愿者管理」运行一键分配岗位。", duty="roster", tab="tree")
    logger.info("活动「%s」已截止，提醒管理者排岗：%s", act.title, "；".join(gaps))


# ======================= 审批超时提醒 =======================

def remind_stale_approvals(db: OrmSession) -> int:
    """待审批放太久就催一次，抄送会长。**永不自动通过**。

    审批是关口，没人看就该一直等着；能自动放行的关口等于没有关口。
    催过一次就记下时间，别每分钟催一遍。
    """
    from .models import ApprovalEvent, Department  # noqa: F401
    from .integrations import msgcenter as _msgcenter

    deadline = now() - timedelta(hours=config.APPROVAL_REMIND_HOURS)
    rows = (db.query(Activity)
            .filter(Activity.status == "pending",
                    Activity.submitted_at.isnot(None),
                    Activity.submitted_at < deadline,
                    Activity.approval_reminded_at.is_(None)).all())
    from . import liaison
    for act in rows:
        who = (db.get(User, act.approval_assignee_id)
               if act.approval_assignee_id else None)
        # 挂在部长名下的、启用值班的部门：催值班的人（没人值班还是部长）
        if who is not None and liaison.is_dept_handler(db, who, act.approval_dept_id):
            who = liaison.approval_contact(db, act.approval_dept_id) or who
        waited = int((now() - act.submitted_at).total_seconds() // 3600)
        text = (f"「{act.title}」（{act.organizer}）已提交 {waited} 小时未处理。\n"
                f"请到「活动审批」通过或驳回，如不该由您审批可以转交。")
        targets = [who] + [p for p in db.query(User)
                           .filter(User.qingxie_post == "council").all()
                           if p is not None and p.id != (who.id if who else "")]
        for t in targets:
            if t is None:
                continue
            _msgcenter.send_user(db, t, "custom",
                             data={"activity_id": act.id, "route": "approvals"},
                             title=f"审批已等待 {waited} 小时｜{act.title}",
                             content=text)
        act.approval_reminded_at = now()
        logger.info("活动「%s」审批超时未处理，已提醒", act.title)
    if rows:
        db.commit()
    return len(rows)


def remind_stale_hours_requests(db: OrmSession) -> int:
    """时长锁定/解锁审批放太久没人处理：催归口部门的对接人，抄送会长团。
    每条只催一次，**永不自动通过**。

    时长审批平时只通知对接人（见 staff_ops._hours_notify_targets），会长团
    不逐条收；催办这一下才把会长团带上——没人处理的那几条才值得他们看。
    先用条件更新把 reminded_at 占下来再发：清扫每分钟一轮，换了主或多进程
    也只发一遍。
    """
    from sqlalchemy import update
    from . import liaison
    from .routers.staff_ops import council_members

    t = now()
    deadline = t - timedelta(hours=config.APPROVAL_REMIND_HOURS)
    rows = (db.query(HoursRequest)
            .filter(HoursRequest.status == "pending",
                    HoursRequest.created_at < deadline,
                    HoursRequest.reminded_at.is_(None)).all())
    sent = 0
    for req in rows:
        claimed = db.execute(
            update(HoursRequest)
            .where(HoursRequest.id == req.id,
                   HoursRequest.status == "pending",
                   HoursRequest.reminded_at.is_(None))
            .values(reminded_at=t)).rowcount
        db.commit()
        if not claimed:
            continue
        act = db.get(Activity, req.activity_id)
        if act is None:
            continue
        contact = liaison.approval_contact(db, act.approval_dept_id)
        targets = ([contact] if contact else []) + [
            p for p in council_members(db) if contact is None or p.id != contact.id]
        waited = int((t - req.created_at).total_seconds() // 3600)
        text = (f"「{act.title}」的时长{req.kind_label()}申请已提交 {waited} 小时未处理。\n"
                f"说明：{req.reason}\n请到「活动审批」通过或驳回。")
        for u in targets:
            msgcenter.send_user(db, u, "custom",
                                data={"activity_id": act.id, "route": "approvals"},
                                title=f"时长{req.kind_label()}审批已等待 {waited} 小时"
                                      f"｜{act.title}",
                                content=text)
        db.commit()
        sent += 1
        logger.info("活动「%s」时长%s审批超时未处理，已提醒",
                    act.title, req.kind_label())
    return sent


# ======================= 志愿北京外联确认 =======================

def remind_stale_vb_confirms(db: OrmSession) -> int:
    """志愿北京名称推给外联超过 APPROVAL_REMIND_HOURS 小时还没确认：催一次
    对外联络部的对接人（值班人，没人值班是部长），每条只催这一次，同时长审批。

    只催一次而不是每天催：外联的待确认列表就挂在「活动审批」页上，第一条
    通知要了回执，催一次是兜「通知被淹了」的底；每天催会让人把通知关掉。
    先用条件更新把 reminded_at 占下来再发，多进程也只发一遍。
    """
    from sqlalchemy import update
    from . import liaison

    t = now()
    deadline = t - timedelta(hours=config.APPROVAL_REMIND_HOURS)
    rows = (db.query(VbConfirm)
            .filter(VbConfirm.status == "pending",
                    VbConfirm.submitted_at < deadline,
                    VbConfirm.reminded_at.is_(None)).all())
    sent = 0
    for r in rows:
        claimed = db.execute(
            update(VbConfirm)
            .where(VbConfirm.id == r.id, VbConfirm.status == "pending",
                   VbConfirm.reminded_at.is_(None))
            .values(reminded_at=t)).rowcount
        db.commit()
        if not claimed:
            continue
        act = db.get(Activity, r.activity_id)
        if act is None:
            continue
        waited = int((t - r.submitted_at).total_seconds() // 3600)
        for u in liaison.vb_contacts(db):
            msgcenter.send_user(
                db, u, "custom", data={"activity_id": act.id, "route": "approvals"},
                title=f"志愿北京待确认已等待 {waited} 小时｜{act.title}",
                content=(f"「{act.title}」的志愿北京活动名称{liaison.names_text(r.names)}"
                         f"已推送 {waited} 小时未确认。\n"
                         f"请到志愿北京核对后，在「活动审批」确认或退回。"))
        db.commit()
        sent += 1
        logger.info("活动「%s」志愿北京外联确认超时，已提醒", act.title)
    return sent


def remind_vb_resubmit(db: OrmSession) -> int:
    """志愿北京名称被外联退回满 VB_REMIND_DAYS 天，管理者还没重提：每天
    VB_REMIND_HOUR 点起提醒一次本活动的管理者。

    按天去重：先用条件更新把「今天提醒过了」（vb_reminded_on）占下来，
    占到了才发——清扫每分钟一轮，进程重启或多进程也不会一天发两遍。
    重提（push_vb_confirm）会把 vb_reminded_on 清掉，下次退回从头算。
    """
    from sqlalchemy import or_, update
    from . import liaison

    t = now()
    if t.hour < config.VB_REMIND_HOUR:
        return 0
    today = t.date()
    cutoff = t - timedelta(days=config.VB_REMIND_DAYS)
    rows = (db.query(Activity)
            .filter(Activity.status == "vb_pending",
                    or_(Activity.vb_reminded_on.is_(None),
                        Activity.vb_reminded_on < today)).all())
    sent = 0
    for act in rows:
        last = liaison.latest_vb_confirm(db, act.id)
        if last is None or last.status != "returned" \
                or last.handled_at is None or last.handled_at > cutoff:
            continue
        claimed = db.execute(
            update(Activity)
            .where(Activity.id == act.id,
                   Activity.status == "vb_pending",
                   or_(Activity.vb_reminded_on.is_(None),
                       Activity.vb_reminded_on < today))
            .values(vb_reminded_on=today)).rowcount
        db.commit()
        if not claimed:
            continue
        days = (t - last.handled_at).days
        notify_managers(
            db, act.id, f"请重新提交志愿北京活动名称｜{act.title}",
            f"「{act.title}」的志愿北京活动名称已被对外联络部退回 {days} 天，还没有重新提交。\n"
            f"退回理由：{last.reply}\n"
            f"请到活动管理的「审核」页签修改名称后重新提交，活动才会标记为已完成。",
            duty="hours", tab="review")
        db.commit()
        sent += 1
        logger.info("活动「%s」志愿北京名称退回 %d 天未重提，已提醒管理者", act.title, days)
    return sent


# ======================= 值班日报 =======================

def send_duty_digests(db: OrmSession) -> int:
    """每晚 DUTY_DIGEST_HOUR 点给启用值班的部门的部长发一份当天的审批日报。

    值班之后部长不再收逐条的审批通知，但部门的事还是他的事：一天一条，
    今天进来了什么、谁处理了什么、还压着什么。三样都是空的就不发。
    一天只发一次：先用一条条件更新把「今天发过了」占下来，占到了才发——
    清扫每分钟一轮，进程重启或换了主也不会发第二遍。
    """
    from sqlalchemy import or_, update
    from . import liaison

    t = now()
    if t.hour < config.DUTY_DIGEST_HOUR:
        return 0
    today = t.date()
    day_start = t.replace(hour=0, minute=0, second=0, microsecond=0)
    sent = 0
    for dept in (db.query(Department)
                 .filter(Department.name.in_(liaison.DUTY_DEPTS)).all()):
        head = liaison.dept_head(db, dept.id)
        if head is None:
            continue
        claimed = db.execute(
            update(Department)
            .where(Department.id == dept.id,
                   or_(Department.duty_digest_on.is_(None),
                       Department.duty_digest_on < today))
            .values(duty_digest_on=today)).rowcount
        db.commit()
        if not claimed:
            continue
        new, done, waiting = _duty_digest_lines(db, dept, day_start)
        if not (new or done or waiting):
            continue          # 今天什么都没发生，不打扰
        duty = liaison.on_duty(db, dept)

        def block(title: str, lines: list[str]) -> str:
            return f"【{title}】{len(lines)} 条\n" + (
                "\n".join(f"· {x}" for x in lines) if lines else "· 无")

        msgcenter.send_user(
            db, head, "custom", data={"route": "approvals"},
            title=f"值班日报｜{dept.name} {t:%m-%d}",
            content=(f"今日值班：{duty.name if duty else '无人值班（通知发给了您）'}\n"
                     + "\n".join((block("今日新增", new), block("今日处理", done),
                                  block("尚未处理", waiting)))))
        db.commit()
        sent += 1
        logger.info("值班日报已发给 %s（%s）", head.name, dept.name)
    return sent


def _duty_digest_lines(db: OrmSession, dept: Department, since
                       ) -> tuple[list[str], list[str], list[str]]:
    """日报的三栏：今日新增 / 今日处理 / 尚未处理，各是一串人话。

    这个部门的审批 = 归口它的活动审批与时长锁定/解锁；对外联络部另加志愿北京
    外联确认（不分活动归口哪个部门，都由对外联络部确认）。
    """
    from . import liaison

    def name(uid_: str | None) -> str:
        u = db.get(User, uid_) if uid_ else None
        return u.name if u else "系统"

    new: list[str] = []
    done: list[str] = []
    waiting: list[str] = []
    acts = {a.id: a for a in db.query(Activity)
            .filter(Activity.approval_dept_id == dept.id).all()}

    # 活动审批：提交与转入算新增，通过/驳回算处理
    for e in (db.query(ApprovalEvent)
              .filter(ApprovalEvent.activity_id.in_(list(acts)),
                      ApprovalEvent.created_at >= since)
              .order_by(ApprovalEvent.created_at).all()):
        title = acts[e.activity_id].title
        if e.kind in ("submit", "transfer") and e.dept_id == dept.id:
            new.append(f"活动审批「{title}」（{name(e.actor_id)} {e.kind_label()}）")
        elif e.kind in ("approve", "reject"):
            done.append(f"活动审批「{title}」{e.kind_label()}（{name(e.actor_id)}）")
    for a in acts.values():
        if a.status == "pending":
            waiting.append(f"活动审批「{a.title}」（{a.submitted_at:%m-%d %H:%M} 提交）"
                           if a.submitted_at else f"活动审批「{a.title}」")

    # 时长锁定/解锁
    for r in (db.query(HoursRequest)
              .filter(HoursRequest.activity_id.in_(list(acts)))
              .filter((HoursRequest.created_at >= since)
                      | (HoursRequest.handled_at >= since)
                      | (HoursRequest.status == "pending"))
              .order_by(HoursRequest.created_at).all()):
        what = f"时长{r.kind_label()}「{acts[r.activity_id].title}」"
        if r.created_at >= since:
            new.append(f"{what}（{name(r.requested_by)} 提交）")
        if r.handled_at and r.handled_at >= since:
            done.append(f"{what}{r.status_label()}（{name(r.handled_by)}）")
        if r.status == "pending":
            waiting.append(f"{what}（{r.created_at:%m-%d %H:%M} 提交）")

    # 志愿北京外联确认：只算在对外联络部头上
    if dept.name != liaison.LIAISON_DEPT:
        return new, done, waiting
    for v in (db.query(VbConfirm)
              .filter((VbConfirm.submitted_at >= since)
                      | (VbConfirm.handled_at >= since)
                      | (VbConfirm.status == "pending"))
              .order_by(VbConfirm.submitted_at).all()):
        act = db.get(Activity, v.activity_id)
        what = f"志愿北京确认「{act.title if act else '?'}」"
        if v.submitted_at >= since:
            new.append(f"{what}（{v.kind_label()}）")
        if v.handled_at and v.handled_at >= since \
                and v.status in ("confirmed", "returned"):
            done.append(f"{what}{v.status_label()}（{name(v.handled_by)}）")
        if v.status == "pending":
            waiting.append(f"{what}（{v.submitted_at:%m-%d %H:%M} 推送）")
    return new, done, waiting


# ======================= 编辑冻结兜底 =======================

def release_stale_edit_locks(db: OrmSession) -> int:
    """超时没点「保存并发布」就自动解冻。

    管理者点了「调整岗位」转头去开会，学生一下午都报不了名，而这件事
    没有任何外部表现——除了学生以为活动黄了。
    """
    deadline = now() - timedelta(hours=config.EDIT_LOCK_HOURS)
    rows = (db.query(Activity)
            .filter(Activity.editing_since.isnot(None),
                    Activity.editing_since < deadline).all())
    for act in rows:
        who = db.get(User, act.editing_by) if act.editing_by else None
        since = act.editing_since
        act.editing_since = None
        act.editing_by = None
        # 改到一半的东西也已经生效了：和「保存并发布」一样按人告诉志愿者
        if act.status != "draft":
            notify_edit_changes(db, act, since)
        audit_log(db, actor=None, action="update", target_type="activity",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=f"编辑态超过 {config.EDIT_LOCK_HOURS} 小时未保存，"
                          f"系统自动解冻并恢复报名")
        if who is not None:
            msgcenter.send_user(db, who, "custom",
                            title=f"报名已自动恢复｜{act.title}",
                            content=f"您对「{act.title}」的编辑态超过 "
                                    f"{config.EDIT_LOCK_HOURS} 小时未保存，"
                                    f"系统已自动解冻并恢复报名。\n"
                                    f"如仍需调整，请重新点击「调整岗位」。")
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
    """活动前几天，每天给管理者每个场次一条人员变动摘要。

    逐条推变动会被无视（报名期一天几十条），完全不推又会让老师在现场
    才发现少人。折中：临近时每天一条「昨日 +2 −1，当前 18/20」，
    真正紧急的（名单锁定后的服务场次减员）另有即时提醒。
    培训/彩排的请假也只进这里：逐条即时推，彩排前一天几个人请假就是几条通知。
    """
    from .enroll_logic import shift_enrolled_count

    t = now()
    # 每天固定时间推一条：扫描是每分钟一跑的，不定点的话零点整就发出去了
    cutoff = t.replace(hour=config.ROSTER_DIGEST_HOUR, minute=0,
                       second=0, microsecond=0)
    if t < cutoff:
        return 0
    lead = timedelta(days=config.ROSTER_DIGEST_DAYS)
    shifts = (db.query(Shift)
              .join(Activity, Shift.activity_id == Activity.id)
              .filter(Shift.start_time > t,
                      Shift.start_time <= t + lead,
                      Activity.status.in_(("published", "closed")))
              .order_by(Shift.start_time)
              .all())
    since = t - timedelta(days=1)
    sent = 0
    for g in shifts:
        if g.digest_sent_on and g.digest_sent_on >= cutoff:
            continue
        g.digest_sent_on = t
        # 变动按这一场算：流水挂在报名记录上，报名记录不会换场次。
        # 按活动算的话，同一活动的几场会收到同一组数字，看不出是哪一场少了人
        rows = (db.query(AuditLog)
                .join(Enrollment, AuditLog.target_id == Enrollment.id)
                .filter(AuditLog.activity_id == g.activity_id,
                        AuditLog.target_type == "enrollment",
                        Enrollment.shift_id == g.id,
                        AuditLog.action.in_(_DIGEST_JOIN + _DIGEST_LEAVE
                                            + _DIGEST_EXCUSE),
                        AuditLog.created_at >= since)
                .order_by(AuditLog.created_at).all())
        if not rows:
            continue          # 没有变动就不打扰
        joined = sum(1 for r in rows if r.action in _DIGEST_JOIN)
        left = sum(1 for r in rows if r.action in _DIGEST_LEAVE)
        lines = []
        if joined or left:
            lines.append(f"过去 24 小时：新增 {joined} 人，退出 {left} 人。")
        excuses = [r.summary for r in rows if r.action in _DIGEST_EXCUSE]
        if excuses:
            lines.append("请假/销假：")
            lines += [f"· {x}" for x in excuses]
        enrolled = shift_enrolled_count(db, g.id)
        if g.is_attached():
            # 培训/彩排不设名额，报上服务场次就连带在册；要看的是会少几个人
            excused = (db.query(Enrollment)
                       .filter(Enrollment.shift_id == g.id,
                               Enrollment.status == "enrolled",
                               Enrollment.excused.is_(True)).count())
            lines.append(f"当前在册 {enrolled} 人"
                         + (f"，其中请假 {excused} 人。" if excused else "。"))
        else:
            capacity = g.total_capacity()
            gap = max(0, capacity - enrolled)
            lines.append(f"当前 {enrolled}/{capacity} 人"
                         + (f"，缺 {gap} 人。" if gap else "，人数已满。"))
        lines.append(f"场次开始：{g.start_time:%m-%d %H:%M}")
        act = db.get(Activity, g.activity_id)
        notify_managers(db, act.id, f"[人员变动] {act.title} · {g.name}",
                        "\n".join(lines), duty="roster", tab="roster")
        sent += 1
    if shifts:
        db.commit()
    return sent


# 进日报的动作。候补的进出不改变到场人数，只进管理页的人员变动流水
_DIGEST_JOIN = ("enroll", "promote")
_DIGEST_LEAVE = ("cancel", "abandon")
_DIGEST_EXCUSE = ("excuse", "unexcuse")


# ======================= 清理 =======================

def purge_expired_sessions(db: OrmSession) -> int:
    """删掉过期会话。

    到期的令牌本来就用不了（deps._authorize 会拒），但记录留在库里只增不减，
    一年下来就是一张没人看、越查越慢的表。
    """
    from .models import Session

    n = (db.query(Session).filter(Session.expires_at <= now())
         .delete(synchronize_session=False))
    if n:
        db.commit()
    return n


def purge_orphan_attachments(db: OrmSession) -> int:
    """清掉没人引用的问卷附件：文件与记录一起删。

    报名问卷的文件题是先传文件、再提交答案。填到一半关掉页面，文件就留在
    磁盘上，没有任何申请指向它。提交与撤回时会当场清一遍，这里管的是
    「传了就再也没回来」的那些，留够重来的时间再删。
    """
    from .models import Application, Attachment
    from .routers.materials import MATERIAL_DIR

    cutoff = now() - timedelta(days=config.ORPHAN_ATTACHMENT_DAYS)
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == "application",
                    Attachment.created_at <= cutoff).all())
    if not rows:
        return 0
    # 还被某份申请的答案引用着的，一律不动
    used: set[str] = set()
    for app in db.query(Application).filter(
            Application.activity_id.in_({r.activity_id for r in rows})).all():
        used |= {str(v) for v in (app.answers or {}).values()
                 if isinstance(v, str)}
    n = 0
    for att in rows:
        if att.id in used:
            continue
        (MATERIAL_DIR / att.stored_name).unlink(missing_ok=True)
        db.delete(att)
        n += 1
    if n:
        db.commit()
        logger.info("清理无人引用的问卷附件 %d 个", n)
    return n


def purge_oplogs(db: OrmSession) -> int:
    """删掉超过保留期（config.OPLOG_RETAIN_DAYS）的操作日志文件。

    按文件名里的日期判断，一个文件就是一个进程一天的日志。每轮都查一遍，
    只是列一下目录；到了日子当天就删，不必等到某个钟点。
    """
    from . import oplog

    n = oplog.purge(now().date())
    if n:
        logger.info("清理过期操作日志 %d 个文件", n)
    return n
