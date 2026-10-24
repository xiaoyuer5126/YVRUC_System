"""平台级的三样东西：操作日志的查看与导出、通知记录的查看与导出、平台弹窗公告。

  · 操作日志 → 仅管理员（会长团不行）。记录本身在文件里，见 oplog.py。
  · 通知记录 → 仅管理员。从 notifications 表读，每条通知是谁发的、发给了谁。
  · 平台公告 → 管理员编辑；所有登录用户读当前生效的那一条。公告的修改
    不另写审计日志：改公告的请求本身进操作日志。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from io import BytesIO

from fastapi import APIRouter, Depends, HTTPException, Query
from openpyxl import Workbook
from pydantic import BaseModel, Field
from sqlalchemy import func, or_
from sqlalchemy.orm import Session as OrmSession, aliased

from .. import config, downloads, oplog
from ..database import db_session
from ..deps import current_user, require_admin, require_admin_download
from ..exporter import disarm_formulas
from ..integrations import msgcenter
from ..models import (Activity, Announcement, Notification, User, iso,
                      like_pattern, now, parse_local_dt)

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


def _filters(db: OrmSession, start: str, end: str, activity: str,
             kind: str) -> tuple[datetime, datetime, set[str] | None]:
    """校验操作类型与时间段，把活动名解析成活动编号集合。"""
    if kind and kind not in oplog.KIND_LABELS:
        raise HTTPException(400, "未知操作类型")
    lo, hi = _window(start, end)
    return lo, hi, _activity_ids(db, activity)


def _titles(db: OrmSession, ids: set[str]) -> dict[str, str]:
    if not ids:
        return {}
    return dict(db.query(Activity.id, Activity.title)
                .filter(Activity.id.in_(ids)).all())


def _out(row: dict, titles: dict[str, str]) -> dict:
    return {**row, "kind_label": oplog.KIND_LABELS.get(row.get("kind"), ""),
            "activity_title": titles.get(row.get("activity_id") or "", "")}


def _excel_row(row: dict, titles: dict[str, str]) -> list:
    """一条记录摊平成表格的一行，顺序与表头一致。"""
    return [row["t"].replace("T", " "), row.get("sid", ""), row.get("name", ""),
            row.get("identity", ""), row.get("ip", ""),
            oplog.KIND_LABELS.get(row.get("kind"), ""), row.get("label", ""),
            row.get("note", ""),
            row.get("method", ""), row.get("path", ""),
            titles.get(row.get("activity_id") or "", row.get("activity_id", "")),
            row.get("status", ""), row.get("ms", "")]


@router.get("/api/staff/oplogs")
def list_oplogs(start: str = "", end: str = "", person: str = "",
                activity: str = "", kind: str = "",
                page: int = Query(1, ge=1), size: int = Query(20, ge=1, le=100),
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_admin)):
    """操作日志列表：按人（学号或姓名）、时间段、活动、操作类型筛选，新的在前。"""
    lo, hi, activity_ids = _filters(db, start, end, activity, kind)
    rows, total = oplog.page_rows(lo, hi, page=page, size=size, person=person,
                                  activity_ids=activity_ids, kind=kind)
    titles = _titles(db, {r["activity_id"] for r in rows if r.get("activity_id")})
    return {"total": total, "items": [_out(r, titles) for r in rows],
            "kinds": oplog.KIND_LABELS,
            "max_days": config.OPLOG_QUERY_MAX_DAYS}


@router.get("/api/staff/oplogs.xlsx")
def export_oplogs(start: str = "", end: str = "", person: str = "",
                  activity: str = "", kind: str = "",
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_admin_download)):
    """导出操作日志：筛选条件与列表相同，全部符合条件的记录，新的在前。"""
    lo, hi, activity_ids = _filters(db, start, end, activity, kind)
    # 一趟读完：超上限当场回绝，不必为一份导不出去的表格建工作簿。工作簿本身
    # 就把每一格放在内存里，先攒下这批记录不多占什么
    rows: list[dict] = []
    for r in oplog.iter_rows(lo, hi, person=person, activity_ids=activity_ids,
                             kind=kind):
        if len(rows) >= config.OPLOG_EXPORT_MAX_ROWS:
            raise HTTPException(
                400, f"这段时间的日志超过 {config.OPLOG_EXPORT_MAX_ROWS} 条，"
                     "请缩短时间段再导")
        rows.append(r)
    titles = _titles(db, {r["activity_id"] for r in rows if r.get("activity_id")})
    wb = Workbook()
    ws = wb.active
    ws.title = "操作日志"
    header = ["时间", "学号", "姓名", "身份", "IP", "操作类型", "操作", "说明",
              "方法", "接口", "活动", "结果", "耗时(毫秒)"]
    ws.append(header)
    for r in reversed(rows):
        ws.append(_excel_row(r, titles))
    for col, w in zip("ABCDEFGHIJKLM", (24, 14, 10, 10, 16, 10, 24, 26, 8, 48, 24, 8, 10)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"操作日志_{now().strftime('%Y%m%d%H%M')}.xlsx"
    return downloads.stream_response(buf, filename, downloads.XLSX)


# ======================= 通知记录 =======================

# 推送状态：site 是演示模式下只落库的「仅站内」，其余按是否送达分成功、失败
NOTIFY_STATUS_LABELS = {"ok": "成功", "failed": "失败", "site": "仅站内"}


def _notify_status(row: Notification) -> str:
    """一条通知的推送状态。"""
    if row.channel == "site":
        return "site"
    return "ok" if row.delivered else "failed"


def _notify_query(db: OrmSession, start: str, end: str, person: str,
                  activity: str, template: str, status: str):
    """通知记录的查询：收件人、触发人、筛选条件都在这儿定。

    收件人和触发人都用 User 联查，触发人外联——定时任务发的通知没有触发人，
    但记录本身要留着。
    """
    if status and status not in NOTIFY_STATUS_LABELS:
        raise HTTPException(400, "未知推送状态")
    lo, hi = _window(start, end)
    recipient = aliased(User)
    trigger = aliased(User)
    q = (db.query(Notification, recipient.student_id, recipient.name,
                  trigger.student_id, trigger.name)
         .outerjoin(recipient, recipient.id == Notification.user_id)
         .outerjoin(trigger, trigger.id == Notification.triggered_by)
         .filter(Notification.created_at >= lo, Notification.created_at <= hi))
    person = person.strip()
    if person:
        q = q.filter(or_(recipient.student_id == person,
                         recipient.name.like(like_pattern(person), escape="\\")))
    activity_ids = _activity_ids(db, activity)
    if activity_ids is not None:
        q = q.filter(Notification.data["activity_id"].as_string().in_(activity_ids))
    template = template.strip()
    if template:
        q = q.filter(Notification.template_key == template)
    if status == "site":
        q = q.filter(Notification.channel == "site")
    elif status == "ok":
        q = q.filter(Notification.channel != "site",
                     Notification.delivered.is_(True))
    elif status == "failed":
        q = q.filter(Notification.channel != "site",
                     Notification.delivered.is_(False))
    return q, lo, hi


def _notify_titles(db: OrmSession, rows) -> dict[str, str]:
    """这批通知涉及的活动标题。data 里的 activity_id 可能是数字，统一按字符串查。"""
    ids = {str((row[0].data or {}).get("activity_id") or "") for row in rows}
    return _titles(db, ids - {""})


def _notify_out(row: Notification, rec_sid: str | None, rec_name: str | None,
                trg_sid: str | None, trg_name: str | None,
                titles: dict[str, str]) -> dict:
    """一条通知摊成前端要的字段。"""
    activity_id = str((row.data or {}).get("activity_id") or "")
    status = _notify_status(row)
    return {
        "id": row.id, "created_at": iso(row.created_at),
        "recipient_sid": rec_sid or "", "recipient_name": rec_name or "",
        "template_key": row.template_key,
        "template_label": msgcenter.TEMPLATE_LABELS.get(row.template_key,
                                                        row.template_key),
        "title": row.title, "content": row.content,
        "activity_id": activity_id, "activity_title": titles.get(activity_id, ""),
        "status": status, "status_label": NOTIFY_STATUS_LABELS[status],
        "read": row.read, "need_ack": row.need_ack,
        "acked_at": iso(row.acked_at), "batch_id": row.batch_id,
        # 没有触发人就是定时任务等系统行为
        "trigger_sid": trg_sid or "", "trigger_name": trg_name or "系统",
    }


def _notify_excel_row(row: Notification, rec_sid: str | None,
                      rec_name: str | None, trg_sid: str | None,
                      trg_name: str | None, titles: dict[str, str]) -> list:
    """一条通知摊平成表格的一行，顺序与表头一致。"""
    activity_id = str((row.data or {}).get("activity_id") or "")
    status = _notify_status(row)
    if not row.need_ack:
        ack = "不需签收"
    elif row.acked_at:
        ack = "已签收 " + row.acked_at.strftime("%Y-%m-%d %H:%M")
    else:
        ack = "未签收"
    return [row.created_at.strftime("%Y-%m-%d %H:%M:%S"), rec_sid or "",
            rec_name or "",
            msgcenter.TEMPLATE_LABELS.get(row.template_key, row.template_key),
            row.title, row.content, titles.get(activity_id, activity_id),
            NOTIFY_STATUS_LABELS[status], "是" if row.read else "否", ack,
            trg_name or "系统"]


@router.get("/api/staff/notifications")
def list_notifications(start: str = "", end: str = "", person: str = "",
                       activity: str = "", template: str = "", status: str = "",
                       page: int = Query(1, ge=1),
                       size: int = Query(20, ge=1, le=100),
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_admin)):
    """通知记录列表：按收件人、时间段、活动、类型、推送状态筛选，新的在前。"""
    q, _lo, _hi = _notify_query(db, start, end, person, activity, template,
                                status)
    total = q.count()
    rows = (q.order_by(Notification.created_at.desc(), Notification.id.desc())
            .offset((page - 1) * size).limit(size).all())
    titles = _notify_titles(db, rows)
    # 今日数字不受筛选影响：页顶看的是「今天整体发了多少、失败多少」
    day_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    today_total = (db.query(func.count(Notification.id))
                   .filter(Notification.created_at >= day_start).scalar() or 0)
    today_failed = (db.query(func.count(Notification.id))
                    .filter(Notification.created_at >= day_start,
                            Notification.channel != "site",
                            Notification.delivered.is_(False)).scalar() or 0)
    return {"total": total,
            "items": [_notify_out(*row, titles) for row in rows],
            "templates": msgcenter.TEMPLATE_LABELS,
            "today": {"total": today_total, "failed": today_failed},
            "max_days": config.OPLOG_QUERY_MAX_DAYS}


@router.get("/api/staff/notifications.xlsx")
def export_notifications(start: str = "", end: str = "", person: str = "",
                         activity: str = "", template: str = "",
                         status: str = "",
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_admin_download)):
    """导出通知记录：筛选条件与列表相同，全部符合条件的记录，新的在前。"""
    q, _lo, _hi = _notify_query(db, start, end, person, activity, template,
                                status)
    rows = (q.order_by(Notification.created_at.desc(), Notification.id.desc())
            .limit(config.OPLOG_EXPORT_MAX_ROWS + 1).all())
    if len(rows) > config.OPLOG_EXPORT_MAX_ROWS:
        raise HTTPException(
            400, f"这段时间的通知超过 {config.OPLOG_EXPORT_MAX_ROWS} 条，"
                 "请缩短时间段再导")
    titles = _notify_titles(db, rows)
    wb = Workbook()
    ws = wb.active
    ws.title = "通知记录"
    header = ["时间", "收件人学号", "收件人姓名", "类型", "标题", "正文", "活动",
              "推送状态", "已读", "签收", "触发人"]
    ws.append(header)
    for row in rows:
        ws.append(_notify_excel_row(*row, titles))
    for col, w in zip("ABCDEFGHIJK", (20, 14, 12, 18, 30, 50, 24, 10, 8, 20, 12)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    filename = f"通知记录_{now().strftime('%Y%m%d%H%M')}.xlsx"
    return downloads.stream_response(buf, filename, downloads.XLSX)


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
