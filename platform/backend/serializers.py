"""API 序列化辅助：模型 → dict。

列表接口只带概览数字（避免为显示"还剩3个名额"拉整份状态）；
详情接口才带岗位树全量。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .enroll_logic import (position_confirmed_count, position_occupied_count,
                           shift_confirmed_count, shift_occupied_count,
                           time_conflicts, unmet_requirements,
                           waitlist_position, waitlist_rows)
from . import config
from .models import (Activity, Attachment, AuditLog, Enrollment, Position,
                     Shift, User, iso, now)


def iso_of(dt):
    return iso(dt)


def display_status(act: Activity) -> tuple[str, str]:
    """广场展示状态：报名中（绿）/ 已截止（黄）/ 进行中（红）/ 已结束（灰）。

    进行中 = 报名已截止且当前时间落在场次时间跨度内；
    已截止的场次时间已过但未手动结束 → 也归为已结束。
    """
    if act.status == "published":
        return "published", "报名中"
    if act.status == "finished":
        return "finished", "已结束"
    if act.status == "closed":
        span = act.time_span()
        t = now()
        if span:
            if t < span[0]:
                return "closed", "已截止"
            if t <= span[1]:
                return "ongoing", "进行中"
            return "finished", "已结束"
        return "closed", "已截止"
    return "draft", "筹备中"


def activity_brief(db: OrmSession, act: Activity) -> dict:
    shifts = sorted(act.shifts, key=lambda g: g.sort)
    total_cap = sum(g.total_capacity() for g in shifts)
    total_conf = sum(shift_confirmed_count(db, g.id) for g in shifts)
    total_used = sum(shift_occupied_count(db, g.id) for g in shifts)
    span = act.time_span()
    ds_code, ds_label = display_status(act)
    return {
        "id": act.id, "title": act.title, "type": act.type,
        "type_label": f"{act.type}类·{config.type_name(act.type)}", "status": act.status,
        "status_label": act.label_status(), "location": act.location,
        "intro": act.intro[:120], "cover_hue": act.cover_hue,
        "display_status": ds_code, "display_label": ds_label,
        "shifts_count": len(shifts),
        "total_capacity": total_cap, "total_confirmed": total_conf,
        # 剩余名额扣掉临近退选扣留的占位，不然广场上显示有位、点进去报不了
        "remaining": max(0, total_cap - total_used),
        "time_text": (f"{span[0]:%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}"
                      if span else "待定"),
        "reg_start": iso_of(act.reg_start), "reg_end": iso_of(act.reg_end),
        "contacts": act.contacts or [],
    }


def position_out(db: OrmSession, p: Position, user=None) -> dict:
    """岗位。带 user 时附上「这个人能不能报」——前端据此灰掉并说明原因，
    比让人点了再被拒要清楚。"""
    confirmed = position_confirmed_count(db, p.id)
    queued = len([r for r in waitlist_rows(db, p.shift_id)
                  if r.position_id == p.id])
    out = {
        "id": p.id, "name": p.name,
        # 岗位没有独立时间：显示所属场次的时段
        "time_text": p.time_text(),
        "location": p.location, "quota": p.quota, "confirmed": confirmed,
        "remaining": max(0, p.quota - position_occupied_count(db, p.id)),
        "waitlist_quota": p.waitlist_quota, "waitlist_count": queued,
        "needs_leader": p.needs_leader,
        "requirement_tags": p.requirement_tags or {},
        "requirement_desc": p.requirement_desc() or "不限",
        "requirement_note": p.requirement_note,
        "training_note": p.training_note,
        "attachments": attachments_out(db, "position", p.id),
        "sort": p.sort,
    }
    leader = (db.query(Enrollment)
              .filter(Enrollment.position_id == p.id,
                      Enrollment.is_leader.is_(True),
                      Enrollment.status == "confirmed").first())
    out["leader_name"] = (db.get(User, leader.user_id).name if leader else "")
    if user is not None:
        unmet = unmet_requirements(user, p)
        out["eligible"] = not unmet
        out["ineligible_reason"] = "；".join(unmet)
    return out


def attachments_out(db: OrmSession, owner_type: str, owner_id: str) -> list[dict]:
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == owner_type,
                    Attachment.owner_id == owner_id)
            .order_by(Attachment.created_at).all())
    return [{"id": a.id, "kind": a.kind, "title": a.title, "size": a.size,
             "url": (a.url if a.kind == "link" else f"/api/materials/{a.id}")}
            for a in rows]


def shift_out(db: OrmSession, g: Shift, user=None) -> dict:
    positions = sorted(g.positions, key=lambda p: p.sort)
    capacity = g.total_capacity()
    out = {
        "id": g.id, "name": g.name,
        "activity_id": g.activity_id,
        "start_time": iso_of(g.start_time), "end_time": iso_of(g.end_time),
        "time_text": g.span_text(),
        # 名额：assign 模式记在场次上，pick 模式是各岗位之和
        "capacity": capacity,
        "confirmed": shift_confirmed_count(db, g.id),
        "remaining": max(0, capacity - shift_occupied_count(db, g.id)),
        "waitlist_quota": g.waitlist_quota,
        "needs_assign": g.needs_assign(),
        "mode": g.mode,
        "mode_label": g.mode_label(),
        "note": g.note, "sort": g.sort,
        "positions": [position_out(db, p, user) for p in positions],
        "attachments": attachments_out(db, "shift", g.id),
        "waitlist_count": len(waitlist_rows(db, g.id)),
    }

    if user is not None:
        enr = (db.query(Enrollment)
               .filter(Enrollment.shift_id == g.id, Enrollment.user_id == user.id,
                       Enrollment.status.in_(("confirmed", "waitlisted",
                                              "pending_confirm")))
               .first())
        if enr:
            out["my_enrollment"] = {
                "enrollment_id": enr.id, "status": enr.status,
                "position_id": enr.position_id,
                "allow_reassign": enr.allow_reassign,
                "is_leader": enr.is_leader,
                "confirm_deadline": iso_of(enr.confirm_deadline),
                "waitlist_position": (waitlist_position(db, enr)
                                      if enr.status != "confirmed" else 0),
            }
    return out


def activity_detail(db: OrmSession, act: Activity, user=None,
                    include_logs: bool = True) -> dict:
    out = activity_brief(db, act)
    out.update({
        "intro": act.intro,
        "contact_info": act.contact_info,
        "qa_group_qr": act.qa_group_qr or "",
        "shifts": sorted([shift_out(db, g, user) for g in act.shifts],
                         key=lambda x: x["start_time"]),
    })
    if include_logs:
        logs = (db.query(AuditLog)
                .filter(AuditLog.activity_id == act.id, AuditLog.visible.is_(True))
                .order_by(AuditLog.created_at.desc()).limit(10).all())
        out["change_logs"] = [l.out() for l in logs]
    # 我在该活动的冲突提示（报名前预检用）
    if user is not None:
        clashes = []
        for g in act.shifts:
            clashes.extend(time_conflicts(db, user, g))
        out["my_time_conflicts"] = clashes
    return out
