"""API 序列化辅助：模型 → dict。

列表接口只带概览数字（避免为显示"还剩3个名额"拉整份状态）；
详情接口才带岗位树全量。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .enroll_logic import (group_leader, group_needs_leader, group_positions,
                           position_enrolled_count,
                           position_occupied_count,
                           shift_enrolled_count, shift_occupied_count,
                           time_conflicts, unmet_requirements,
                           waitlist_position, waitlist_rows)
from . import config
from .models import (Activity, Attachment, AuditLog, Enrollment, Position,
                     Shift, User, iso, now)


def iso_of(dt):
    return iso(dt)


def display_status(act: Activity) -> tuple[str, str]:
    """展示状态：按**时间**算，而不是只看数据库里的 status。

    状态流转由定时任务每分钟推进，中间那一分钟里 status 还没跟上；报名开始
    时间更是完全不体现在 status 上——「已发布但还没到报名时间」的活动，
    以前一律显示「报名中」，学生点进去才发现报不了。

        筹备中 → 未开始报名 → 报名中 → 已截止 → 进行中 → 已结束
    """
    t = now()
    if act.status == "draft":
        return "draft", "筹备中"
    if act.status == "finished":
        return "finished", "已结束"

    span = act.time_span()
    if span and t > span[1]:
        return "finished", "已结束"
    if span and span[0] <= t <= span[1]:
        return "ongoing", "进行中"

    if act.status == "published":
        if act.reg_start and t < act.reg_start:
            return "upcoming", "未开始报名"
        if act.reg_end and t > act.reg_end:
            return "closed", "已截止"
        return "published", "报名中"
    return "closed", "已截止"


def activity_brief(db: OrmSession, act: Activity) -> dict:
    # 只数服务场次：培训/彩排不招人，算进「共几场、多少名额」会把学生看晕
    shifts = sorted(act.main_shifts(), key=lambda g: g.sort)
    total_cap = sum(g.total_capacity() for g in shifts)
    total_conf = sum(shift_enrolled_count(db, g.id) for g in shifts)
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
        "total_capacity": total_cap, "total_enrolled": total_conf,
        # 剩余名额扣掉临近退选扣留的占位，不然广场上显示有位、点进去报不了
        "remaining": max(0, total_cap - total_used),
        "time_text": (f"{span[0]:%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}"
                      if span else "待定"),
        "reg_start": iso_of(act.reg_start), "reg_end": iso_of(act.reg_end),
        "contacts": act.contacts or [],
        "editing": act.editing_since is not None,
    }


def position_out(db: OrmSession, p: Position, user=None, *,
                 enrolled: bool = False, manager: bool = False,
                 same_group: bool = False) -> dict:
    """岗位。

    带 user 时附上「这个人能不能报」——前端据此灰掉并说明原因，比让人点了
    再被拒要清楚。培训内容、学习材料与组长姓名只对**已报名本场次的人**
    和管理者可见：培训材料是发给要上岗的人的，组长姓名属于个人信息，
    不该挂在公开页面上供人浏览。
    """
    # 变量名要和「我是否已报名」的参数区分开：一个是人数，一个是布尔
    n_enrolled = position_enrolled_count(db, p.id)
    occupied = position_occupied_count(db, p.id)
    queued = len([r for r in waitlist_rows(db, p.shift_id)
                  if r.position_id == p.id])
    out = {
        "id": p.id, "name": p.name,
        # 岗位没有独立时间：显示所属场次的时段
        "time_text": p.time_text(),
        "location": p.location, "quota": p.quota, "enrolled": n_enrolled,
        "remaining": max(0, p.quota - occupied),
        # 临近退选扣留的名额：不发出去的话，「已报名 8/10、剩余 0」没人看得懂
        "held_seats": occupied - n_enrolled,
        "waitlist_quota": p.waitlist_quota, "waitlist_count": queued,
        "needs_leader": p.needs_leader,
        "requirement_tags": p.requirement_tags or {},
        "requirement_desc": p.requirement_desc() or "不限",
        "requirement_note": p.requirement_note,
        "group_name": p.group_name,
        "sort": p.sort,
    }
    visible = enrolled or manager
    out["training_visible"] = visible
    out["training_note"] = p.training_note if visible else ""
    out["attachments"] = attachments_out(db, "position", p.id) if visible else []
    out["material_count"] = attachment_count(db, "position", p.id)
    # 组长按「组」取：同组岗位共用一名组长
    siblings = group_positions(db, p)
    out["group_positions"] = [x.name for x in siblings] if len(siblings) > 1 else []
    out["needs_leader"] = group_needs_leader(siblings)
    # 组长联系方式只给**同组已正式报名**的人：现场要找得到组长，
    # 但候补和别的组的人拿他的手机号没有意义，只是多暴露一份个人信息。
    leader = group_leader(db, p)
    leader_user = db.get(User, leader.user_id) if leader else None
    out["leader_name"] = (leader_user.name if leader_user else "") if visible else ""
    out["leader_contact"] = (
        {"phone": leader_user.phone, "wechat": leader_user.wechat}
        if leader_user and (manager or same_group) else None)
    if user is not None:
        unmet = unmet_requirements(user, p)
        out["eligible"] = not unmet
        out["ineligible_reason"] = "；".join(unmet)
    return out


def attachment_count(db: OrmSession, owner_type: str, owner_id: str) -> int:
    """材料**数量**始终公开：让人知道「报名后有 3 份培训材料」是招募信息，
    内容本身才需要报名后才能看。"""
    return (db.query(Attachment)
            .filter(Attachment.owner_type == owner_type,
                    Attachment.owner_id == owner_id).count())


def attachments_out(db: OrmSession, owner_type: str, owner_id: str) -> list[dict]:
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == owner_type,
                    Attachment.owner_id == owner_id)
            .order_by(Attachment.created_at).all())
    return [{"id": a.id, "kind": a.kind, "title": a.title, "size": a.size,
             "url": (a.url if a.kind == "link" else f"/api/materials/{a.id}")}
            for a in rows]


def _same_group(db: OrmSession, my_enr, position: Position) -> bool:
    """我是否**正式报名**在该岗位所属的组里。候补不算：还没确定要去现场。"""
    if my_enr is None or my_enr.status != "enrolled" or not my_enr.position_id:
        return False
    mine = db.get(Position, my_enr.position_id)
    if mine is None:
        return False
    return any(x.id == mine.id for x in group_positions(db, position))


def attached_out(db: OrmSession, s: Shift, my_enr=None) -> dict:
    """附属场次（培训/彩排）在主场次上的一行摘要。

    自己不招人，所以没有名额与候补；关心的是什么时候、在哪、对谁生效、算不算时长。
    """
    applies = s.applies_to_positions or []
    mine = (my_enr is not None and my_enr.status == "enrolled"
            and (not applies or my_enr.position_id in applies))
    return {
        "id": s.id, "name": s.name, "kind": s.kind, "kind_label": s.kind_label(),
        "start_time": iso_of(s.start_time), "end_time": iso_of(s.end_time),
        "time_text": s.span_text(), "location": s.location,
        "hours_mode": s.hours_mode, "hours_label": s.hours_label(),
        "applies_to_positions": applies,
        "enrolled": shift_enrolled_count(db, s.id),
        "mine": mine,        # 我这次报名要不要去这一场
    }


def shift_out(db: OrmSession, g: Shift, user=None, *,
              manager: bool = False) -> dict:
    positions = sorted(g.positions, key=lambda p: p.sort)
    capacity = g.total_capacity()
    # 报名本场次的人才看得到培训内容与组长（见 position_out）
    my_enr = None
    if user is not None:
        my_enr = (db.query(Enrollment)
                  .filter(Enrollment.shift_id == g.id,
                          Enrollment.user_id == user.id,
                          Enrollment.status.in_(("enrolled", "waitlisted",
                                                 "pending_confirm")))
                  .first())
    enrolled = my_enr is not None
    out = {
        "id": g.id, "name": g.name,
        "activity_id": g.activity_id,
        "start_time": iso_of(g.start_time), "end_time": iso_of(g.end_time),
        "time_text": g.span_text(),
        # 名额：assign 模式记在场次上，pick 模式是各岗位之和
        "capacity": capacity,
        "enrolled": shift_enrolled_count(db, g.id),
        "remaining": max(0, capacity - shift_occupied_count(db, g.id)),
        "held_seats": (shift_occupied_count(db, g.id)
                       - shift_enrolled_count(db, g.id)),
        "waitlist_quota": g.waitlist_quota,
        "needs_assign": g.needs_assign(),
        "mode": g.mode,
        "mode_label": g.mode_label(),
        "location": g.location,
        "kind": g.kind, "kind_label": g.kind_label(),
        "parent_id": g.parent_id,
        "applies_to_positions": g.applies_to_positions or [],
        "hours_mode": g.hours_mode, "hours_label": g.hours_label(),
        # 本场次附带的培训/彩排：学生看的是「报这个岗位还要去哪几场」
        "attached": [attached_out(db, s, my_enr)
                     for s in sorted(
                         (x for x in g.activity.shifts if x.parent_id == g.id),
                         key=lambda x: x.start_time)],
        "note": g.note, "sort": g.sort,
        "positions": [position_out(db, p, user, enrolled=enrolled,
                                   manager=manager,
                                   same_group=_same_group(db, my_enr, p))
                      for p in positions],
        # 已报名但还没岗位的人数：pick 模式下岗位名额之和不够时就有人落空，
        # 管理者需要当场看见这个缺口，而不是等分配面板报「未排上」
        "unassigned_count": (db.query(Enrollment)
                             .filter(Enrollment.shift_id == g.id,
                                     Enrollment.status == "enrolled",
                                     Enrollment.position_id.is_(None)).count()),
        "position_quota_sum": sum(p.quota for p in positions),
        "attachments": attachments_out(db, "shift", g.id),
        "waitlist_count": len(waitlist_rows(db, g.id)),
    }

    if my_enr is not None:
        out["my_enrollment"] = {
            "enrollment_id": my_enr.id, "status": my_enr.status,
            "position_id": my_enr.position_id,
            "allow_reassign": my_enr.allow_reassign,
            "is_leader": my_enr.is_leader,
            "confirm_deadline": iso_of(my_enr.confirm_deadline),
            "waitlist_position": (waitlist_position(db, my_enr)
                                  if my_enr.status != "enrolled" else 0),
        }
    return out


def activity_detail(db: OrmSession, act: Activity, user=None,
                    include_logs: bool = True, *, manager: bool = False) -> dict:
    out = activity_brief(db, act)
    out.update({
        "intro": act.intro,
        "contact_info": act.contact_info,
        "qa_group_qr": act.qa_group_qr or "",
        "volunteer_requirement": act.volunteer_requirement,
        "volunteer_benefit": act.volunteer_benefit,
        # 培训/彩排不单列，它们挂在各自主场次的 attached 里
        "shifts": sorted([shift_out(db, g, user, manager=manager)
                          for g in act.main_shifts()],
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
