"""编辑态的结构快照与还原。

已发布的活动要先点「调整岗位」才能改场次与岗位，期间报名暂停。改动每一步
当场落库，只是学生看不到。「放弃改动」把这一整段时间的结构还原成进入调整
时的样子，再恢复报名。

快照存 Activity.editing_snapshot，跟着编辑态一起清掉。名单、签到码、暂停与
锁定开关不进这份快照：它们不是结构调整，改了就是改了。

还原前先把所有拦下的理由查完，一处不行整件事都不做——删过的场次连名单一起
清掉了，按 id 建回来的行填补不了那个洞，半还原比不还原更难收拾。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy.orm import Session as OrmSession

from .models import (Activity, Attachment, CheckinTask, Enrollment,
                     FormQuestion, Position, Shift, ShiftPeriod, parse_local_dt)

# 编辑期间能改、放弃时要还原的活动字段
_ACTIVITY_KEYS = ("title", "recruit_mode", "intro", "location", "location_point",
                  "organizer", "co_organizer", "contacts", "contact_group_qr",
                  "volunteer_requirement", "precautions", "volunteer_benefit",
                  "recruit_campuses", "recruit_stages", "recruit_colleges",
                  "reg_start", "reg_end", "held_periods", "cover_hue")
_TIME_KEYS = ("reg_start", "reg_end")

# 暂停、锁定、自动锁定用没用掉都是单独拨的开关，不进快照
_SHIFT_KEYS = ("name", "location", "mode", "kind", "applies_to_positions",
               "hours_mode", "requirement_tags", "note", "sort",
               "flexible_time", "lock_hours")

_POSITION_KEYS = ("name", "location", "quota", "waitlist_quota",
                  "requirement_tags", "service_task", "position_requirement",
                  "training_note", "needs_leader", "leader_task", "attire",
                  "attire_note", "pay_optional", "pay_note", "group_name",
                  "is_placeholder", "sort")

# 学习材料只存「原来有哪几份」：还原时删掉编辑期间新加的，删过的拦下
_ATTACHMENT_KEYS = ("owner_type", "owner_id", "kind", "title", "stored_name",
                    "size", "url", "uploaded_by")


def _dt_text(value: datetime | None) -> str:
    """时间存成串。带上秒和微秒：还原时按分钟截断，场次就被悄悄挪了几秒。"""
    return value.isoformat() if value else ""


def snapshot(db: OrmSession, act: Activity) -> dict:
    """进入编辑态时拍一份结构。"""
    fields = {}
    for key in _ACTIVITY_KEYS:
        value = getattr(act, key)
        fields[key] = _dt_text(value) if key in _TIME_KEYS else deepcopy(value)
    return {
        "activity": fields,
        "shifts": [_shift_snapshot(g) for g in act.shifts],
        "attachments": [
            {k: deepcopy(getattr(a, k)) for k in _ATTACHMENT_KEYS}
            | {"id": a.id, "activity_id": a.activity_id}
            for a in _attachments(db, act.id)
        ],
        # 题目本身发布后改不了，但删岗位、开放选岗会顺手修剪它们圈的岗位
        "questions": {q.id: list(q.applies_to_positions or [])
                      for q in db.query(FormQuestion)
                      .filter(FormQuestion.activity_id == act.id).all()},
        # 报名记录不还原，但要认得出它有没有少：删场次会连带清掉名单
        "enrollments": [r[0] for r in db.query(Enrollment.id)
                        .filter(Enrollment.activity_id == act.id).all()],
    }


def _shift_snapshot(g: Shift) -> dict:
    return {
        "id": g.id,
        **{k: deepcopy(getattr(g, k)) for k in _SHIFT_KEYS},
        "periods": [{"id": p.id, "start_time": _dt_text(p.start_time),
                     "end_time": _dt_text(p.end_time), "sort": p.sort}
                    for p in g.periods],
        "positions": [{"id": p.id,
                       **{k: deepcopy(getattr(p, k)) for k in _POSITION_KEYS}}
                      for p in g.positions],
    }


def _attachments(db: OrmSession, activity_id: str) -> list[Attachment]:
    """学习材料挂岗位（owner_type=position）。问卷附件、通知附件另有归属。"""
    return (db.query(Attachment)
            .filter(Attachment.activity_id == activity_id,
                    Attachment.owner_type == "position").all())


# ======================= 拦下的理由 =======================

def _check(db: OrmSession, act: Activity, snap: dict) -> None:
    """凡是还原不回去的，都在动手之前说完。"""
    now_ids = {r[0] for r in db.query(Enrollment.id)
               .filter(Enrollment.activity_id == act.id).all()}
    if set(snap["enrollments"]) - now_ids:
        raise HTTPException(
            409, "调整期间删掉的场次上还有报名记录，它们已经跟着一起清掉了，"
                 "放弃改动也找不回来；请手动把场次和名单补回来，或保存发布")
    _check_title(db, act, snap["activity"])
    _check_attachments(db, act, snap["attachments"])
    shifts = {g.id: g for g in act.shifts}
    for row in snap["shifts"]:
        g = shifts.get(row["id"])
        if g is not None:
            _check_positions(db, g, row["positions"], g.name)
            _check_periods(db, g, row["periods"])
    for gid, g in shifts.items():
        if any(r["id"] == gid for r in snap["shifts"]):
            continue
        if db.query(Enrollment.id).filter(Enrollment.shift_id == gid).first() \
                or db.query(CheckinTask.id).filter(CheckinTask.shift_id == gid).first():
            raise HTTPException(
                409, f"调整期间新建的场次「{g.name}」里已经有人或签到码，"
                     f"撤掉它就没处安放；请先处理这一场，或保存发布")
        _check_positions(db, g, [], g.name)


def _check_title(db: OrmSession, act: Activity, fields: dict) -> None:
    title = str(fields.get("title") or "").strip()
    if not title or title == act.title:
        return
    taken = (db.query(Activity.id)
             .filter(Activity.title == title, Activity.id != act.id).first())
    if taken:
        raise HTTPException(
            409, f"活动名称在这段时间里被别的活动用了「{title}」，"
                 f"放弃改动还原不回去；请先改名，或保存发布")


def _check_attachments(db: OrmSession, act: Activity, rows: list[dict]) -> None:
    now_ids = {a.id for a in _attachments(db, act.id)}
    missing = {r["id"]: r for r in rows if r["id"] not in now_ids}
    if missing:
        first = next(iter(missing.values()))
        raise HTTPException(
            409, f"调整期间删掉了《{first['title']}》等 {len(missing)} 份学习材料，"
                 f"文件在删除时已经清掉，放弃改动找不回来；请重新上传，或保存发布")


def _check_positions(db: OrmSession, g: Shift, rows: list[dict],
                     shift_name: str) -> None:
    keep = {r["id"] for r in rows}
    for p in g.positions:
        if p.id in keep:
            continue
        if db.query(Enrollment.id).filter(Enrollment.position_id == p.id).first() \
                or db.query(CheckinTask.id).filter(CheckinTask.position_id == p.id).first():
            raise HTTPException(
                409, f"场次「{shift_name}」调整期间新建的岗位「{p.name}」上"
                     f"已经有人或签到码，撤不掉；请先把人调走，或保存发布")


def _check_periods(db: OrmSession, g: Shift, rows: list[dict]) -> None:
    keep = {r["id"] for r in rows}
    for p in g.periods:
        if p.id in keep:
            continue
        if db.query(CheckinTask.id).filter(CheckinTask.period_id == p.id).first():
            raise HTTPException(
                409, f"场次「{g.name}」调整期间新建的时段上开过签到码，撤不掉；"
                     f"请先在签到里处理这一场，或保存发布")


# ======================= 还原 =======================

def restore(db: OrmSession, act: Activity, snap: dict) -> int:
    """把活动结构与基本信息还原成快照里的样子，返回改了多少处。

    调用方持活动行锁，并在同一个事务里提交；这里不 commit。
    """
    _check(db, act, snap)
    restored = _restore_activity(act, snap["activity"])
    restored += _restore_shifts(db, act, snap["shifts"])
    restored += _restore_questions(db, act, snap["questions"])
    restored += _restore_attachments(db, act, snap["attachments"])
    db.flush()
    return restored


def _restore_activity(act: Activity, fields: dict) -> int:
    changed = 0
    for key in _ACTIVITY_KEYS:
        if key not in fields:
            continue
        value = fields[key]
        if key == "title":
            value = str(value or "").strip()
        elif key in _TIME_KEYS:
            value = parse_local_dt(value) if value else None
        else:
            value = deepcopy(value)
        if getattr(act, key) != value:
            setattr(act, key, value)
            changed += 1
    return changed


def _restore_shifts(db: OrmSession, act: Activity, rows: list[dict]) -> int:
    snap = {r["id"]: r for r in rows}
    current = {g.id: g for g in act.shifts}
    changed = 0
    for gid, g in list(current.items()):
        if gid in snap:
            continue
        act.shifts.remove(g)      # 场次下的岗位、时段随它一起删
        db.delete(g)
        del current[gid]
        changed += 1
    for gid, row in snap.items():
        g = current.get(gid)
        if g is None:
            g = Shift(id=gid)
            act.shifts.append(g)
            changed += 1
        changed += _restore_shift_fields(g, row)
        changed += _restore_periods(db, g, row["periods"])
        changed += _restore_positions(db, g, row["positions"])
    return changed


def _restore_shift_fields(g: Shift, row: dict) -> int:
    changed = 0
    for key in _SHIFT_KEYS:
        value = deepcopy(row.get(key))
        if getattr(g, key) != value:
            setattr(g, key, value)
            changed += 1
    return changed


def _restore_periods(db: OrmSession, g: Shift, rows: list[dict]) -> int:
    snap = {r["id"]: r for r in rows}
    current = {p.id: p for p in g.periods}
    changed = 0
    for pid, p in list(current.items()):
        if pid in snap:
            continue
        g.periods.remove(p)
        db.delete(p)
        changed += 1
    for pid, row in snap.items():
        start, end = (parse_local_dt(row["start_time"]),
                      parse_local_dt(row["end_time"]))
        p = current.get(pid)
        if p is None:
            g.periods.append(ShiftPeriod(id=pid, sort=row["sort"],
                                         start_time=start, end_time=end))
            changed += 1
            continue
        if (p.start_time, p.end_time, p.sort) != (start, end, row["sort"]):
            p.start_time, p.end_time, p.sort = start, end, row["sort"]
            changed += 1
    return changed


def _restore_positions(db: OrmSession, g: Shift, rows: list[dict]) -> int:
    snap = {r["id"]: r for r in rows}
    current = {p.id: p for p in g.positions}
    changed = 0
    for pid, p in list(current.items()):
        if pid in snap:
            continue
        g.positions.remove(p)
        db.delete(p)
        changed += 1
    for pid, row in snap.items():
        p = current.get(pid)
        if p is None:
            p = Position(id=pid)
            g.positions.append(p)
            changed += 1
        for key in _POSITION_KEYS:
            value = deepcopy(row.get(key))
            if getattr(p, key) != value:
                setattr(p, key, value)
                changed += 1
    return changed


def _restore_questions(db: OrmSession, act: Activity, rows: dict) -> int:
    changed = 0
    for q in (db.query(FormQuestion)
              .filter(FormQuestion.activity_id == act.id).all()):
        if q.id in rows and (q.applies_to_positions or []) != rows[q.id]:
            q.applies_to_positions = list(rows[q.id])
            changed += 1
    return changed


def _restore_attachments(db: OrmSession, act: Activity, rows: list[dict]) -> int:
    from .routers.materials import MATERIAL_DIR

    keep = {r["id"] for r in rows}
    changed = 0
    for a in _attachments(db, act.id):
        if a.id in keep:
            continue
        # 编辑期间新加的：记录和文件一起撤掉，与删除材料时一致
        if a.kind == "file" and a.stored_name:
            (MATERIAL_DIR / a.stored_name).unlink(missing_ok=True)
        db.delete(a)
        changed += 1
    return changed
