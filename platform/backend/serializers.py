"""API 序列化辅助：模型 → dict。

列表接口只带概览数字（避免为显示"还剩3个名额"拉整份状态）；
详情接口才带岗位树全量。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .enroll_logic import (shift_confirmed_count, group_held_count,
                           group_occupied_count, position_confirmed_count,
                           time_conflicts, waitlist_position, waitlist_rows)
from . import config
from .models import Activity, AuditLog, Enrollment, Shift, Position, iso, now


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
    total_cap = sum(g.capacity for g in shifts)
    total_conf = sum(shift_confirmed_count(db, g.id) for g in shifts)
    total_used = sum(group_occupied_count(db, g.id) for g in shifts)
    span = act.time_span()
    ds_code, ds_label = display_status(act)
    return {
        "id": act.id, "title": act.title, "type": act.type,
        "type_label": f"{act.type}类·{config.type_name(act.type)}", "status": act.status,
        "status_label": act.label_status(), "location": act.location,
        "intro": act.intro[:120], "cover_hue": act.cover_hue,
        "display_status": ds_code, "display_label": ds_label,
        "groups_count": len(shifts),
        "total_capacity": total_cap, "total_confirmed": total_conf,
        # 剩余名额扣掉临近退选扣留的占位，不然广场上显示有位、点进去报不了
        "remaining": max(0, total_cap - total_used),
        "time_text": (f"{span[0]:%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}"
                      if span else "待定"),
        "reg_start": iso_of(act.reg_start), "reg_end": iso_of(act.reg_end),
        "contacts": act.contacts or [],
    }


def position_out(db: OrmSession, st: Position) -> dict:
    confirmed = position_confirmed_count(db, st.id)
    req_desc = []
    for tag, value in (st.requirement_tags or {}).items():
        if isinstance(value, list):
            if value:
                req_desc.append(f"{tag}={'/'.join(map(str, value))}")
        else:
            req_desc.append(f"{tag}={value}")
    return {
        "id": st.id, "name": st.name,
        # 岗位不再有独立时间：时间随场次（time_text = 场次时段）
        "time_text": st.time_text(),
        "location": st.location, "quota": st.quota, "confirmed": confirmed,
        "remaining": max(0, st.quota - confirmed),
        "requirement_tags": st.requirement_tags or {},
        "requirement_desc": "；".join(req_desc) or "不限",
        "requirement_note": st.requirement_note,
        "training_note": st.training_note,
        "sort": st.sort,
    }


def shift_out(db: OrmSession, g: Shift, user=None) -> dict:
    positions = sorted(g.positions, key=lambda s: s.sort)
    confirmed = shift_confirmed_count(db, g.id)
    held = group_held_count(db, g.id)
    out = {
        "id": g.id, "name": g.name,
        "activity_id": g.activity_id,
        "start_time": iso_of(g.start_time), "end_time": iso_of(g.end_time),
        "time_text": g.span_text(),
        "capacity": g.capacity, "confirmed": confirmed,
        "held_seats": held,
        "remaining": max(0, g.capacity - group_occupied_count(db, g.id)),
        "waitlist_quota": g.waitlist_quota,
        "needs_assign": g.needs_assign(),
        "mode": g.mode,
        "mode_label": g.mode_label(),
        "note": g.note, "sort": g.sort,
        "positions": [position_out(db, s) for s in positions],
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
