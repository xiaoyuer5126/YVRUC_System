"""教师/青协端：工作台统计（仅管理员）、审计日志、用户与信用（管理员/青协）、集成设置（仅管理员）。

页面可见性：
  · 数据总览、集成设置 → 仅管理员
  · 审计日志 → 老师/青协/管理员（非管理员只看自己管理的活动）
  · 用户与信用 → 管理员 / 青协
"""
from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from ..audit import ACTION_LABELS, log as audit_log
from .. import config, violations
from ..credit import apply_delta
from ..database import db_session
from ..deps import require_admin, require_manager, require_users_page
from ..integrations import all_configs, save_config
from ..models import (Activity, AuditLog, CheckinRecord, Enrollment,
                      HoursRecord, User, ViolationRecord, iso, now, uid)
from ..perm import managed_activity_ids

router = APIRouter(prefix="/api/staff", tags=["staff-admin"])


@router.get("/overview")
def overview(db: OrmSession = Depends(db_session),
             user: User = Depends(require_admin)):
    t = now()
    total_students = (db.query(func.count(User.id))
                      .filter(User.user_type == "student").scalar() or 0)
    act_rows = db.query(Activity.status, func.count(Activity.id)).group_by(
        Activity.status).all()
    by_status = {s: n for s, n in act_rows}
    active_enroll = (db.query(func.count(Enrollment.id))
                     .filter(Enrollment.status.in_(("confirmed", "waitlisted",
                                                    "pending_confirm")))
                     .scalar() or 0)
    waitlisted = (db.query(func.count(Enrollment.id))
                  .filter(Enrollment.status.in_(("waitlisted", "pending_confirm")))
                  .scalar() or 0)
    checked_today = (db.query(func.count(func.distinct(CheckinRecord.user_id)))
                     .filter(CheckinRecord.checkin_at >= t - timedelta(days=1))
                     .scalar() or 0)
    hours_total = db.query(func.coalesce(func.sum(HoursRecord.hours), 0.0)).scalar()

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

    # 学院分布（有效报名）
    college_rows = (db.query(User.college, func.count(Enrollment.id))
                    .join(Enrollment, Enrollment.user_id == User.id)
                    .filter(Enrollment.status == "confirmed")
                    .group_by(User.college).all())
    colleges = [{"college": c or "未填写", "count": n}
                for c, n in sorted(college_rows, key=lambda x: -x[1])[:8]]

    # 信用分布
    credit_buckets = [
        {"label": "90-100", "min": 90}, {"label": "75-89", "min": 75},
        {"label": "60-74", "min": 60}, {"label": "<60", "min": 0}]
    bucket_out = []
    for b in credit_buckets:
        q = db.query(func.count(User.id)).filter(User.user_type == "student")
        if b["label"] == "90-100":
            q = q.filter(User.credit_score >= 90)
        elif b["label"] == "75-89":
            q = q.filter(User.credit_score >= 75, User.credit_score < 90)
        elif b["label"] == "60-74":
            q = q.filter(User.credit_score >= 60, User.credit_score < 75)
        else:
            q = q.filter(User.credit_score < 60)
        bucket_out.append({"label": b["label"],
                           "count": q.scalar() or 0})

    logs = (db.query(AuditLog).order_by(AuditLog.created_at.desc())
            .limit(8).all())

    return {
        "cards": {
            "students": total_students,
            "activities_published": by_status.get("published", 0),
            "activities_all": sum(by_status.values()),
            "active_enrollments": active_enroll,
            "waitlisted": waitlisted,
            "checked_recent": checked_today,
            "hours_total": round(hours_total or 0.0, 1),
        },
        "activity_status": by_status,
        "enroll_trend": trend,
        "colleges": colleges,
        "credit_buckets": bucket_out,
        "recent_logs": [l.out() for l in logs],
    }


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
    items = []
    for l in rows:
        d = l.out()
        d["action_label"] = ACTION_LABELS.get(l.action, l.action)
        items.append(d)
    return {"total": total, "page": page, "items": items}


# ======================= 用户与信用（管理员/青协） =======================

@router.get("/users")
def list_users(q: str = "", college: str = "",
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_users_page)):
    query = db.query(User).filter(User.user_type == "student")
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((User.name.ilike(like)) |
                             (User.student_id.ilike(like)))
    if college:
        query = query.filter(User.college == college)
    rows = query.order_by(User.student_id).limit(200).all()
    return [{
        **u.full(),
        "id_card_masked": ("无内地身份证号" if u.no_mainland_id
                           else u.masked_id_card()),
    } for u in rows]


class CreditBody(BaseModel):
    delta: int
    reason: str


@router.post("/users/{user_id}/credit")
def adjust_credit(user_id: str, body: CreditBody,
                  db: OrmSession = Depends(db_session),
                  operator: User = Depends(require_users_page)):
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    score = apply_delta(db, u, body.delta, body.reason,
                        operator_id=operator.id)
    audit_log(db, actor=operator, action="credit_adjust", target_type="user",
              target_id=u.id, target_label=f"{u.name}（{u.student_id}）",
              summary=f"信用分 {body.delta:+d} → {score} 分，事由：{body.reason}",
              changes=[{"field": "credit_score", "label": "信用分",
                        "old": str(score - body.delta), "new": str(score)}])
    db.commit()
    return {"ok": True, "score": score}


# ——— 违规登记（《志愿者服务规范》限制准入名单） ———

class ViolationPreviewBody(BaseModel):
    user_id: str
    activity_id: str
    kind: str
    has_proof: bool = False


def _violation_context(db: OrmSession, body: ViolationPreviewBody):
    u = db.get(User, body.user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    act = db.get(Activity, body.activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if body.kind not in violations.KINDS:
        raise HTTPException(400, "违规类型不合法")
    return u, act


@router.post("/violations/preview")
def preview_violation(body: ViolationPreviewBody,
                      db: OrmSession = Depends(db_session),
                      operator: User = Depends(require_users_page)):
    """按规范算出建议的限制准入，不落库。"""
    u, act = _violation_context(db, body)
    s = violations.suggest(db, u, act.type, body.kind, has_proof=body.has_proof)
    return {
        "user": {"name": u.name, "student_id": u.student_id},
        "activity": {"title": act.title, "type": act.type},
        "suggest_types": s["types"], "suggest_until": iso(s["until"]),
        "basis": s["basis"], "notes": s["notes"],
        "history_count": s["history_count"], "kind_label": s["kind_label"],
    }


class ViolationBody(ViolationPreviewBody):
    detail: str = ""
    types: list[str] = []          # 操作者确认后的限制类型
    until: str | None = None       # 操作者确认后的截止日


@router.post("/violations")
def create_violation(body: ViolationBody,
                     db: OrmSession = Depends(db_session),
                     operator: User = Depends(require_users_page)):
    """登记一条违规并按确认后的口径落限制准入。"""
    u, act = _violation_context(db, body)
    if not body.detail.strip():
        raise HTTPException(400, "请填写违规情况简述")
    types = [t for t in body.types if t in config.ACTIVITY_TYPES]
    until = None
    if body.until:
        try:
            until = datetime.fromisoformat(body.until)
        except ValueError:
            raise HTTPException(400, "时间格式不合法")
    if types and until is None:
        raise HTTPException(400, "限制准入必须给出截止日期")

    s = violations.suggest(db, u, act.type, body.kind, has_proof=body.has_proof)
    old = f"{'/'.join(u.restricted_types or []) or '无'} 至 {iso(u.restricted_until) or '—'}"
    violations.apply_restriction(db, u, types, until)
    new = f"{'/'.join(types) or '无'} 至 {iso(until) or '—'}"

    record = ViolationRecord(
        id=uid("vio"), user_id=u.id, activity_id=act.id,
        activity_title=act.title, activity_type=act.type, kind=body.kind,
        detail=body.detail.strip(), has_proof=body.has_proof,
        applied_types=types, applied_until=until, basis=s["basis"],
        operator_id=operator.id)
    db.add(record)
    audit_log(db, actor=operator, action="violation", target_type="user",
              target_id=u.id, target_label=f"{u.name}（{u.student_id}）",
              activity_id=act.id,
              summary=f"违规登记：{s['kind_label']}·{act.type}类「{act.title}」"
                      f"（第{s['history_count']}次）→ 限制准入 {new}",
              changes=[{"field": "restriction", "label": "限制准入",
                        "old": old, "new": new}])
    db.commit()
    return {"ok": True, "id": record.id, "restricted_types": types,
            "restricted_until": iso(until)}


@router.get("/users/{user_id}/violations")
def list_violations(user_id: str, db: OrmSession = Depends(db_session),
                    operator: User = Depends(require_users_page)):
    rows = (db.query(ViolationRecord)
            .filter(ViolationRecord.user_id == user_id)
            .order_by(ViolationRecord.created_at.desc()).all())
    return [{
        "id": v.id, "activity_title": v.activity_title,
        "activity_type": v.activity_type,
        "kind": v.kind, "kind_label": violations.KINDS.get(v.kind, v.kind),
        "detail": v.detail, "basis": v.basis,
        "applied_types": v.applied_types or [],
        "applied_until": iso(v.applied_until),
        "created_at": iso(v.created_at),
    } for v in rows]


class RestrictionBody(BaseModel):
    types: list[str] = []
    until: str | None = None


@router.post("/users/{user_id}/restriction")
def set_restriction(user_id: str, body: RestrictionBody,
                    db: OrmSession = Depends(db_session),
                    operator: User = Depends(require_users_page)):
    """手动设置限制准入。违规登记走 /violations，这里用于纠正与撤销。"""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(404, "用户不存在")
    types = [t for t in body.types if t in config.ACTIVITY_TYPES]
    until = None
    if body.until:
        try:
            until = datetime.fromisoformat(body.until)
        except ValueError:
            raise HTTPException(400, "时间格式不合法")
    old = f"{','.join(u.restricted_types or [])} 至 {iso(u.restricted_until)}"
    u.restricted_types = types
    u.restricted_until = until
    new = f"{','.join(types)} 至 {iso(until)}"
    audit_log(db, actor=operator, action="restriction", target_type="user",
              target_id=u.id, target_label=f"{u.name}（{u.student_id}）",
              summary=f"限制准入：{old} → {new}",
              changes=[{"field": "restriction", "label": "限制准入",
                        "old": old, "new": new}])
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
