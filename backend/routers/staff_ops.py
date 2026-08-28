"""教师/青协端：签到任务、导出中心、时长录入与回传。"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from .. import config
from ..database import db_session
from ..deps import require_manager, require_manager_download
from ..enroll_logic import position_enrolled_count
from ..exporter import build_activity_workbook
from ..perm import ensure_activity_manager
from ..integrations import checkin_gateway, shuzhi, wecom
from ..notify import notify_checkin
from ..models import (Activity, CheckinRecord, CheckinTask, Enrollment,
                      HoursAppeal, HoursRecord, Shift, Position, User,
                      iso, now, uid)

router = APIRouter(prefix="/api/staff", tags=["staff-ops"])


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


# ======================= 签到任务 =======================

class TaskBody(BaseModel):
    mode: str = "shift"                 # group 统一签到 / position 分岗位
    position_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@router.post("/shifts/{shift_id}/checkin-tasks")
def create_task(shift_id: str, body: TaskBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    position = None
    if body.mode == "position":
        position = db.get(Position, body.position_id or "")
        if position is None or position.shift_id != g.id:
            raise HTTPException(400, "分岗位签到必须指定本场次下的岗位")
    token = checkin_gateway.create_task(body.mode, g.id,
                                        position.id if position else "")
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                       position_id=position.id if position else None,
                       mode=body.mode, token=token,
                       valid_from=(datetime.fromisoformat(body.valid_from)
                                   if body.valid_from else g.start_time),
                       valid_to=(datetime.fromisoformat(body.valid_to)
                                 if body.valid_to else g.end_time),
                       created_by=user.id)
    db.add(task)
    label = f"{position.name}" if position else f"{g.name}·统一签到"
    audit_log(db, actor=user, action="checkin_task", target_type="checkin",
              target_id=task.id, target_label=f"{act.title}·{label}",
              activity_id=act.id,
              summary=f"生成签到码 {token}（{'岗位' if position else '统一'}模式，"
                      f"有效 {task.valid_from:%H:%M}~{task.valid_to:%H:%M}）")
    db.commit()
    return {"id": task.id, "token": token}


@router.get("/activities/{activity_id}/checkin-tasks")
def list_tasks(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    _act(db, activity_id)
    tasks = (db.query(CheckinTask)
             .filter(CheckinTask.activity_id == activity_id)
             .order_by(CheckinTask.created_at.desc()).all())
    out = []
    for t in tasks:
        g = db.get(Shift, t.shift_id)
        st = db.get(Position, t.position_id) if t.position_id else None
        n = (db.query(func.count(CheckinRecord.id))
             .filter(CheckinRecord.task_id == t.id).scalar() or 0)
        out.append({"id": t.id, "token": t.token, "mode": t.mode,
                    "shift_name": g.name,
                    "position_name": st.name if st else "",
                    "valid_from": iso(t.valid_from), "valid_to": iso(t.valid_to),
                    "active": t.active, "count": n})
    return out


@router.patch("/checkin-tasks/{task_id}")
def toggle_task(task_id: str, body: dict,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    if "active" in body:
        task.active = bool(body["active"])
    db.commit()
    return {"ok": True, "active": task.active}


class ReminderBody(BaseModel):
    """范围三选一：整场次 / 指定岗位组 / 指定岗位。"""
    position_ids: list[str] = []
    group_name: str = ""
    need_ack: bool = False   # 要求志愿者点「我已知悉」，管理端能看到谁没回


@router.post("/shifts/{shift_id}/checkin-reminder")
def send_reminder(shift_id: str, body: ReminderBody | None = None,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """手动补发签到提醒。

    自动提醒在场次开始前 30 分钟发一次；这个按钮用于临时催场——
    只发给还没签到的正式志愿者，已经到岗的不再打扰。
    可只催某几个岗位或某个岗位组：现场往往是「东门那三个人没到」。
    勾选签收后，这一批会出现在活动的「待签收批次」里，能看到谁还没回。
    """
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)

    body = body or ReminderBody()
    ids, scope = None, "全场次"
    if body.group_name.strip():
        ids = [p.id for p in g.positions if p.group_name == body.group_name.strip()]
        if not ids:
            raise HTTPException(400, f"本场次没有岗位组「{body.group_name}」")
        scope = f"岗位组「{body.group_name}」"
    elif body.position_ids:
        known = {p.id: p for p in g.positions}
        bad = [i for i in body.position_ids if i not in known]
        if bad:
            raise HTTPException(400, "所选岗位不属于该场次")
        ids = list(body.position_ids)
        scope = "岗位" + "、".join(f"「{known[i].name}」" for i in ids)

    batch_id = uid("bat") if body.need_ack else None
    n = notify_checkin(db, g, position_ids=ids, need_ack=body.need_ack,
                       batch_id=batch_id)
    if not n:
        raise HTTPException(400, f"{scope}没有需要提醒的人（未签到的正式志愿者为 0）")
    g.reminder_sent_at = now()
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=g.id, target_label=g.name, activity_id=g.activity_id,
              summary=f"手动补发签到提醒：{scope} {n} 人（未签到）"
                      + ("，要求签收" if body.need_ack else ""))
    db.commit()
    return {"ok": True, "count": n, "scope": scope, "batch_id": batch_id}


@router.get("/checkin-tasks/{task_id}/board")
def board(task_id: str, db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    """签到实时大屏。以已报名报名名单为主表（含未签到者），前端 5s 轮询。"""
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    g = db.get(Shift, task.shift_id)
    act = db.get(Activity, task.activity_id)

    roster = (db.query(Enrollment, User)
              .join(User, Enrollment.user_id == User.id)
              .filter(Enrollment.shift_id == g.id,
                      Enrollment.status == "enrolled")
              .order_by(User.college, User.name).all())
    recs = (db.query(CheckinRecord)
            .filter(CheckinRecord.shift_id == g.id).all())
    rec_by_user: dict[str, CheckinRecord] = {r.user_id: r for r in recs}

    items = []
    checked = checked_out = 0
    by_position: dict[str, int] = {}   # 到岗统计只基于有签到记录的
    for enr, u in roster:
        st = db.get(Position, enr.position_id) if enr.position_id else None
        rec = rec_by_user.get(u.id)
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            key = rec.position_id or "_shift"
            by_position[key] = by_position.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "position": st.name if st else "待分配",
            "is_leader": enr.is_leader,
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                 "checked": by_position.get(s.id, 0)}
                for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "task": {"id": task.id, "token": task.token, "mode": task.mode,
                 "active": task.active},
        "activity_title": act.title, "shift_name": g.name,
        "expected": len(roster), "checked": checked,
        "checked_out": checked_out,
        "positions": positions, "records": items[:500],
    }


@router.get("/shifts/{shift_id}/board")
def shift_board(shift_id: str, db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """按场次聚合的签到实时大屏（不依赖激活的签到任务，
    任务关闭/不存在时仍可查看到场名单）。前端 5s 轮询。

    以已报名报名（Enrollment.status=enrolled）join User 为主表，
    左连该场次的 CheckinRecord（user_id+shift_id 匹配，一人至多一条）。
    """
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)

    rows = (db.query(Enrollment, User, CheckinRecord)
            .join(User, Enrollment.user_id == User.id)
            .outerjoin(CheckinRecord,
                       (CheckinRecord.user_id == Enrollment.user_id)
                       & (CheckinRecord.shift_id == Enrollment.shift_id))
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.status == "enrolled")
            .order_by(User.college, User.name).all())

    items = []
    checked = checked_out = 0
    by_position: dict[str, int] = {}   # 到岗统计只基于有签到记录者
    for enr, u, rec in rows:
        st = db.get(Position, enr.position_id) if enr.position_id else None
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            key = rec.position_id or "_shift"
            by_position[key] = by_position.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "position": st.name if st else "待分配",
            "is_leader": enr.is_leader,
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                 "checked": by_position.get(s.id, 0)}
                for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "activity_id": act.id, "activity_title": act.title,
        "shift_id": g.id, "shift_name": g.name,
        "expected": len(rows), "checked": checked,
        "checked_out": checked_out,
        "positions": positions, "records": items[:500],
    }


@router.post("/checkin-tasks/{task_id}/batch")
def batch_op(task_id: str, body: dict,
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """批量签到/签退/取消签到/取消签退（多选志愿者）。

    body: {"action": "checkin|checkout|cancel_checkin|cancel_checkout",
           "student_ids": ["2023…", …]}
    全部计入留痕。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    g = db.get(Shift, task.shift_id)
    action = body.get("action")
    sids = [s.strip() for s in (body.get("student_ids") or []) if s.strip()]
    if action not in ("checkin", "checkout", "cancel_checkin", "cancel_checkout"):
        raise HTTPException(400, "未知操作")
    if not sids:
        raise HTTPException(400, "未选择志愿者")

    done, skipped = [], []
    for sid in sids:
        u = db.query(User).filter(User.student_id == sid).first()
        if u is None:
            skipped.append(f"{sid}（学号不存在）")
            continue
        enr = (db.query(Enrollment)
               .filter(Enrollment.shift_id == task.shift_id,
                       Enrollment.user_id == u.id,
                       Enrollment.status == "enrolled").first())
        if enr is None:
            skipped.append(f"{u.name}（非本场次确认志愿者）")
            continue
        rec = (db.query(CheckinRecord)
               .filter(CheckinRecord.user_id == u.id,
                       CheckinRecord.shift_id == task.shift_id).first())

        if action == "checkin":
            if rec:
                skipped.append(f"{u.name}（已签到）")
                continue
            db.add(CheckinRecord(id=uid("ckr"), task_id=task.id,
                                 activity_id=task.activity_id,
                                 shift_id=task.shift_id,
                                 position_id=enr.position_id, user_id=u.id,
                                 method="manual", by_user=user.id))
            done.append(u.name)
        elif action == "checkout":
            if not rec:
                skipped.append(f"{u.name}（尚未签到）")
                continue
            if rec.checkout_at:
                skipped.append(f"{u.name}（已签退）")
                continue
            rec.checkout_at = now()
            rec.checkout_method = "manual"
            rec.checkout_by = user.id
            done.append(u.name)
        elif action == "cancel_checkin":
            if not rec:
                skipped.append(f"{u.name}（无签到记录）")
                continue
            db.delete(rec)
            done.append(u.name)
        else:  # cancel_checkout
            if not rec or not rec.checkout_at:
                skipped.append(f"{u.name}（无签退记录）")
                continue
            rec.checkout_at = None
            rec.checkout_method = ""
            rec.checkout_by = None
            done.append(u.name)

    ACTION_SUMMARY = {
        "checkin": "手动签到", "checkout": "手动签退",
        "cancel_checkin": "取消签到", "cancel_checkout": "取消签退",
    }
    audit_log(db, actor=user, action="manual_checkin", target_type="checkin",
              target_id=task.id, target_label=g.name,
              activity_id=task.activity_id,
              summary=f"{ACTION_SUMMARY[action]} {len(done)} 人"
                      + (f"；跳过：{'、'.join(skipped[:5])}"
                         + ("…" if len(skipped) > 5 else "") if skipped else ""))
    db.commit()
    return {"ok": True, "done": len(done), "names": done, "skipped": skipped}


# ======================= 导出 =======================

@router.get("/activities/{activity_id}/export.xlsx")
def export(activity_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager_download)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    buf = build_activity_workbook(db, act)
    stamp = now().strftime("%Y%m%d_%H%M")
    filename = f"{act.title}_排班表_{stamp}.xlsx"
    # 下载留痕（《设计方案》：后台记录每一次下载操作）
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出排班表《{filename}》"
                      "（生产版将强制加密，密码经企业微信单独发送）")
    db.commit()
    from urllib.parse import quote
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("/activities/{activity_id}/poster")
def poster(activity_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """招募版活动信息（纯文本，供复制到群里/推送）。

    只写对外能公开的：时间地点、岗位与名额、要求与保障、联系人。
    不含任何志愿者名单——名单要用「导出排班表」，那条路有下载留痕。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    L: list[str] = [f"【{act.title}】志愿者招募",
                    f"主办：{act.organizer or '—'}",
                    f"类型：{act.type}类·{config.type_name(act.type)}"]
    span = act.time_span()
    if span:
        L.append(f"时间：{span[0]:%Y-%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}")
    L.append(f"地点：{act.location or '待定'}")
    if act.reg_end:
        L.append(f"报名截止：{act.reg_end:%Y-%m-%d %H:%M}")
    if act.intro:
        L += ["", "【活动简介】", act.intro.strip()]
    if act.volunteer_requirement:
        L += ["", "【志愿者要求】", act.volunteer_requirement.strip()]
    if act.volunteer_benefit:
        L += ["", "【志愿者保障】", act.volunteer_benefit.strip()]

    L += ["", "【岗位安排】"]
    for g in sorted(act.main_shifts(), key=lambda x: x.start_time):
        head = f"◆ {g.name}（{g.span_text()}）"
        if g.needs_assign():
            head += f"　名额 {g.capacity} 人，岗位于报名截止后统一分配"
        L.append(head)
        for p in sorted(g.positions, key=lambda x: x.sort):
            left = max(0, p.quota - position_enrolled_count(db, p.id))
            line = (f"  · {p.name}　{p.quota} 人"
                    + (f"（剩 {left}）" if left else "（已满）")
                    + (f"　{p.location}" if p.location else ""))
            if p.requirement_note:
                line += f"\n    要求：{p.requirement_note}"
            L.append(line)
        for s in sorted((x for x in act.shifts if x.parent_id == g.id),
                        key=lambda x: x.start_time):
            who = "全体" if not s.applies_to_positions else "、".join(
                p.name for p in g.positions if p.id in s.applies_to_positions)
            L.append(f"  ▷ {s.kind_label()}：{s.span_text()}"
                     + (f"　{s.location}" if s.location else "")
                     + f"　（{who}需参加"
                     + ("，不计时长）" if s.hours_mode == "none" else "，计入时长）"))

    contacts = "；".join(f"{c.get('name', '')} {c.get('phone', '')}".strip()
                        for c in (act.contacts or []) if isinstance(c, dict))
    if contacts:
        L += ["", f"【联系方式】{contacts}"]
    L += ["", "报名方式：登录人大校青协志愿服务管理平台，在「活动广场」找到本活动报名。"]
    return {"text": "\n".join(L)}


# ======================= 时长录入与回传 =======================

CHECKOUT_GRACE = timedelta(hours=config.CHECKOUT_GRACE_HOURS)


def _round_half_up(hours: float) -> float:
    """向上取整至 0.5 的倍数：2h13m → 2.5h。"""
    return math.ceil(max(0.0, hours) * 2) / 2


def effective_checkout(rec: CheckinRecord, g: Shift):
    """签退时间判定：
       · 已签退 → 实际签退时间；
       · 未签退但已超过「场次结束+2小时」→ 按场次结束时间兜底；
       · 宽限期内未签退 → None（时长待定）。"""
    if rec.checkout_at:
        return rec.checkout_at, "actual"
    deadline = g.end_time + CHECKOUT_GRACE
    if now() > deadline:
        return g.end_time, "auto"
    return None, "pending"


def shift_hours(rec: CheckinRecord, g: Shift, t: datetime):
    """一条签到记录在该场次记多少小时，以及要不要人工过目。

    时长认定方式是建场次时定下的一次决定，不是事后逐条判断：
      fixed  到场即按场次时长计——两小时的彩排人就在那儿，不值得卡分钟，
             顺带消灭了「忘记签退」这类麻烦的一大半；
      none   只记出勤，不产生时长（线上培训）；
      checkin 签退 − 签到，未签退按宽限期规则兜底并标记待核。
    """
    if g.hours_mode == "none":
        return 0.0, ""
    if g.hours_mode == "fixed":
        return (g.end_time - g.start_time).total_seconds() / 3600, ""
    checkout, kind = effective_checkout(rec, g)
    if checkout is None:
        return (min(t, g.end_time) - rec.checkin_at).total_seconds() / 3600, "pending"
    return ((checkout - rec.checkin_at).total_seconds() / 3600,
            "auto" if kind == "auto" else "")


def compute_hours(db: OrmSession, act: Activity) -> list[dict]:
    """基于签到/签退记录计算时长：签退 - 签到，向上取整至 0.5 小时。

    未签退且在宽限期内 → 标记 pending（暂按当前时间估算展示）；
    超过宽限未签退 → 按场次结束时间兜底，并标记 auto_checkout **待核**：
    到场扫个码就走的人和全程服务的人算出来一样多，这类记录必须由管理者
    过目——「确认全部」会跳过它们，见 hours_confirm。
    """
    t = now()
    records = (db.query(CheckinRecord)
               .filter(CheckinRecord.activity_id == act.id).all())
    per_user: dict[str, float] = {}
    flags: dict[str, set] = {}
    for rec in records:
        g = db.get(Shift, rec.shift_id)
        if g is None:
            continue
        hours, flag = shift_hours(rec, g, t)
        if flag:
            flags.setdefault(rec.user_id, set()).add(flag)
        per_user[rec.user_id] = per_user.get(rec.user_id, 0.0) + max(0.0, hours)

    out = []
    for uid_, raw in sorted(per_user.items(), key=lambda kv: -kv[1]):
        if raw <= 0:
            continue      # 只去了「不计时长」的场合（如线上培训）：出勤在签到那边看
        u = db.get(User, uid_)
        existing = db.query(HoursRecord).filter(
            HoursRecord.activity_id == act.id, HoursRecord.user_id == uid_,
            HoursRecord.source == "checkin").first()
        fl = flags.get(uid_, set())
        out.append({
            "user": {"id": u.id, "name": u.name,
                     "student_id": u.student_id, "college": u.college},
            "hours": _round_half_up(raw),
            "pending": "pending" in fl,      # 未签退（宽限期内）
            "auto_checkout": "auto" in fl,   # 超时自动按场次结束时间
            "status": existing.status if existing else None,
            "upload_ref": existing.upload_ref if existing else "",
        })
    return out


@router.get("/activities/{activity_id}/hours")
def hours_preview(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    manual = []
    for r in (db.query(HoursRecord)
              .filter(HoursRecord.activity_id == act.id,
                      HoursRecord.source == "manual")
              .order_by(HoursRecord.created_at.desc()).all()):
        u = db.get(User, r.user_id)
        manual.append({"id": r.id, "name": u.name if u else "?",
                       "student_id": u.student_id if u else "",
                       "hours": r.hours, "note": r.note,
                       "status": r.status, "upload_ref": r.upload_ref,
                       "created_at": iso(r.created_at)})
    return {"items": compute_hours(db, act), "manual": manual,
            "status_label": act.label_status()}


class ManualHoursBody(BaseModel):
    items: list[dict]   # [{student_id, hours, note}]


@router.post("/activities/{activity_id}/hours/manual")
def hours_manual(activity_id: str, body: ManualHoursBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """管理者为志愿者手动添加时长（不依赖签到），可批量。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not body.items:
        raise HTTPException(400, "空列表")
    added = []
    for it in body.items:
        u = db.query(User).filter(
            User.student_id == (it.get("student_id") or "").strip()).first()
        if u is None:
            raise HTTPException(404, f"学号 {it.get('student_id')} 不存在")
        try:
            hours = float(it.get("hours"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{u.name} 的时长不是数字")
        if hours <= 0:
            raise HTTPException(400, f"{u.name} 的时长必须大于 0")
        db.add(HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id,
                           hours=round(hours, 2), status="enrolled",
                           source="manual", note=(it.get("note") or "").strip(),
                           confirmed_by=user.id))
        added.append(f"{u.name} {hours}h")
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"手动添加时长：{'、'.join(added)}")
    db.commit()
    return {"ok": True, "count": len(added)}


@router.delete("/activities/{activity_id}/hours/manual/{record_id}")
def hours_manual_delete(activity_id: str, record_id: str,
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    r = db.get(HoursRecord, record_id)
    if r is None or r.activity_id != act.id or r.source != "manual":
        raise HTTPException(404, "手动时长记录不存在")
    if r.status == "uploaded":
        raise HTTPException(409, "该记录已回传数智人大，不可删除")
    u = db.get(User, r.user_id)
    db.delete(r)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"删除手动时长：{u.name if u else '?'} {r.hours}h（{r.note}）")
    db.commit()
    return {"ok": True}


class HoursConfirmBody(BaseModel):
    items: list[dict] | None = None   # [{student_id, hours}] 指定部分志愿者并覆盖时长


@router.post("/activities/{activity_id}/hours/confirm")
def hours_confirm(activity_id: str, body: HoursConfirmBody | None = None,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """确认时长（依据签到记录），幂等可重复执行。

    · 不带 items → 全部志愿者按计算值确认（原逻辑）；
    · 带 items   → 只确认所选志愿者，时长以请求值为准（覆盖计算值）。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)

    kept: list[str] = []

    def upsert(user_id: str, hours: float, *, force: bool = False) -> None:
        existing = db.query(HoursRecord).filter(
            HoursRecord.activity_id == act.id,
            HoursRecord.user_id == user_id,
            HoursRecord.source == "checkin").first()
        if existing:
            # 采纳过申诉的记录不被「确认全部」推回计算值：那次改判是人判的
            settled = db.query(HoursAppeal).filter(
                HoursAppeal.record_id == existing.id,
                HoursAppeal.status == "accepted").first()
            if settled is not None and not force:
                u = db.get(User, user_id)
                kept.append(u.name if u else user_id)
                return
            existing.hours = hours
            return
        db.add(HoursRecord(id=uid("hrs"), activity_id=act.id,
                           user_id=user_id, hours=hours,
                           status="enrolled", source="checkin",
                           confirmed_by=user.id))

    chosen = (body.items if body else None) or []
    if chosen:
        targets: list[tuple[str, float]] = []
        for it in chosen:
            sid = str(it.get("student_id") or "").strip()
            if not sid:
                raise HTTPException(400, "缺少学号")
            try:
                hours = float(it.get("hours"))
            except (TypeError, ValueError):
                raise HTTPException(400, f"学号 {sid} 的时长不是数字")
            if hours < 0:
                raise HTTPException(400, f"学号 {sid} 的时长不能为负数")
            u = db.query(User).filter(User.student_id == sid).first()
            if u is None:
                raise HTTPException(404, f"学号 {sid} 不存在")
            targets.append((u.id, round(hours, 2)))
        for uid_, h in targets:
            upsert(uid_, h, force=True)   # 逐个点名改，就是人判的，照改
        count = len(targets)
        detail = "、".join(f"{db.get(User, u_).name}{h:g}h"
                           for u_, h in targets[:8])
        summary = (f"部分确认 {count} 人志愿时长（以管理端填写为准）：{detail}"
                   + ("…" if count > 8 else ""))
    else:
        items = compute_hours(db, act)
        if not items:
            raise HTTPException(400, "暂无签到数据，无法确认时长")
        # 未签退（宽限期内）与超时自动兜底的记录不在此列：时长存疑，
        # 需要管理者在表格里逐条核对数值后单独确认
        clear = [it for it in items
                 if not it["pending"] and not it["auto_checkout"]]
        review = [it for it in items if it not in clear]
        if not clear:
            raise HTTPException(
                400, f"{len(review)} 条记录未正常签退，时长待核，"
                     f"请在表格中逐条核对后确认")
        for it in clear:
            upsert(it["user"]["id"], it["hours"])
        count = len(clear) - len(kept)
        summary = (f"确认 {count} 人志愿时长（依据签到签退记录）"
                   + (f"；{len(review)} 人未正常签退、时长待核未确认"
                      if review else "")
                   + (f"；{'、'.join(kept)} 的时长由申诉改判过，保留不覆盖"
                      if kept else ""))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)
    db.commit()
    return {"ok": True, "count": count, "message": summary}


# ======================= 时长申诉 =======================

@router.get("/activities/{activity_id}/hours-appeals")
def list_appeals(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    rows = (db.query(HoursAppeal)
            .filter(HoursAppeal.activity_id == activity_id)
            .order_by(HoursAppeal.status.desc(),
                      HoursAppeal.created_at.desc()).all())
    out = []
    for a in rows:
        u = db.get(User, a.user_id)
        rec = db.get(HoursRecord, a.record_id)
        out.append({"id": a.id, "status": a.status, "label": a.status_label(),
                    "name": u.name if u else "?",
                    "student_id": u.student_id if u else "",
                    "old_hours": a.old_hours, "claimed_hours": a.claimed_hours,
                    "current_hours": rec.hours if rec else None,
                    "uploaded": bool(rec and rec.status == "uploaded"),
                    "reason": a.reason, "reply": a.reply,
                    "final_hours": a.final_hours,
                    "created_at": iso(a.created_at)})
    return out


class ResolveAppealBody(BaseModel):
    accept: bool
    hours: float | None = None    # 采纳时可改判成第三个数，留空则按申报值
    reply: str = ""


@router.post("/hours-appeals/{appeal_id}/resolve")
def resolve_appeal(appeal_id: str, body: ResolveAppealBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """采纳或驳回一条时长申诉。采纳即改写时长，全程留痕并回告本人。"""
    a = db.get(HoursAppeal, appeal_id)
    if a is None:
        raise HTTPException(404, "申诉不存在")
    ensure_activity_manager(db, user, a.activity_id)
    if a.status != "pending":
        raise HTTPException(409, f"该申诉已{a.status_label()}")
    rec = db.get(HoursRecord, a.record_id)
    if rec is None:
        raise HTTPException(404, "对应的时长记录已不存在")
    if rec.status == "uploaded":
        raise HTTPException(409, "该记录已回传数智人大，无法再改；请走线下更正")
    act = _act(db, a.activity_id)
    who = db.get(User, a.user_id)

    if body.accept:
        hours = a.claimed_hours if body.hours is None else round(body.hours, 2)
        if hours <= 0:
            raise HTTPException(400, "时长必须大于 0")
        before = rec.hours
        rec.hours = hours
        rec.confirmed_by = user.id
        a.final_hours = hours
        a.status = "accepted"
        summary = (f"采纳 {who.name if who else ''} 的时长申诉："
                   f"{before:g}h → {hours:g}h（申报 {a.claimed_hours:g}h）")
    else:
        a.status = "rejected"
        summary = (f"驳回 {who.name if who else ''} 的时长申诉"
                   f"（申报 {a.claimed_hours:g}h，维持 {rec.hours:g}h）")
    a.reply = body.reply.strip()
    a.handled_by = user.id
    a.handled_at = now()

    if who is not None:
        wecom.send_user(
            db, who, "custom", data={"activity_id": act.id},
            title=f"时长申诉{'已采纳' if body.accept else '未采纳'}｜{act.title}",
            content=(f"您对「{act.title}」时长的申诉已处理。\n"
                     f"结果：{'采纳' if body.accept else '未采纳'}，"
                     f"当前时长 {rec.hours:g} 小时。"
                     + (f"\n说明：{a.reply}" if a.reply else "")))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary + (f"；说明：{a.reply}" if a.reply else ""))
    db.commit()
    return {"ok": True, "status": a.status, "hours": rec.hours}


@router.post("/activities/{activity_id}/hours/upload")
def hours_upload(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """回传数智人大（演示模式返回模拟回执）。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id,
                    HoursRecord.status != "uploaded").all())
    # 回传即定稿，之后改不了。有人正在申诉的记录先留下，处理完下次一起传
    held = {a.record_id for a in db.query(HoursAppeal)
            .filter(HoursAppeal.activity_id == act.id,
                    HoursAppeal.status == "pending").all()}
    skipped = [r for r in rows if r.id in held]
    rows = [r for r in rows if r.id not in held]
    if not rows:
        raise HTTPException(
            400, f"{len(skipped)} 条记录正在申诉中，处理完再回传"
            if skipped else "没有待回传的时长记录，请先确认时长")
    receipt = shuzhi.upload_service_hours(act.id, rows)
    if not receipt.ok:
        raise HTTPException(502, f"数智人大接口返回失败：{receipt.message}")
    stamp = now()
    for r in rows:
        r.status = "uploaded"
        r.upload_ref = receipt.receipt_no
        r.uploaded_at = stamp
    tail = f"；{len(skipped)} 条因申诉待处理未回传" if skipped else ""
    audit_log(db, actor=user, action="hours_upload", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"回传 {len(rows)} 人时长至数智人大"
                      f"（回执号 {receipt.receipt_no}，{receipt.message}）{tail}")
    db.commit()
    return {"ok": True, "receipt_no": receipt.receipt_no,
            "count": len(rows), "skipped": len(skipped),
            "message": receipt.message + tail}
