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
from ..exporter import build_activity_workbook
from ..perm import ensure_activity_manager
from ..integrations import checkin_gateway, shuzhi
from ..notify import notify_checkin
from ..models import (Activity, CheckinRecord, CheckinTask, Enrollment,
                      HoursRecord, PositionGroup, Station, User, iso, now, uid)

router = APIRouter(prefix="/api/staff", tags=["staff-ops"])


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


# ======================= 签到任务 =======================

class TaskBody(BaseModel):
    mode: str = "group"                 # group 统一签到 / station 分时分工位
    station_id: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None


@router.post("/groups/{group_id}/checkin-tasks")
def create_task(group_id: str, body: TaskBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    station = None
    if body.mode == "station":
        station = db.get(Station, body.station_id or "")
        if station is None or station.group_id != g.id:
            raise HTTPException(400, "分时分工位签到必须指定本场次下的二级工位")
    token = checkin_gateway.create_task(body.mode, g.id,
                                        station.id if station else "")
    task = CheckinTask(id=uid("tsk"), activity_id=act.id, group_id=g.id,
                       station_id=station.id if station else None,
                       mode=body.mode, token=token,
                       valid_from=(datetime.fromisoformat(body.valid_from)
                                   if body.valid_from else g.start_time),
                       valid_to=(datetime.fromisoformat(body.valid_to)
                                 if body.valid_to else g.end_time),
                       created_by=user.id)
    db.add(task)
    label = f"{station.name}" if station else f"{g.name}·统一签到"
    audit_log(db, actor=user, action="checkin_task", target_type="checkin",
              target_id=task.id, target_label=f"{act.title}·{label}",
              activity_id=act.id,
              summary=f"生成签到码 {token}（{'工位' if station else '统一'}模式，"
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
        g = db.get(PositionGroup, t.group_id)
        st = db.get(Station, t.station_id) if t.station_id else None
        n = (db.query(func.count(CheckinRecord.id))
             .filter(CheckinRecord.task_id == t.id).scalar() or 0)
        out.append({"id": t.id, "token": t.token, "mode": t.mode,
                    "group_name": g.name,
                    "station_name": st.name if st else "",
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


@router.post("/groups/{group_id}/checkin-reminder")
def send_reminder(group_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """手动补发签到提醒。

    自动提醒在场次开始前 30 分钟发一次；这个按钮用于临时催场——
    只发给还没签到的已确认志愿者，已经到岗的不再打扰。
    """
    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    ensure_activity_manager(db, user, g.activity_id)
    n = notify_checkin(db, g)
    if not n:
        raise HTTPException(400, "该场次没有需要提醒的人（未签到的已确认志愿者为 0）")
    g.reminder_sent_at = now()
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=g.id, target_label=g.name, activity_id=g.activity_id,
              summary=f"手动补发签到提醒：{n} 人（未签到的已确认志愿者）",
              visible=False)
    db.commit()
    return {"ok": True, "count": n}


@router.get("/checkin-tasks/{task_id}/board")
def board(task_id: str, db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    """签到实时大屏。以已确认报名名单为主表（含未签到者），前端 5s 轮询。"""
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    g = db.get(PositionGroup, task.group_id)
    act = db.get(Activity, task.activity_id)

    roster = (db.query(Enrollment, User)
              .join(User, Enrollment.user_id == User.id)
              .filter(Enrollment.group_id == g.id,
                      Enrollment.status == "confirmed")
              .order_by(User.college, User.name).all())
    recs = (db.query(CheckinRecord)
            .filter(CheckinRecord.group_id == g.id).all())
    rec_by_user: dict[str, CheckinRecord] = {r.user_id: r for r in recs}

    items = []
    checked = checked_out = 0
    by_station: dict[str, int] = {}   # 到岗统计只基于有签到记录的
    for enr, u in roster:
        st = db.get(Station, enr.station_id) if enr.station_id else None
        rec = rec_by_user.get(u.id)
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            key = rec.station_id or "_group"
            by_station[key] = by_station.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "station": st.name if st else "待分配",
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
        })
    stations = [{"id": s.id, "name": s.name, "quota": s.quota,
                 "checked": by_station.get(s.id, 0)}
                for s in sorted(g.stations, key=lambda x: x.sort)]
    return {
        "task": {"id": task.id, "token": task.token, "mode": task.mode,
                 "active": task.active},
        "activity_title": act.title, "group_name": g.name,
        "expected": len(roster), "checked": checked,
        "checked_out": checked_out,
        "stations": stations, "records": items[:500],
    }


@router.get("/groups/{group_id}/board")
def group_board(group_id: str, db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """按场次聚合的签到实时大屏（不依赖激活的签到任务，
    任务关闭/不存在时仍可查看到场名单）。前端 5s 轮询。

    以已确认报名（Enrollment.status=confirmed）join User 为主表，
    左连该场次的 CheckinRecord（user_id+group_id 匹配，一人至多一条）。
    """
    g = db.get(PositionGroup, group_id)
    if g is None:
        raise HTTPException(404, "岗位组不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)

    rows = (db.query(Enrollment, User, CheckinRecord)
            .join(User, Enrollment.user_id == User.id)
            .outerjoin(CheckinRecord,
                       (CheckinRecord.user_id == Enrollment.user_id)
                       & (CheckinRecord.group_id == Enrollment.group_id))
            .filter(Enrollment.group_id == g.id,
                    Enrollment.status == "confirmed")
            .order_by(User.college, User.name).all())

    items = []
    checked = checked_out = 0
    by_station: dict[str, int] = {}   # 到岗统计只基于有签到记录者
    for enr, u, rec in rows:
        st = db.get(Station, enr.station_id) if enr.station_id else None
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            key = rec.station_id or "_group"
            by_station[key] = by_station.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "station": st.name if st else "待分配",
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
        })
    stations = [{"id": s.id, "name": s.name, "quota": s.quota,
                 "checked": by_station.get(s.id, 0)}
                for s in sorted(g.stations, key=lambda x: x.sort)]
    return {
        "activity_id": act.id, "activity_title": act.title,
        "group_id": g.id, "group_name": g.name,
        "expected": len(rows), "checked": checked,
        "checked_out": checked_out,
        "stations": stations, "records": items[:500],
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
    g = db.get(PositionGroup, task.group_id)
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
               .filter(Enrollment.group_id == task.group_id,
                       Enrollment.user_id == u.id,
                       Enrollment.status == "confirmed").first())
        if enr is None:
            skipped.append(f"{u.name}（非本场次确认志愿者）")
            continue
        rec = (db.query(CheckinRecord)
               .filter(CheckinRecord.user_id == u.id,
                       CheckinRecord.group_id == task.group_id).first())

        if action == "checkin":
            if rec:
                skipped.append(f"{u.name}（已签到）")
                continue
            db.add(CheckinRecord(id=uid("ckr"), task_id=task.id,
                                 activity_id=task.activity_id,
                                 group_id=task.group_id,
                                 station_id=enr.station_id, user_id=u.id,
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
                      "（生产版将强制加密，密码经企业微信单独发送）",
              visible=False)
    db.commit()
    from urllib.parse import quote
    return StreamingResponse(
        buf, media_type="application/vnd.openxmlformats-officedocument"
                        ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


# ======================= 时长录入与回传 =======================

CHECKOUT_GRACE = timedelta(hours=config.CHECKOUT_GRACE_HOURS)


def _round_half_up(hours: float) -> float:
    """向上取整至 0.5 的倍数：2h13m → 2.5h。"""
    return math.ceil(max(0.0, hours) * 2) / 2


def effective_checkout(rec: CheckinRecord, g: PositionGroup):
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
        g = db.get(PositionGroup, rec.group_id)
        if g is None:
            continue
        checkout, kind = effective_checkout(rec, g)
        if checkout is None:
            # 宽限期内未签退：暂按 min(now, 场次结束) 估算
            eff = min(t, g.end_time)
            flags.setdefault(rec.user_id, set()).add("pending")
        else:
            eff = checkout
            if kind == "auto":
                flags.setdefault(rec.user_id, set()).add("auto")
        hours = (eff - rec.checkin_at).total_seconds() / 3600
        per_user[rec.user_id] = per_user.get(rec.user_id, 0.0) + max(0.0, hours)

    out = []
    for uid_, raw in sorted(per_user.items(), key=lambda kv: -kv[1]):
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
                           hours=round(hours, 2), status="confirmed",
                           source="manual", note=(it.get("note") or "").strip(),
                           confirmed_by=user.id))
        added.append(f"{u.name} {hours}h")
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"手动添加时长：{'、'.join(added)}", visible=False)
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
              summary=f"删除手动时长：{u.name if u else '?'} {r.hours}h（{r.note}）",
              visible=False)
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

    def upsert(user_id: str, hours: float) -> None:
        existing = db.query(HoursRecord).filter(
            HoursRecord.activity_id == act.id,
            HoursRecord.user_id == user_id,
            HoursRecord.source == "checkin").first()
        if existing:
            existing.hours = hours
            return
        db.add(HoursRecord(id=uid("hrs"), activity_id=act.id,
                           user_id=user_id, hours=hours,
                           status="confirmed", source="checkin",
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
            upsert(uid_, h)
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
        count = len(clear)
        summary = (f"确认 {count} 人志愿时长（依据签到签退记录）"
                   + (f"；{len(review)} 人未正常签退、时长待核未确认"
                      if review else ""))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)
    db.commit()
    return {"ok": True, "count": count, "message": summary}


@router.post("/activities/{activity_id}/hours/upload")
def hours_upload(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """回传数智人大（演示模式返回模拟回执）。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id,
                    HoursRecord.status != "uploaded").all())
    if not rows:
        raise HTTPException(400, "没有待回传的时长记录，请先确认时长")
    receipt = shuzhi.upload_service_hours(act.id, rows)
    if not receipt.ok:
        raise HTTPException(502, f"数智人大接口返回失败：{receipt.message}")
    stamp = now()
    for r in rows:
        r.status = "uploaded"
        r.upload_ref = receipt.receipt_no
        r.uploaded_at = stamp
    audit_log(db, actor=user, action="hours_upload", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"回传 {len(rows)} 人时长至数智人大"
                      f"（回执号 {receipt.receipt_no}，{receipt.message}）")
    db.commit()
    return {"ok": True, "receipt_no": receipt.receipt_no,
            "count": len(rows), "message": receipt.message}
