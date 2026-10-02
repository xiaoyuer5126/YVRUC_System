"""复制活动 / 批量复制岗位。

多天活动、每年重办的固定活动，岗位表几乎一模一样，一个个重建既慢又容易漏。
复制**只带结构**：活动信息、场次、岗位、学习材料。报名、签到、时长、留痕、
管理者名单一概不带——那些属于上一次活动，跟着复制过来就是脏数据。

学习材料的物理文件是各存一份的（不共用），删原件不会把副本弄坏。
"""
from __future__ import annotations

from datetime import timedelta
from shutil import copyfile
from uuid import uuid4

from sqlalchemy.orm import Session as OrmSession

from .models import (Activity, ActivityManager, Attachment, FormQuestion, Position, Shift, User, uid)


def copy_attachments(db: OrmSession, src_type: str, src_id: str,
                      dst_id: str, activity_id: str, operator: User) -> int:
    from .routers.materials import MATERIAL_DIR

    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == src_type,
                    Attachment.owner_id == src_id).all())
    for a in rows:
        stored = a.stored_name
        if a.kind == "file" and stored:
            src = MATERIAL_DIR / stored
            if not src.is_file():
                continue          # 原件已丢，跳过而不是复制出一条死记录
            stored = f"{uuid4().hex}.{stored.rsplit('.', 1)[-1]}"
            MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
            copyfile(src, MATERIAL_DIR / stored)
        db.add(Attachment(id=uid("att"), owner_type=src_type, owner_id=dst_id,
                          activity_id=activity_id, kind=a.kind, title=a.title,
                          stored_name=stored, size=a.size, url=a.url,
                          uploaded_by=operator.id))
    return len(rows)


def copy_position(db: OrmSession, src: Position, target: Shift,
                  operator: User, *, sort: int) -> Position:
    p = Position(id=uid("p"), shift_id=target.id, name=src.name,
                 location=src.location, quota=src.quota,
                 waitlist_quota=src.waitlist_quota,
                 requirement_tags=dict(src.requirement_tags or {}),
                 service_task=src.service_task,
                 position_requirement=src.position_requirement,
                 training_note=src.training_note,
                 needs_leader=src.needs_leader, attire=src.attire,
                 attire_note=src.attire_note,
                 pay_optional=src.pay_optional, pay_note=src.pay_note,
                 group_name=src.group_name,
                 is_placeholder=src.is_placeholder, sort=sort)
    db.add(p)
    db.flush()
    copy_attachments(db, "position", src.id, p.id,
                     target.activity_id, operator)
    return p


def copy_positions_to(db: OrmSession, sources: list[Position],
                      targets: list[Shift], operator: User) -> dict:
    """把一批岗位复制到若干场次。同名岗位跳过——重复的名字会让跨场次
    连续性分不清谁是谁，也让现场对不上人。"""
    copied, skipped = 0, []
    for target in targets:
        existing = {p.name for p in target.positions}
        sort = len(target.positions)
        for src in sources:
            if src.name in existing:
                skipped.append(f"{target.name}·{src.name}")
                continue
            copy_position(db, src, target, operator, sort=sort)
            existing.add(src.name)
            sort += 1
            copied += 1
    return {"copied": copied, "skipped": skipped}


def copy_activity(db: OrmSession, src: Activity, operator: User, *,
                  title: str, shift_days: int, contact_group_qr: str) -> Activity:
    """整活动复制。所有时间整体平移 shift_days 天，落「筹备中」。"""
    delta = timedelta(days=shift_days)
    # 联络群二维码不带：每场活动一个新群，复制时就要求传新图
    act = Activity(id=uid("act"), title=title, recruit_mode=src.recruit_mode,
                   intro=src.intro, organizer=src.organizer,
                   location=src.location, location_point=src.location_point,
                   contacts=[dict(c) for c in (src.contacts or [])],
                   contact_group_qr=contact_group_qr, cover_hue=src.cover_hue,
                   volunteer_requirement=src.volunteer_requirement,
                   volunteer_benefit=src.volunteer_benefit,
                   recruit_campuses=list(src.recruit_campuses or []),
                   recruit_stages=list(src.recruit_stages or []),
                   recruit_colleges=list(src.recruit_colleges or []),
                   approval_dept_id=src.approval_dept_id,
                   status="draft",
                   reg_start=(src.reg_start + delta) if src.reg_start else None,
                   reg_end=(src.reg_end + delta) if src.reg_end else None,
                   held_start=(src.held_start + delta) if src.held_start else None,
                   held_end=(src.held_end + delta) if src.held_end else None,
                   created_by=operator.id)
    db.add(act)
    db.flush()
    # 只有复制的人是管理者：上一次的共管名单未必还对
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=operator.id, added_by=operator.id))

    def clone(src_shift: Shift, parent_id: str | None,
              position_map: dict[str, str]) -> Shift:
        shift = Shift(id=uid("s"), activity_id=act.id, name=src_shift.name,
                      start_time=src_shift.start_time + delta,
                      end_time=src_shift.end_time + delta,
                      mode=src_shift.mode,
                      note=src_shift.note, sort=src_shift.sort,
                      location=src_shift.location, kind=src_shift.kind,
                      hours_mode=src_shift.hours_mode, parent_id=parent_id,
                      # 适用岗位要换成新岗位的 id，照抄会指向旧活动
                      applies_to_positions=[
                          position_map[i]
                          for i in (src_shift.applies_to_positions or [])
                          if i in position_map])
        db.add(shift)
        db.flush()
        return shift

    all_positions: dict[str, str] = {}
    for src_shift in sorted(src.main_shifts(), key=lambda s: s.sort):
        shift = clone(src_shift, None, {})
        position_map = {}
        for i, src_pos in enumerate(sorted(src_shift.positions,
                                           key=lambda p: p.sort)):
            position_map[src_pos.id] = copy_position(
                db, src_pos, shift, operator, sort=i).id
        all_positions.update(position_map)
        for child in sorted((s for s in src.shifts
                             if s.parent_id == src_shift.id),
                            key=lambda s: s.start_time):
            clone(child, shift.id, position_map)
    # 题目里指着别的东西的两处都要换成新 id：适用岗位、显示条件依赖的前一道题。
    # 题目按 sort 复制，显示条件只指向前面的题，所以边复制边建映射就够了
    question_map: dict[str, str] = {}
    for q in (db.query(FormQuestion).filter(FormQuestion.activity_id == src.id)
              .order_by(FormQuestion.sort).all()):
        cond = q.show_if or {}
        src_q = cond.get("question_id")
        show_if = ({"question_id": question_map[src_q],
                    "values": list(cond.get("values") or [])}
                   if src_q in question_map else {})
        row = FormQuestion(id=uid("q"), activity_id=act.id, kind=q.kind,
                           title=q.title, options=list(q.options or []),
                           required=q.required, show_if=show_if,
                           applies_to_positions=[
                               all_positions[i]
                               for i in (q.applies_to_positions or [])
                               if i in all_positions],
                           sort=q.sort)
        db.add(row)
        question_map[q.id] = row.id
    return act


def describe(act: Activity) -> str:
    mains = act.main_shifts()
    n_pos = sum(len(s.positions) for s in mains)
    extra = len(act.shifts) - len(mains)
    return (f"{len(mains)} 个场次、{n_pos} 个岗位"
            + (f"、{extra} 个培训/彩排" if extra else ""))
