"""管理端：工作台统计、审计日志、志愿者档案与违规记录、集成设置。

页面可见性：
  · 数据总览 → 管理员 / 会长团；集成设置 → 仅管理员
  · 审计日志 → 活动发起方/青协/管理员（非全权者只看自己能管的活动）
  · 志愿者档案（全校名册）→ 管理员 / 会长团 / 部长；
    活动内点姓名看档案 → 该活动的管理者，且只限报了这场活动的人
"""
from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from .. import config, scoring
from ..audit import log as audit_log
from ..database import db_session
from ..deps import (require_admin, require_archive_page, require_council,
                    require_council_download, require_manager)
from ..exporter import (build_activities_summary_workbook, record_settlements,
                        service_activity_ids)
from ..integrations import all_configs, save_config
from ..models import (Activity, AuditLog, CheckinRecord, Enrollment,
                      HoursRecord, Shift, User, ViolationRecord, iso,
                      like_pattern, now, parse_local_dt, span_text, uid)
from ..perm import ensure_activity_manager, managed_activity_ids

router = APIRouter(prefix="/api/staff", tags=["staff-admin"])


@router.get("/overview")
def overview(start: str = "", end: str = "",
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_council)):
    """数据总览。start / end 是 YYYY-MM-DD、end 含当天整天，只改四张
    「这段时间的量」的卡：活动总数、有效报名、累计时长、报名学院分布。
    此刻的状态卡（注册学生、报名中活动、候补中、24 小时签到、近 14 天报名趋势、
    最近操作留痕）与该区间无关，参数怎么改都不动。
    活动总数与累计时长都按服务日期（见 exporter.service_activity_ids 与
    record_settlements）：一笔账整笔算在它最后一次服务那一天所属的区间里，
    办完再补录的时长照样算得进来。
    """
    t = now()
    lo = hi = None
    if start:
        try:
            lo = parse_local_dt(start)
        except ValueError:
            raise HTTPException(400, "开始时间格式不对") from None
    if end:
        try:
            hi = parse_local_dt(end) + timedelta(days=1)
        except ValueError:
            raise HTTPException(400, "截止时间格式不对") from None
    ranged = lo is not None or hi is not None

    total_students = (db.query(func.count(User.id))
                      .filter(User.user_type == "student").scalar() or 0)
    act_rows = db.query(Activity.status, func.count(Activity.id)).group_by(
        Activity.status).all()
    by_status = {s: n for s, n in act_rows}
    # 有效报名：不填区间是此刻在册的（含候补与待确认），填了就是区间内新产生的
    enroll_q = (db.query(func.count(Enrollment.id))
                .filter(Enrollment.status.in_(("enrolled", "waitlisted",
                                               "pending_confirm"))))
    if lo is not None:
        enroll_q = enroll_q.filter(Enrollment.joined_at >= lo)
    if hi is not None:
        enroll_q = enroll_q.filter(Enrollment.joined_at < hi)
    active_enroll = enroll_q.scalar() or 0
    waitlisted = (db.query(func.count(Enrollment.id))
                  .filter(Enrollment.status.in_(("waitlisted", "pending_confirm")))
                  .scalar() or 0)
    checked_today = (db.query(func.count(func.distinct(CheckinRecord.user_id)))
                     .filter(CheckinRecord.checkin_at >= t - timedelta(days=1))
                     .scalar() or 0)

    # 区间内的活动与时长都按服务日期算，和活动汇总导出同一口径（见
    # exporter.service_activity_ids / record_settlements）：这里数出几场，导出
    # 表就有几行。没填区间就是全部活动（含草稿与还没办的）
    if ranged:
        activities_all = len(service_activity_ids(db, start=lo, before=hi))
    else:
        activities_all = sum(by_status.values())
    # 累计时长：区间内各笔结算的志愿时数之和，每一笔整笔归属到它最后一次服务
    # 的日期，活动办完再补录的时长也算在服务发生的那个区间里
    hours_total = sum(counted for _aid, _day, counted, _pay
                      in record_settlements(db, start=lo, before=hi))

    # 近 14 天报名趋势：拉出来在内存按天聚合（演示规模无性能问题，
    # 且 SQLite/PG 的日期函数差异就不用管了）
    trend = [{"date": ((t - timedelta(days=i)).date()).strftime("%m-%d"),
              "count": 0} for i in range(13, -1, -1)]
    rows = db.query(Enrollment.joined_at).filter(
        Enrollment.joined_at >= t - timedelta(days=14)).all()
    counts: dict[str, int] = {}
    for (jt,) in rows:
        key = jt.strftime("%m-%d")
        counts[key] = counts.get(key, 0) + 1
    for item in trend:
        item["count"] = counts.get(item["date"], 0)

    # 学院分布：区间内新产生的在册报名；不填区间就是全部在册报名
    college_q = (db.query(User.college, func.count(Enrollment.id))
                 .join(Enrollment, Enrollment.user_id == User.id)
                 .filter(Enrollment.status == "enrolled"))
    if lo is not None:
        college_q = college_q.filter(Enrollment.joined_at >= lo)
    if hi is not None:
        college_q = college_q.filter(Enrollment.joined_at < hi)
    college_rows = college_q.group_by(User.college).all()
    colleges = [{"college": c or "未填写", "count": n}
                for c, n in sorted(college_rows, key=lambda x: -x[1])[:8]]

    logs = (db.query(AuditLog).order_by(AuditLog.created_at.desc())
            .limit(8).all())

    return {
        "cards": {
            "students": total_students,
            "activities_published": by_status.get("published", 0),
            "activities_all": activities_all,
            "active_enrollments": active_enroll,
            "waitlisted": waitlisted,
            "checked_recent": checked_today,
            "hours_total": round(hours_total or 0.0, 1),
        },
        "enroll_trend": trend,
        "colleges": colleges,
        "recent_logs": [l.out() for l in logs],
    }


@router.get("/activities-summary.xlsx")
def export_activities_summary(start: str = "", end: str = "",
                              db: OrmSession = Depends(db_session),
                              user: User = Depends(require_council_download)):
    """数据总览页的「导出活动汇总」：一场活动一行，末行合计。

    start / end 是 YYYY-MM-DD，能只给一个，都不给就是不限。区间按服务日期
    算，end 含当天整天（口径见 exporter.build_activities_summary_workbook）。
    """
    lo = hi = None
    if start:
        try:
            lo = parse_local_dt(start)
        except ValueError:
            raise HTTPException(400, "开始时间格式不对") from None
    if end:
        try:
            hi = parse_local_dt(end)
        except ValueError:
            raise HTTPException(400, "截止时间格式不对") from None
    buf, count = build_activities_summary_workbook(db, lo, hi)
    # 文件名按解析后的日期拼，不照抄查询串：不然手写地址栏里带的斜杠、
    # 引号会原样进 Content-Disposition
    bounds = ("_".join(f"{x:%Y-%m-%d}" for x in (lo, hi) if x) or "全部")
    filename = f"活动汇总_{bounds}.xlsx"
    audit_log(db, actor=user, action="export", target_type="export",
              target_id="", target_label="活动汇总",
              summary=f"导出活动汇总《{filename}》，{count} 个活动")
    db.commit()
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument"
                   ".spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


# ======================= 审计日志中心 =======================

@router.get("/audit-logs")
def audit_logs(activity_id: str = "", target_type: str = "",
               visible: str = "", page: int = 1, size: int = 20,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    q = db.query(AuditLog)
    managed = managed_activity_ids(db, user)
    if activity_id:
        if managed is not None and activity_id not in managed:
            raise HTTPException(403, "您不是该活动的管理者")
        q = q.filter(AuditLog.activity_id == activity_id)
    elif managed is not None:
        q = q.filter(AuditLog.activity_id.in_(managed or ["__none__"]))
    if target_type:
        q = q.filter(AuditLog.target_type == target_type)
    if visible == "1":
        q = q.filter(AuditLog.visible.is_(True))
    total = q.count()
    rows = (q.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * size).limit(size).all())
    return {"total": total, "page": page, "items": [l.out() for l in rows]}


# ======================= 用户与信用（管理员/青协） =======================

@router.get("/users")
def list_users(q: str = "", college: str = "",
               page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_archive_page)):
    """全校名册，分页。按学号排：学号唯一，翻页时不会有人重复或漏掉。"""
    query = db.query(User).filter(User.user_type == "student")
    if q:
        like = like_pattern(q.strip())
        query = query.filter((User.name.ilike(like, escape="\\")) |
                             (User.student_id.ilike(like, escape="\\")))
    if college:
        query = query.filter(User.college == college)
    total = query.count()
    rows = (query.order_by(User.student_id)
            .offset((page - 1) * size).limit(size).all())
    return {"total": total, "items": [{
        **u.full(),
        "id_card_masked": ("无内地身份证号" if u.no_mainland_id
                           else u.masked_id_card()),
    } for u in rows]}


# ======================= 志愿者档案 =======================

# 本人主动退出的几种。未录取、录到别的场次、场次被删都不是他的行为，不算
SELF_CANCELS = ("normal", "locked", "abandoned", "expired")
CANCEL_WINDOW_DAYS = 90


def _archive(db: OrmSession, u: User) -> dict:
    """一个人的志愿档案：参与过的活动、违规记录、退选画像。

    只是给青协看的记录，不产生任何后果——平台不对志愿者做限制。
    """
    rows = (db.query(Enrollment, Activity, Shift)
            .join(Activity, Enrollment.activity_id == Activity.id)
            .join(Shift, Enrollment.shift_id == Shift.id)
            .filter(Enrollment.user_id == u.id, Shift.kind == "service",
                    # 筛选没录上、录到了别的场次都不是经历，档案里不留
                    Enrollment.cancel_type.notin_(["rejected", "not_chosen"]))
            .order_by(Shift.start_time.desc()).all())
    checked = {r[0] for r in db.query(CheckinRecord.shift_id)
               .filter(CheckinRecord.user_id == u.id).all()}
    dated = [(g.start_time, {
        "enrollment_id": e.id, "activity_id": a.id, "activity_title": a.title,
        "shift_name": g.name, "time_text": g.span_text(),
        "status": e.status, "cancel_type": e.cancel_type,
        "checked_in": g.id in checked,
    }) for e, a, g in rows]
    # 补记录活动没有报名：名单里有他就是参与过，照样进档案（口径同提示分）
    for rec, a in (db.query(HoursRecord, Activity)
                   .join(Activity, HoursRecord.activity_id == Activity.id)
                   .filter(HoursRecord.user_id == u.id,
                           Activity.recruit_mode == config.RECORD_MODE).all()):
        start = a.held_start or rec.created_at
        dated.append((start, {
            "enrollment_id": f"rec_{rec.id}", "activity_id": a.id,
            "activity_title": a.title, "shift_name": "补记录",
            "time_text": (span_text(a.held_start, a.held_end)
                          if a.held_start and a.held_end else ""),
            "status": "recorded", "cancel_type": "", "checked_in": True,
        }))
    dated.sort(key=lambda x: x[0], reverse=True)
    history = [item for _at, item in dated]
    cancels = _cancel_profile(db, u, rows)
    violations = (db.query(ViolationRecord)
                  .filter(ViolationRecord.user_id == u.id)
                  .order_by(ViolationRecord.created_at.desc()).all())
    return {
        "user": {**u.full(),
                 "id_card_masked": ("无内地身份证号" if u.no_mainland_id
                                    else u.masked_id_card())},
        "history": history,
        "score": scoring.detail(db, u),
        "locked_quit_count": sum(1 for h in history if h["cancel_type"] == "locked"),
        "cancels": cancels,
        "violations": [{
            "id": v.id, "activity_id": v.activity_id,
            "activity_title": v.activity_title,
            "kind": v.kind, "kind_label": v.kind_label(), "detail": v.detail,
            "created_at": iso(v.created_at),
        } for v in violations],
    }


def _cancel_profile(db: OrmSession, u: User, rows) -> dict:
    """退选画像：近 90 天退了几次，每次是报名多久之后退的。

    间隔短说明报了就退（占位、试手），间隔长而且卡在名单锁定之后，是另一种问题。
    早于 cancelled_at 这一列存在的旧记录没有时间，间隔给 None，不参与统计。
    """
    since = now() - timedelta(days=CANCEL_WINDOW_DAYS)
    items = []
    for e, a, g in rows:
        if e.cancel_type not in SELF_CANCELS:
            continue
        gap = (None if e.cancelled_at is None
               else round((e.cancelled_at - e.joined_at).total_seconds() / 3600, 1))
        items.append({
            "activity_title": a.title, "shift_name": g.name,
            "cancel_type": e.cancel_type,
            "joined_at": iso(e.joined_at), "cancelled_at": iso(e.cancelled_at),
            "gap_hours": gap,
            "recent": e.cancelled_at is not None and e.cancelled_at >= since,
        })
    gaps = [x["gap_hours"] for x in items if x["gap_hours"] is not None]
    items.sort(key=lambda x: x["cancelled_at"] or "", reverse=True)
    return {
        "window_days": CANCEL_WINDOW_DAYS,
        "recent_count": sum(1 for x in items if x["recent"]),
        "total_count": len(items),
        "median_gap_hours": (sorted(gaps)[len(gaps) // 2] if gaps else None),
        "items": items,
    }


def took_part(db: OrmSession, activity_id: str, user_id: str) -> bool:
    """这个人跟这场活动有关系：报过名，或者在补记录活动的名单里。"""
    if (db.query(Enrollment.id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.user_id == user_id).first()):
        return True
    return (db.query(HoursRecord.id)
            .join(Activity, HoursRecord.activity_id == Activity.id)
            .filter(HoursRecord.activity_id == activity_id,
                    HoursRecord.user_id == user_id,
                    Activity.recruit_mode == config.RECORD_MODE).first()
            is not None)


@router.get("/users/{user_id}/archive")
def user_archive(user_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_archive_page)):
    """全校名册入口：管理员 / 会长团 / 部长。"""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    return _archive(db, u)


@router.get("/activities/{activity_id}/volunteers/{user_id}/archive")
def activity_volunteer_archive(activity_id: str, user_id: str,
                               db: OrmSession = Depends(db_session),
                               user: User = Depends(require_manager)):
    """活动内点姓名看档案：得是这个活动的管理者，且对方报了这场活动。

    院系二级团体也能查自己活动的人，但翻不到别人。部门成员不给。
    """
    if user.is_trainee():
        raise HTTPException(403, "部门成员为学习权限，不能查看志愿者档案")
    ensure_activity_manager(db, user, activity_id)
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    if not took_part(db, activity_id, user_id):
        raise HTTPException(403, "只能查看报了本活动的志愿者")
    return _archive(db, u)


# ======================= 违规记录 =======================

class ViolationBody(BaseModel):
    user_id: str
    activity_id: str
    kind: str
    detail: str = ""
    # 这个人在这场活动已经登记过违规时，前端先把已有的几条摆出来问一句，
    # 确认了再带上它重提——多半是两个管理者各登了一遍，偶尔确实是两回事
    confirm_duplicate: bool = False


def _same_activity_violations(db: OrmSession, activity_id: str,
                              user_id: str) -> list[dict]:
    """这个人在这场活动已有的违规记录，早的在前，带登记人姓名。"""
    rows = (db.query(ViolationRecord)
            .filter(ViolationRecord.activity_id == activity_id,
                    ViolationRecord.user_id == user_id)
            .order_by(ViolationRecord.created_at).all())
    ops = {o.id: o.name for o in db.query(User).filter(
        User.id.in_({v.operator_id for v in rows if v.operator_id}))} if rows else {}
    return [{
        "id": v.id, "kind": v.kind, "kind_label": v.kind_label(),
        "detail": v.detail, "created_at": iso(v.created_at),
        "operator_name": ops.get(v.operator_id, ""),
    } for v in rows]


@router.get("/activities/{activity_id}/volunteers/{user_id}/violations")
def activity_volunteer_violations(activity_id: str, user_id: str,
                                  db: OrmSession = Depends(db_session),
                                  operator: User = Depends(require_manager)):
    """登记违规的对话框一打开就能看到「本活动已登记 N 条」。
    部门成员看不了档案，这里的明细同样不给。"""
    if operator.is_trainee():
        raise HTTPException(403, "部门成员为学习权限，不能查看违规记录")
    ensure_activity_manager(db, operator, activity_id)
    return {"items": _same_activity_violations(db, activity_id, user_id)}


@router.post("/violations")
def create_violation(body: ViolationBody,
                     db: OrmSession = Depends(db_session),
                     operator: User = Depends(require_manager)):
    """在活动的志愿者名单里登记一条违规。只记录，不产生任何后果。"""
    ensure_activity_manager(db, operator, body.activity_id)
    u = db.get(User, body.user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    act = db.get(Activity, body.activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if body.kind not in ViolationRecord.KINDS:
        raise HTTPException(400, "违规类型不合法")
    if not body.detail.strip():
        raise HTTPException(400, "请填写情况简述")
    if not took_part(db, act.id, u.id):
        raise HTTPException(400, f"{u.name} 没有报名本活动")
    if not body.confirm_duplicate:
        existing = _same_activity_violations(db, act.id, u.id)
        if existing:
            raise HTTPException(409, {
                "code": "VIOLATION_DUPLICATE",
                "message": f"{u.name} 在本活动已登记过 {len(existing)} 条违规",
                "items": existing,
            })

    record = ViolationRecord(
        id=uid("vio"), user_id=u.id, activity_id=act.id,
        activity_title=act.title, kind=body.kind,
        detail=body.detail.strip(), operator_id=operator.id)
    db.add(record)
    audit_log(db, actor=operator, action="violation", target_type="user",
              target_id=u.id, target_label=f"{u.name}（{u.student_id}）",
              activity_id=act.id,
              summary=f"违规登记：{record.kind_label()}「{act.title}」——"
                      f"{body.detail.strip()}")
    db.commit()
    return {"ok": True, "id": record.id}


@router.delete("/violations/{record_id}")
def delete_violation(record_id: str, db: OrmSession = Depends(db_session),
                     operator: User = Depends(require_manager)):
    """登记错了可以撤，留痕。"""
    v = db.get(ViolationRecord, record_id)
    if v is None:
        raise HTTPException(404, "记录不存在")
    ensure_activity_manager(db, operator, v.activity_id)
    u = db.get(User, v.user_id)
    audit_log(db, actor=operator, action="violation", target_type="user",
              target_id=v.user_id,
              target_label=f"{u.name}（{u.student_id}）" if u else v.user_id,
              activity_id=v.activity_id,
              summary=f"撤销违规登记：{v.kind_label()}「{v.activity_title}」")
    db.delete(v)
    db.commit()
    return {"ok": True}


# ======================= 集成设置（仅管理员） =======================

@router.get("/integrations")
def get_integrations(db: OrmSession = Depends(db_session),
                     user: User = Depends(require_admin)):
    return all_configs()


@router.put("/integrations/{key}")
def put_integration(key: str, body: dict,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_admin)):
    from ..integrations import DEFAULTS
    if key not in DEFAULTS:
        raise HTTPException(404, "未知集成项")
    clean = {k: v for k, v in body.items() if k not in ("key", "label")}
    save_config(key, clean)
    cfg = next(c for c in all_configs() if c["key"] == key)
    audit_log(db, actor=user, action="integration", target_type="integration",
              target_id=key, target_label=cfg.get("label", key),
              summary=f"更新「{cfg.get('label', key)}」对接配置"
                      f"（{'真实接口' if cfg.get('enabled') else '演示模式'}）")
    db.commit()
    return cfg
