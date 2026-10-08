"""补记录活动（不公开招募）的名单导入：学号 + 时长，一人一行。

线下已经办完的活动没有报名、场次和签到，时长只能由管理者整理成名单交进来。
导进来的每一行直接成为这个人在这场活动里的那一笔 HoursRecord（一段
kind="import" 的明细），之后的核准 → 确认/申诉 → 提交时长审核 → 外联确认
→ 已完成，和别的活动走同一条路（见 staff_ops 的时长那一节与 hours.py）。

不另建隐藏场次：HoursRecord 本来就只挂在活动上、不挂场次，导出、我的志愿
时长、申诉、锁定都只认它；造一个假场次反而要在广场、签到、名额、冲突检测、
档案里处处把它挑出来。

导入规则：
  · 平台里找不到的学号只列出来提醒，不建档——建档是统一身份认证的事，
    这里补建的人没有学院、校区，也登不了录；
  · 同一批里重复的学号（取第一行）、时长不合法、老师类型（不参与志愿）都跳过
    并逐条说明原因，其余照常导入；时长不设上限，超过 24 小时的照导但标出来请核对；
  · 可以多次导入：新的人追加，**已在名单里的人按这次的时长覆盖**（方便整批改），
    结果里逐个列出旧值→新值；本人有申诉还没处理的不覆盖，列出来提醒；
  · 单人的时长和备注也可以单独改、整条可以删；以上都在时长锁定（含提交审核）之前。
"""
from __future__ import annotations

import csv
import io
import math
import re

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config, hours as hours_flow
from ..audit import log as audit_log
from ..database import db_session
from ..deps import require_manager
from ..models import (Activity, HoursAppeal, HoursRecord, User, iso, span_text,
                      uid)
from ..notify import notify
from ..perm import ensure_activity_manager
from ..stage import ensure_hours_editable

router = APIRouter(prefix="/api/staff", tags=["records"])

# 上传的名单文件：几百行的表撑死几十 KB，给足余量，但不收大文件
MAX_ROSTER_BYTES = 2 * 1024 * 1024
MAX_ROSTER_ROWS = 2000
# 粘贴文本里一行的分隔：空白、制表符、中英文逗号分号、顿号
_SPLIT = re.compile(r"[\s,，;；、]+")
_HOURS_UNIT = re.compile(r"\s*(h|H|小时|时)$")


def _record_act(db: OrmSession, user: User, activity_id: str, *,
                for_update: bool = False) -> Activity:
    ensure_activity_manager(db, user, activity_id)
    # 名单写入与核准、提交审核共用活动行锁；不能在自动报批的同时
    # 沿用旧的「时长可编辑」判断，提交一份志愿者没见过的新账。
    act = ((db.query(Activity).filter(Activity.id == activity_id)
            .populate_existing().with_for_update().one_or_none())
           if for_update else db.get(Activity, activity_id))
    if act is None:
        raise HTTPException(404, "活动不存在")
    if not act.is_record():
        raise HTTPException(409, "只有「不公开招募（补记录）」活动才导入名单；"
                                 "其他活动的时长来自签到，补录请用时长管理的手动加减")
    return act


def _ensure_importable(db: OrmSession, act: Activity) -> None:
    """审批通过（活动落「已结束」）之后、时长锁定之前才能动名单。"""
    if act.status in ("draft", "pending", "approved"):
        raise HTTPException(409, "活动还没通过校青协立项审批，审批通过后才能导入名单")
    ensure_hours_editable(db, act)


def _invalidate_hours_approval(db: OrmSession, act: Activity) -> bool:
    """核准后的名单发生变化：撤销活动的核准与截止时间，要求管理者重新核准。

    自动报批只看活动级截止时间；不撤销就可能把新增或改过的
    时长按旧截止时间直接送审，志愿者没有完整的确认/申诉机会。

    只撤销活动这一级。没改到的人，上次通知过的数值（approved_hours）和本人
    的确认都留着：重新核准时只通知新增的、时长变了的人（见
    staff_ops.hours_approve），改一个人不该惊动整份名单。这一轮提过申诉的人
    例外，清掉他的 approved_hours，重新核准时照样通知到他。
    """
    if act.hours_approved_at is None:
        return False
    appealed = {a.record_id for a in db.query(HoursAppeal).filter(
        HoursAppeal.activity_id == act.id,
        HoursAppeal.created_at >= act.hours_approved_at)}
    act.hours_approved_at = None
    act.appeal_deadline = None
    for rec in (db.query(HoursRecord)
                .filter(HoursRecord.activity_id == act.id,
                        HoursRecord.id.in_(appealed or [""])).all()):
        rec.confirmed_at = None
        rec.approved_hours = None
    return True


def _import_part(act: Activity, hours: float, note: str) -> dict:
    """导入的那一段明细。时段写活动时间：导出的备注、志愿者看到的明细都靠它
    说明这几个小时是哪一天的。"""
    time = (span_text(act.held_start, act.held_end)
            if act.held_start and act.held_end else "")
    return {"id": uid("seg"), "kind": "import", "label": note, "time": time,
            "position": "", "hours": hours}


def _import_segment(rec: HoursRecord) -> dict | None:
    return next((p for p in (rec.parts or []) if p.get("kind") == "import"), None)


def parse_hours(raw) -> float:
    """时长：大于 0、0.5 小时的整数倍，不设上限。不合法抛 ValueError（带原因）。"""
    text = _HOURS_UNIT.sub("", str(raw if raw is not None else "").strip())
    if not text:
        raise ValueError("缺少时长")
    try:
        hours = float(text)
    except ValueError:
        raise ValueError(f"时长「{raw}」不是数字") from None
    if not math.isfinite(hours):
        raise ValueError(f"时长「{raw}」不是有效数字")
    if hours <= 0:
        raise ValueError("时长必须大于 0")
    if not (hours * 2).is_integer():
        raise ValueError(f"时长「{raw}」须为 0.5 小时的整数倍")
    return hours


def _cell(v) -> str:
    """表格单元格转成字符串。学号在 Excel 里常被存成数字（2023001 或
    2023001.0），原样 str 会多出 .0。"""
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).strip()


def rows_from_text(text: str) -> list[tuple[int, list[str]]]:
    """粘贴的文本 → [(行号, [学号, 时长, 备注…])]，空行跳过。"""
    out = []
    for i, line in enumerate((text or "").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        # 备注里可能有空格：只切前两刀，剩下的整段当备注
        cells = _SPLIT.split(line, maxsplit=2)
        out.append((i, cells))
    return out


def rows_from_file(name: str, data: bytes) -> list[tuple[int, list[str]]]:
    """上传的 xlsx / csv → 同上。只读前三列：学号、时长、备注。"""
    lower = (name or "").lower()
    out = []
    if lower.endswith(".xlsx"):
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        except Exception:  # noqa: BLE001  坏文件、改了扩展名的文件都在这儿
            raise HTTPException(400, "读不出这个 Excel 文件，请确认是 .xlsx 格式") from None
        ws = wb.worksheets[0]
        for i, row in enumerate(ws.iter_rows(values_only=True), start=1):
            cells = [_cell(v) for v in list(row)[:3]]
            if any(cells):
                out.append((i, cells))
            if len(out) > MAX_ROSTER_ROWS:
                break
        wb.close()
        return out
    if lower.endswith(".csv") or lower.endswith(".txt"):
        for enc in ("utf-8-sig", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        else:
            raise HTTPException(400, "读不出这个文件的编码，请另存为 UTF-8 的 CSV")
        if lower.endswith(".txt"):
            return rows_from_text(text)
        for i, row in enumerate(csv.reader(io.StringIO(text)), start=1):
            cells = [c.strip() for c in row[:3]]
            if any(cells):
                out.append((i, cells))
        return out
    raise HTTPException(400, "只支持 .xlsx、.csv 文件，或直接粘贴文本")


def _is_header(cells: list[str]) -> bool:
    """表头行（第一格写着「学号」之类）：不当数据，也不报错。"""
    return bool(cells) and "学号" in cells[0]


def import_rows(db: OrmSession, act: Activity, user: User,
                rows: list[tuple[int, list[str]]]) -> dict:
    """逐行导入。能导的照常导，其余逐条说明原因；找不到的学号单列。不提交事务。"""
    if len(rows) > MAX_ROSTER_ROWS:
        raise HTTPException(400, f"一次最多导入 {MAX_ROSTER_ROWS} 行，请分批导入")
    # 覆盖的是整列 parts：先锁住这场活动的记录行再读改写（见 lock_records，
    # 按 id 顺序取锁、取库里的最新值）。调用方已持有活动行锁（_record_act
    # for_update），顺序是活动 → 时长记录，不回头拿活动锁
    existing = {r.user_id: r for r in hours_flow.lock_records(db, act.id)}
    rows = [(n, c) for n, c in rows if not _is_header(c)]
    sids = {(c[0] if c else "").strip() for _n, c in rows} - {""}
    users = {u.student_id: u for u in db.query(User)
             .filter(User.student_id.in_(sids or [""])).all()}

    appealing = {a.record_id for a in db.query(HoursAppeal)
                 .filter(HoursAppeal.activity_id == act.id,
                         HoursAppeal.status == "pending").all()}

    def warn_of(hours: float) -> str:
        return (f"超过 {config.RECORD_WARN_HOURS} 小时，请核对"
                if hours > config.RECORD_WARN_HOURS else "")

    added, skipped, not_found, overwritten = [], [], [], []
    unchanged = 0
    seen: dict[str, int] = {}
    for line, cells in rows:
        sid = (cells[0] if cells else "").strip()
        raw_hours = cells[1] if len(cells) > 1 else ""
        note = (cells[2] if len(cells) > 2 else "").strip()[:120]

        def skip(reason: str, name: str = "") -> None:
            skipped.append({"line": line, "student_id": sid, "name": name,
                            "reason": reason})

        if not sid:
            skip("缺少学号")
            continue
        if sid in seen:
            skip(f"学号重复（第 {seen[sid]} 行已出现，以那一行为准）")
            continue
        seen[sid] = line
        u = users.get(sid)
        if u is None:
            # 不建档：只提醒。时长格式顺带也说一句，省得补完学号再被拦一次
            not_found.append({"line": line, "student_id": sid})
            continue
        try:
            hours = parse_hours(raw_hours)
        except ValueError as e:
            skip(str(e), u.name)
            continue
        if u.user_type == "teacher":
            skip("老师类型用户不参与志愿，不记时长", u.name)
            continue
        rec = existing.get(u.id)
        if rec is not None:
            # 已在名单里：按这次的时长覆盖。申诉没处理完的不动——管理者正对着
            # 那个数判申诉，这边悄悄换掉，判的就不是同一笔账了
            if rec.id in appealing:
                skip(f"有待处理的时长申诉，未覆盖（仍为 {rec.hours:g} 小时），"
                     f"请先处理申诉", u.name)
                continue
            seg = _import_segment(rec)
            old_note = (seg or {}).get("label", rec.note)
            new_note = note or old_note        # 这次没写备注就留着原来的
            if rec.hours == hours and new_note == old_note:
                unchanged += 1
                continue
            old = rec.hours
            rec.parts = [{**(seg or _import_part(act, hours, new_note)),
                          "hours": hours, "label": new_note}]
            rec.hours, rec.note, rec.confirmed_by = hours, new_note, user.id
            if old != hours:
                rec.confirmed_at = None        # 本人之前是对着旧数字确认的
            overwritten.append({"student_id": sid, "name": u.name,
                                "old_hours": old, "hours": hours,
                                "warning": warn_of(hours)})
            continue
        rec = HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id,
                          hours=hours, source="import", note=note,
                          parts=[_import_part(act, hours, note)],
                          confirmed_by=user.id)
        db.add(rec)
        existing[u.id] = rec
        added.append({"student_id": sid, "name": u.name, "hours": hours,
                      "warning": warn_of(hours)})

    reapproval_required = False
    if added or overwritten:
        reapproval_required = _invalidate_hours_approval(db, act)
        summary = "导入补记录名单"
        if reapproval_required:
            summary += "；名单变更，原时长核准已撤销"
        if added:
            detail = "、".join(f"{a['name']}{a['hours']:g}h" for a in added[:8])
            summary += (f"，新增 {len(added)} 人：{detail}"
                        + ("…" if len(added) > 8 else ""))
        if overwritten:
            detail = "、".join(f"{o['name']}（{o['student_id']}）"
                              f"{o['old_hours']:g}h→{o['hours']:g}h"
                              for o in overwritten[:8])
            summary += (f"，覆盖 {len(overwritten)} 人：{detail}"
                        + ("…" if len(overwritten) > 8 else ""))
        if skipped:
            summary += f"；跳过 {len(skipped)} 行"
        if not_found:
            summary += (f"；{len(not_found)} 个学号平台里找不到："
                        + "、".join(x["student_id"] for x in not_found[:8])
                        + ("…" if len(not_found) > 8 else ""))
        audit_log(db, actor=user, action="roster_import", target_type="hours",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=summary)
    parts = [f"新增 {len(added)} 人"]
    if overwritten:
        parts.append(f"覆盖 {len(overwritten)} 人")
    if unchanged:
        parts.append(f"{unchanged} 人与名单一致未改动")
    flagged = sum(1 for x in added + overwritten if x["warning"])
    if flagged:
        parts.append(f"{flagged} 人超过 {config.RECORD_WARN_HOURS} 小时请核对")
    if skipped:
        parts.append(f"跳过 {len(skipped)} 行")
    if not_found:
        parts.append(f"{len(not_found)} 个学号平台里找不到（未建档）")
    if reapproval_required:
        parts.append("原时长核准已撤销，请到时长管理重新核准")
    return {"added": added, "overwritten": overwritten, "unchanged": unchanged,
            "skipped": skipped, "not_found": not_found,
            "reapproval_required": reapproval_required,
            "message": "，".join(parts)}


# ======================= 接口 =======================

def _row_out(db: OrmSession, rec: HoursRecord, users: dict,
             appeals: dict) -> dict:
    u = users.get(rec.user_id)
    seg = _import_segment(rec)
    a = appeals.get(rec.id)
    return {"id": rec.id,
            "user": ({"id": u.id, "name": u.name, "student_id": u.student_id,
                      "college": u.college} if u else None),
            "hours": rec.hours, "note": (seg or {}).get("label", rec.note),
            "created_at": iso(rec.created_at),
            "appealed": a is not None,
            "appeal_pending": bool(a and a.status == "pending")}


@router.get("/activities/{activity_id}/record-roster")
def record_roster(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """补记录活动的当前名单，外加现在能不能改、为什么不能。"""
    act = _record_act(db, user, activity_id)
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id)
            .order_by(HoursRecord.created_at).all())
    users = {u.id: u for u in db.query(User)
             .filter(User.id.in_([r.user_id for r in rows] or [""])).all()}
    appeals = {}
    for a in (db.query(HoursAppeal)
              .filter(HoursAppeal.activity_id == act.id)
              .order_by(HoursAppeal.created_at).all()):
        appeals[a.record_id] = a
    block = ""
    try:
        _ensure_importable(db, act)
    except HTTPException as e:
        block = e.detail
    return {"items": [_row_out(db, r, users, appeals) for r in rows],
            "total_hours": round(sum(r.hours for r in rows), 1),
            "editable": not block, "block_reason": block,
            "warn_hours": config.RECORD_WARN_HOURS,
            "held_text": (span_text(act.held_start, act.held_end)
                          if act.held_start and act.held_end else "")}


class ImportBody(BaseModel):
    text: str


@router.post("/activities/{activity_id}/record-roster/import")
def import_text(activity_id: str, body: ImportBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """粘贴导入：每行「学号 时长 [备注]」，空格、制表符、逗号都能分隔。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    rows = rows_from_text(body.text)
    if not rows:
        raise HTTPException(400, "没有可导入的内容：每行一个人，「学号 时长」")
    out = import_rows(db, act, user, rows)
    db.commit()
    return out


@router.post("/activities/{activity_id}/record-roster/import-file")
async def import_file(activity_id: str, file: UploadFile = File(...),
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """上传导入：.xlsx / .csv，前三列依次是学号、时长、备注（备注选填），
    第一行是表头的自动跳过。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    data = await file.read(MAX_ROSTER_BYTES + 1)
    if len(data) > MAX_ROSTER_BYTES:
        raise HTTPException(400, "文件太大（上限 2MB），请只保留学号、时长、备注三列")
    rows = rows_from_file(file.filename or "", data)
    if not rows:
        raise HTTPException(400, "文件里没有可导入的行")
    out = import_rows(db, act, user, rows)
    db.commit()
    return out


class RecordEditBody(BaseModel):
    hours: float | str
    note: str | None = None


def _record_of(db: OrmSession, act: Activity, record_id: str, *,
               for_update: bool = False) -> HoursRecord:
    """名单里的这一条。for_update：要重写它的 parts 时先锁住这一行。"""
    rec = ((db.query(HoursRecord).filter(HoursRecord.id == record_id)
            .populate_existing().with_for_update().one_or_none())
           if for_update else db.get(HoursRecord, record_id))
    if rec is None or rec.activity_id != act.id:
        raise HTTPException(404, "名单里没有这条记录")
    return rec


@router.patch("/activities/{activity_id}/record-roster/{record_id}")
def edit_record(activity_id: str, record_id: str, body: RecordEditBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """改一个人的时长或备注。数值变了，本人之前的确认作废（同手动加减）。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    # 这一段明细是整列重写：活动行锁之后接着锁这一条记录，顺序是
    # 活动 → 时长记录（同 import_rows），不回头拿活动锁
    rec = _record_of(db, act, record_id, for_update=True)
    try:
        hours = parse_hours(body.hours)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    u = db.get(User, rec.user_id)
    seg = _import_segment(rec)
    old_hours = rec.hours
    old_note = (seg or {}).get("label", "")
    note = old_note if body.note is None else body.note.strip()[:120]
    # 补记录的一笔只有导入这一段（时长管理的手动加减对它关着），改的就是它
    rec.parts = [{**(seg or _import_part(act, hours, note)),
                  "hours": hours, "label": note}]
    rec.note = note
    rec.hours = hours
    rec.confirmed_by = user.id
    if rec.hours != old_hours:
        rec.confirmed_at = None
    changes = []
    if rec.hours != old_hours:
        changes.append(f"时长 {old_hours:g}h → {rec.hours:g}h")
    if note != old_note:
        changes.append(f"备注「{old_note or '无'}」→「{note or '无'}」")
    reapproval_required = _invalidate_hours_approval(db, act) if changes else False
    if changes:
        audit_log(db, actor=user, action="roster_import", target_type="hours",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=f"修改补记录名单：{u.name if u else '?'}"
                          f"（{u.student_id if u else ''}）{'，'.join(changes)}")
    db.commit()
    return {"ok": True, "hours": rec.hours,
            "reapproval_required": reapproval_required}


@router.delete("/activities/{activity_id}/record-roster/{record_id}")
def delete_record(activity_id: str, record_id: str,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """整条删掉（导错了人）。提过申诉的不删：申诉挂在这条记录上，删了就
    说不清当时争的是什么——要改就改时长。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    rec = _record_of(db, act, record_id)
    if db.query(HoursAppeal).filter(HoursAppeal.record_id == rec.id).first():
        raise HTTPException(409, "这条记录有过时长申诉，不能删除；如需更正请改时长")
    u = db.get(User, rec.user_id)
    reapproval_required = _invalidate_hours_approval(db, act)
    if reapproval_required and u is not None:
        notify(db, u, f"志愿时长核准已撤销｜{act.title}",
               f"「{act.title}」的补记录名单已更正，您原先收到的时长核准不再有效。"
               "如需了解名单调整，请联系活动负责人。",
               data={"route": "hours"})
    audit_log(db, actor=user, action="roster_import", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"从补记录名单删除：{u.name if u else '?'}"
                      f"（{u.student_id if u else ''}）{rec.hours:g}h"
                      + ("；原时长核准已撤销" if reapproval_required else ""))
    db.delete(rec)
    db.commit()
    return {"ok": True, "reapproval_required": reapproval_required}
