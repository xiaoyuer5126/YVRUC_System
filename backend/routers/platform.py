"""平台级的两样东西：操作日志的查看与导出、平台弹窗公告。

  · 操作日志 → 仅管理员（会长团不行）。记录本身在文件里，见 oplog.py。
  · 平台公告 → 管理员编辑；所有登录用户读当前生效的那一条。公告的修改
    不另写审计日志：改公告的请求本身进操作日志。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from .. import config, oplog
from ..database import db_session
from ..deps import current_user, require_admin, require_admin_download
from ..exporter import disarm_formulas
from ..models import (Activity, Announcement, User, iso, like_pattern, now,
                      parse_local_dt)

router = APIRouter(tags=["platform"])


# ======================= 操作日志 =======================

def _window(start: str, end: str) -> tuple[datetime, datetime]:
    """查询的时间段。不填就是最近 7 天；最长 OPLOG_QUERY_MAX_DAYS 天。"""
    try:
        hi = parse_local_dt(end) if end.strip() else now()
        lo = parse_local_dt(start) if start.strip() else hi - timedelta(days=7)
    except ValueError as exc:
        raise HTTPException(400, "时间格式不正确") from exc
    if lo > hi:
        raise HTTPException(400, "开始时间不能晚于结束时间")
    if hi - lo > timedelta(days=config.OPLOG_QUERY_MAX_DAYS):
        raise HTTPException(
            400, f"一次最多查 {config.OPLOG_QUERY_MAX_DAYS} 天，请缩短时间段")
    return lo, hi


def _activity_ids(db: OrmSession, activity: str) -> set[str] | None:
    """活动筛选：按活动名称包含或活动编号匹配。不填返回 None（不筛）。"""
    text = activity.strip()
    if not text:
        return None
    rows = (db.query(Activity.id)
            .filter((Activity.title.like(like_pattern(text), escape="\\"))
                    | (Activity.id == text)).all())
    return {r.id for r in rows}


def _rows(db: OrmSession, start: str, end: str, person: str, activity: str,
          kind: str) -> list[dict]:
    if kind and kind not in oplog.KIND_LABELS:
        raise HTTPException(400, "未知操作类型")
    lo, hi = _window(start, end)
    return oplog.query(lo, hi, person=person,
                       activity_ids=_activity_ids(db, activity), kind=kind)


def _titles(db: OrmSession, rows: list[dict]) -> dict[str, str]:
    ids = {r["activity_id"] for r in rows if r.get("activity_id")}
    if not ids:
        return {}
    return dict(db.query(Activity.id, Activity.title)
                .filter(Activity.id.in_(ids)).all())


def _out(row: dict, titles: dict[str, str]) -> dict:
    return {**row, "kind_label": oplog.KIND_LABELS.get(row.get("kind"), ""),
            "activity_title": titles.get(row.get("activity_id") or "", "")}


@router.get("/api/staff/oplogs")
def list_oplogs(start: str = "", end: str = "", person: str = "",
                activity: str = "", kind: str = "",
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_admin)):
    """操作日志列表：按人（学号或姓名）、时间段、活动、操作类型筛选，新的在前。"""
    rows = _rows(db, start, end, person, activity, kind)
    items = rows[(page - 1) * size: page * size]
    titles = _titles(db, items)
    return {"total": len(rows), "items": [_out(r, titles) for r in items],
            "kinds": oplog.KIND_LABELS,
            "max_days": config.OPLOG_QUERY_MAX_DAYS}


@router.get("/api/staff/oplogs.xlsx")
def export_oplogs(start: str = "", end: str = "", person: str = "",
                  activity: str = "", kind: str = "",
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_admin_download)):
    """导出操作日志：筛选条件与列表相同，全部符合条件的记录。"""
    rows = _rows(db, start, end, person, activity, kind)
    titles = _titles(db, rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "操作日志"
    header = ["时间", "学号", "姓名", "身份", "IP", "操作类型", "操作", "说明",
              "方法", "接口", "活动", "结果", "耗时(毫秒)"]
    ws.append(header)
    for r in rows:
        ws.append([r["t"].replace("T", " "), r.get("sid", ""), r.get("name", ""),
                   r.get("identity", ""), r.get("ip", ""),
                   oplog.KIND_LABELS.get(r.get("kind"), ""), r.get("label", ""),
                   r.get("note", ""),
                   r.get("method", ""), r.get("path", ""),
                   titles.get(r.get("activity_id") or "", r.get("activity_id", "")),
                   r.get("status", ""), r.get("ms", "")])
    for col, w in zip("ABCDEFGHIJKLM", (24, 14, 10, 10, 16, 10, 24, 26, 8, 48, 24, 8, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"操作日志_{now().strftime('%Y%m%d%H%M')}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


# ======================= 平台公告 =======================

class AnnouncementBody(BaseModel):
    title: str = Field("", max_length=60)
    body: str = Field("", max_length=2000)
    enabled: bool = False


def _announcement(db: OrmSession) -> Announcement | None:
    return db.get(Announcement, Announcement.CURRENT)


def _admin_out(db: OrmSession, row: Announcement | None) -> dict:
    if row is None:
        return {"title": "", "body": "", "enabled": False,
                "updated_at": None, "updated_by_name": ""}
    editor = db.get(User, row.updated_by) if row.updated_by else None
    return {"title": row.title, "body": row.body, "enabled": row.enabled,
            "updated_at": iso(row.updated_at),
            "updated_by_name": editor.name if editor else ""}


@router.get("/api/announcement")
def current_announcement(db: OrmSession = Depends(db_session),
                         user: User = Depends(current_user)):
    """当前生效的公告；没开就回 null。前端只在登录后第一次进平台时来取。"""
    row = _announcement(db)
    if row is None or not row.enabled:
        return {"announcement": None}
    return {"announcement": {"title": row.title, "body": row.body}}


@router.get("/api/staff/announcement")
def get_announcement(db: OrmSession = Depends(db_session),
                     user: User = Depends(require_admin)):
    return _admin_out(db, _announcement(db))


@router.put("/api/staff/announcement")
def put_announcement(body: AnnouncementBody,
                     db: OrmSession = Depends(db_session),
                     user: User = Depends(require_admin)):
    """保存平台公告。"""
    title, text = body.title.strip(), body.body.strip()
    if body.enabled and not (title and text):
        raise HTTPException(400, "启用公告前请填写标题和正文")
    row = _announcement(db)
    if row is None:
        row = Announcement(id=Announcement.CURRENT)
        db.add(row)
    row.title, row.body, row.enabled = title, text, body.enabled
    row.updated_by, row.updated_at = user.id, now()
    db.commit()
    return _admin_out(db, row)
