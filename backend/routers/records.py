"""补记录活动（不公开招募）的名单导入：学号 + 时长，一人一行。

线下已经办完的活动没有报名、场次和签到，时长只能由管理者整理成名单交进来。
导进来的每一行直接成为这个人在这场活动里的那一笔 HoursRecord（一段
kind="import" 的明细），之后的核准 → 确认/申诉 → 提交时长审核 → 外联确认
→ 已完成，和别的活动走同一条路（见 staff_ops 的时长那一节与 hours.py）。

不另建隐藏场次：HoursRecord 本来就只挂在活动上、不挂场次，导出、我的志愿
时长、申诉、锁定都只认它；造一个假场次反而要在广场、签到、名额、冲突检测、
档案里处处把它挑出来。

导入分两步：先预览，列出会新增谁、覆盖谁（旧值→新值）、几人未改动、跳过
哪几行、哪些学号找不到、谁超过 24 小时、会不会撤销核准；管理者点「确认导入」
才写入，预览之后名单被人改过就不写、请他重新预览（见 import_rows 的指纹）。
写入时审计里按人记下每一笔的旧值→新值，跳过和找不到的行也逐条记，不截断。

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
import hashlib
import io
import json
import math
import re

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession, selectinload

from .. import config, hours as hours_flow
from ..audit import log as audit_log
from ..database import db_session
from ..deps import require_manager
from ..models import (Activity, HoursAppeal, HoursRecord, HoursSegment, User,
                      day_of, iso, now, span_text, uid)
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


def _import_fields(act: Activity, hours: float, note: str) -> dict:
    """导入那一段的字段：时段写活动时间，服务日期取活动开始那天。

    导出的备注、志愿者看到的明细都靠它说明这几个小时是哪一天的。活动时间是
    给人看的紧凑写法，服务日期是完整的那一天。"""
    return {"kind": "import", "hours": hours, "label": note,
            "date": day_of(act.held_start or act.created_at or now()),
            "time_text": (span_text(act.held_start, act.held_end)
                          if act.held_start and act.held_end else "")}


def _import_segment(rec: HoursRecord) -> HoursSegment | None:
    return next((s for s in rec.live_segments() if s.kind == "import"), None)


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


def _fingerprint(act: Activity, records: list[HoursRecord], appealing: set[str],
                 rows: list[tuple[int, list[str]]], users: dict[str, User]) -> str:
    """预览那一刻的名单与这次要导的内容，压成一串指纹。

    确认导入时按同一份内容重算一遍：名单里谁有几小时、备注是什么、谁有待处理的
    申诉、活动核准了没有，再加上这次交上来的每一行，以及这些行里的学号各对上了
    谁（用户 id 与类型，找不到记 None）。任何一项和预览时不同，管理者点下去的就
    不是他看过的那份结果，这时不写，请他重新预览：比如预览时某个学号还没建档、
    列在「找不到」里，确认前刚好有人建了档，不重新预览就会把他悄悄导进名单。
    """
    sids = sorted({(c[0] if c else "").strip() for _n, c in rows} - {""})
    state = {
        "approved": iso(act.hours_approved_at) if act.hours_approved_at else "",
        "records": sorted(
            (r.user_id, round(float(r.hours or 0), 2),
             (seg.label if (seg := _import_segment(r)) is not None else r.note),
             r.id in appealing)
            for r in records),
        "rows": rows,
        "people": [(sid, u.id, u.user_type) if (u := users.get(sid)) else (sid, None, None)
                   for sid in sids],
    }
    raw = json.dumps(state, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def import_rows(db: OrmSession, act: Activity, user: User,
                rows: list[tuple[int, list[str]]], *,
                fingerprint: str | None = None) -> dict:
    """逐行比对名单，算出这次导入会怎样；给了 fingerprint 才真的写入。

    不给 fingerprint 是预览：只读不写，结果里带上这一刻的指纹。给了就是确认
    导入：先锁住名单再按同一份内容重算指纹，和预览时的对不上（这期间名单被人
    改过，或者交上来的内容变了）就 409，不写；对得上才逐行写入。能导的照常导，
    其余逐条说明原因；找不到的学号单列。不提交事务。
    """
    if len(rows) > MAX_ROSTER_ROWS:
        raise HTTPException(400, f"一次最多导入 {MAX_ROSTER_ROWS} 行，请分批导入")
    apply = fingerprint is not None
    if apply:
        # 改的是名单里那几笔的导入段：先锁住这场活动的记录行再读改写（见
        # lock_records，按 id 顺序取锁、取库里的最新值）。调用方已持有活动行锁
        # （_record_act for_update），顺序是活动 → 时长记录，不回头拿活动锁
        records = hours_flow.lock_records(db, act.id)
    else:
        records = (db.query(HoursRecord)
                   .options(selectinload(HoursRecord.segments))
                   .filter(HoursRecord.activity_id == act.id).all())
    existing = {r.user_id: r for r in records}
    rows = [(n, c) for n, c in rows if not _is_header(c)]
    sids = {(c[0] if c else "").strip() for _n, c in rows} - {""}
    users = {u.student_id: u for u in db.query(User)
             .filter(User.student_id.in_(sids or [""])).all()}

    appealing = {a.record_id for a in db.query(HoursAppeal)
                 .filter(HoursAppeal.activity_id == act.id,
                         HoursAppeal.status == "pending").all()}
    current = _fingerprint(act, records, appealing, rows, users)
    if apply and fingerprint != current:
        # 带 code：前端据此自动按最新名单重新预览，别的 409（如已锁定）照常报错
        raise HTTPException(409, {"code": "ROSTER_CHANGED",
                                  "message": "预览之后名单有变动（或导入的内容和"
                                             "预览时不同），请刷新后重新预览再确认"})

    def warn_of(hours: float) -> str:
        return (f"超过 {config.RECORD_WARN_HOURS} 小时，请核对"
                if hours > config.RECORD_WARN_HOURS else "")

    added, skipped, not_found, overwritten = [], [], [], []
    changes = []
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
            old_note = seg.label if seg is not None else rec.note
            new_note = note or old_note        # 这次没写备注就留着原来的
            if rec.hours == hours and new_note == old_note:
                unchanged += 1
                continue
            old = rec.hours
            overwritten.append({"student_id": sid, "name": u.name,
                                "old_hours": old, "hours": hours,
                                "note_changed": new_note != old_note,
                                "warning": warn_of(hours)})
            if not apply:
                continue
            before = rec.settled_hours()
            if seg is None:
                # 名单里这一笔没有导入段（这条记录不是导入来的）：补一段，
                # 段上记的才是这笔账的依据
                rec.add_segment(**_import_fields(act, hours, new_note))
            else:
                seg.refill(hours=hours, label=new_note)
            if old != hours:
                rec.confirmed_at = None        # 本人之前是对着旧数字确认的
            rec.note, rec.confirmed_by = new_note, user.id
            changes.append(hours_flow.hours_change(
                u, before, rec.settled_hours(),
                "名单导入覆盖" + ("（备注改为「" + new_note + "」）"
                               if new_note != old_note else "")))
            continue
        added.append({"student_id": sid, "name": u.name, "hours": hours,
                      "warning": warn_of(hours)})
        if not apply:
            continue
        rec = HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id,
                          source="import", note=note, confirmed_by=user.id)
        db.add(rec)
        rec.add_segment(**_import_fields(act, hours, note))
        existing[u.id] = rec
        changes.append(hours_flow.hours_change(
            u, None, rec.settled_hours(),
            "名单导入新增" + (f"：{note}" if note else "")))

    # 名单有增改、活动又核准过：确认导入会撤销核准，预览时就要说出来
    reapproval_required = bool(added or overwritten) and act.hours_approved_at is not None
    if apply and (added or overwritten):
        hours_flow.revoke_approval(db, act)
        summary = f"导入补记录名单：新增 {len(added)} 人，覆盖 {len(overwritten)} 人"
        if unchanged:
            summary += f"，{unchanged} 人未改动"
        if reapproval_required:
            summary += "；名单变更，原时长核准已撤销"
        if skipped:
            summary += f"；跳过 {len(skipped)} 行"
        if not_found:
            summary += f"；{len(not_found)} 个学号平台里找不到"
        # 跳过的行、找不到的学号也全记下：事后对原名单时，没进账的那几行
        # 为什么没进账同样要说得清。old 为 None：这一条只是说明，没有「从几到几」
        for x in skipped:
            changes.append({"field": "skipped", "label": f"第 {x['line']} 行跳过",
                            "old": None, "new": f"{x['student_id'] or '（无学号）'}"
                                              f"{x['name'] and '（' + x['name'] + '）'}"
                                              f"：{x['reason']}"})
        for x in not_found:
            changes.append({"field": "not_found",
                            "label": f"第 {x['line']} 行平台里找不到",
                            "old": None, "new": x["student_id"]})
        audit_log(db, actor=user, action="roster_import", target_type="hours",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=summary, changes=changes)
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
        parts.append(f"{len(not_found)} 个学号平台里找不到（未建档，"
                     f"请提醒志愿者登录平台，登录后自动创建账户；"
                     f"或核对名单后重新导入）")
    if reapproval_required:
        parts.append("原时长核准已撤销，请到时长管理重新核准" if apply
                     else "确认后原时长核准会撤销，需要重新核准")
    return {"added": added, "overwritten": overwritten, "unchanged": unchanged,
            "skipped": skipped, "not_found": not_found,
            "reapproval_required": reapproval_required,
            "fingerprint": current, "applied": apply,
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
            "hours": rec.settled_hours(),
            "note": seg.label if seg is not None else rec.note,
            "created_at": iso(rec.created_at),
            "appealed": a is not None,
            "appeal_pending": bool(a and a.status == "pending")}


@router.get("/activities/{activity_id}/record-roster")
def record_roster(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """补记录活动的当前名单，外加现在能不能改、为什么不能。"""
    act = _record_act(db, user, activity_id)
    rows = (db.query(HoursRecord)
            .options(selectinload(HoursRecord.segments))
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
            "total_hours": round(sum(r.settled_hours() for r in rows), 1),
            "editable": not block, "block_reason": block,
            "warn_hours": config.RECORD_WARN_HOURS,
            "held_text": (act.time_text()
                          if act.held_start and act.held_end else "")}


class ImportBody(BaseModel):
    text: str
    # 预览时拿到的指纹：确认导入必须带上，见 import_rows
    fingerprint: str = Field(min_length=1, max_length=64)


class PreviewBody(BaseModel):
    text: str


def _text_rows(text: str) -> list[tuple[int, list[str]]]:
    rows = rows_from_text(text)
    if not rows:
        raise HTTPException(400, "没有可导入的内容：每行一个人，「学号 时长」")
    return rows


async def _file_rows(file: UploadFile) -> list[tuple[int, list[str]]]:
    data = await file.read(MAX_ROSTER_BYTES + 1)
    if len(data) > MAX_ROSTER_BYTES:
        raise HTTPException(400, "文件太大（上限 2MB），请只保留学号、时长、备注三列")
    rows = rows_from_file(file.filename or "", data)
    if not rows:
        raise HTTPException(400, "文件里没有可导入的行")
    return rows


# 导入分两步：先预览（只读，返回会新增谁、覆盖谁从几小时到几小时、跳过哪几行、
# 会不会撤销核准，外加一串指纹），管理者看过点「确认导入」，带着指纹再交一次
# 同样的内容才写入。不直接写：导完才看到结果，导错了就已经进账、撤了核准。

@router.post("/activities/{activity_id}/record-roster/import/preview")
def preview_text(activity_id: str, body: PreviewBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """粘贴导入的预览：每行「学号 时长 [备注]」，空格、制表符、逗号都能分隔。"""
    act = _record_act(db, user, activity_id)
    _ensure_importable(db, act)
    return import_rows(db, act, user, _text_rows(body.text))


@router.post("/activities/{activity_id}/record-roster/import")
def import_text(activity_id: str, body: ImportBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """粘贴导入的确认：内容与预览时相同，并带上预览返回的指纹。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    out = import_rows(db, act, user, _text_rows(body.text),
                      fingerprint=body.fingerprint)
    db.commit()
    return out


@router.post("/activities/{activity_id}/record-roster/import-file/preview")
async def preview_file(activity_id: str, file: UploadFile = File(...),
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """上传导入的预览：.xlsx / .csv，前三列依次是学号、时长、备注（备注选填），
    第一行是表头的自动跳过。"""
    act = _record_act(db, user, activity_id)
    _ensure_importable(db, act)
    return import_rows(db, act, user, await _file_rows(file))


@router.post("/activities/{activity_id}/record-roster/import-file")
async def import_file(activity_id: str, file: UploadFile = File(...),
                      fingerprint: str = Form(..., min_length=1, max_length=64),
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """上传导入的确认：再交一次预览过的那份文件，并带上预览返回的指纹。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    out = import_rows(db, act, user, await _file_rows(file),
                      fingerprint=fingerprint)
    db.commit()
    return out


class RecordEditBody(BaseModel):
    hours: float | str
    note: str | None = None


def _record_of(db: OrmSession, act: Activity, record_id: str, *,
               for_update: bool = False) -> HoursRecord:
    """名单里的这一条。for_update：要改它的导入段时先锁住这一行。"""
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
    # 这一段要改写导入段：活动行锁之后接着锁这一条记录，顺序是
    # 活动 → 时长记录（同 import_rows），不回头拿活动锁
    rec = _record_of(db, act, record_id, for_update=True)
    try:
        hours = parse_hours(body.hours)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    u = db.get(User, rec.user_id)
    seg = _import_segment(rec)
    before = rec.settled_hours()
    old_hours = rec.hours
    old_note = seg.label if seg is not None else ""
    note = old_note if body.note is None else body.note.strip()[:120]
    # 补记录的一笔只有导入这一段（时长管理的手动加减对它关着），改的就是它
    if seg is None:
        seg = rec.add_segment(**_import_fields(act, hours, note))
    else:
        seg.refill(hours=hours, label=note)
    rec.note = note
    rec.confirmed_by = user.id
    if hours != old_hours:
        rec.confirmed_at = None
    changes = []
    if hours != old_hours:
        changes.append(f"时长 {old_hours:g}h → {hours:g}h")
    if note != old_note:
        changes.append(f"备注「{old_note or '无'}」→「{note or '无'}」")
    reapproval_required = hours_flow.revoke_approval(db, act) if changes else False
    if changes:
        audit_log(db, actor=user, action="roster_import", target_type="hours",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=f"修改补记录名单：{u.name if u else '?'}"
                          f"（{u.student_id if u else ''}）{'，'.join(changes)}",
                  changes=[hours_flow.hours_change(
                      u, before, rec.settled_hours(),
                      "名单单人修改" + (f"（备注「{old_note or '无'}」→「{note or '无'}」）"
                                     if note != old_note else ""))])
    db.commit()
    return {"ok": True, "hours": rec.settled_hours(),
            "reapproval_required": reapproval_required}


@router.delete("/activities/{activity_id}/record-roster/{record_id}")
def delete_record(activity_id: str, record_id: str,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """整条删掉（导错了人）。提过申诉的不删：申诉挂在这条记录上，删了就
    说不清当时争的是什么——要改就改时长。"""
    act = _record_act(db, user, activity_id, for_update=True)
    _ensure_importable(db, act)
    # 删整条要连它那一段一起删：活动行锁之后接着锁这一条记录，顺序同 import_rows
    rec = _record_of(db, act, record_id, for_update=True)
    if db.query(HoursAppeal).filter(HoursAppeal.record_id == rec.id).first():
        raise HTTPException(409, "这条记录有过时长申诉，不能删除；如需更正请改时长")
    u = db.get(User, rec.user_id)
    reapproval_required = hours_flow.revoke_approval(db, act)
    if reapproval_required and u is not None:
        notify(db, u, f"志愿时长核准已撤销｜{act.title}",
               f"「{act.title}」的补记录名单已更正，您原先收到的时长核准不再有效。"
               "如需了解名单调整，请联系活动负责人。",
               data={"route": "hours"})
    audit_log(db, actor=user, action="roster_import", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"从补记录名单删除：{u.name if u else '?'}"
                      f"（{u.student_id if u else ''}）{rec.settled_hours():g}h"
                      + ("；原时长核准已撤销" if reapproval_required else ""),
              changes=[hours_flow.hours_change(u, rec.settled_hours(), None,
                                               "从名单删除")])
    # 段跟着记录一起删（relationship 的 delete-orphan）：留下孤儿段，
    # 名单、导出、统计都还会把它算进去
    db.delete(rec)
    db.commit()
    return {"ok": True, "reapproval_required": reapproval_required}
