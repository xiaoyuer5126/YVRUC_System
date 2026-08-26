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
from ..credit import apply_delta
from ..database import db_session
from ..deps import require_admin, require_manager, require_users_page
from ..integrations import all_configs, save_config
from ..models import (Activity, AuditLog, CheckinRecord, Enrollment,
                      HoursRecord, User, iso, now)
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
        "id_card_masked": (u.id_card[:4] + "**********" + u.id_card[-3:]
                           if len(u.id_card) >= 10 else ""),
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


class RestrictionBody(BaseModel):
    types: list[str] = []
    until: str | None = None


@router.post("/users/{user_id}/restriction")
def set_restriction(user_id: str, body: RestrictionBody,
                    db: OrmSession = Depends(db_session),
                    operator: User = Depends(require_users_page)):
    """限制准入（《志愿者服务规范》）：不得参与 A/B/C/D 中若干类活动。"""
    from .. import config
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
                        "old": old, "new": new}], visible=False)
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
                      f"（{'真实接口' if cfg.get('enabled') else '演示模式'}）",
              visible=False)
    db.commit()
    return cfg
