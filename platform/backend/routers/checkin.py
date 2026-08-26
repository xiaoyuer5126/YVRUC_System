"""签到/签退：任务信息、扫码核验（学生）、二维码 SVG。

同一个签到码：第一次扫 = 签到，第二次扫 = 签退。
签退宽限：场次结束后 2 小时内可自行签退；超时未签退由系统按场次结束时间
兜底（见 staff_ops.compute_hours 的 effective_checkout）。

对接说明：演示模式由本平台生成 token 并核验；接入学校现有签到系统后，
create/verify 换到 checkin_gateway 适配器的真实实现，路由层不变。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import current_user
from ..integrations import checkin_gateway
from ..models import (Activity, CheckinRecord, CheckinTask, Enrollment,
                      PositionGroup, Station, User, now, uid)
from ..qrcodes import qr_svg

router = APIRouter(prefix="/api/checkin", tags=["checkin"])

# 场次结束后可自行签退的宽限时长
CHECKOUT_GRACE = timedelta(hours=config.CHECKOUT_GRACE_HOURS)


def _load_task(db: OrmSession, token: str) -> tuple[CheckinTask, Activity, PositionGroup]:
    task = db.query(CheckinTask).filter(CheckinTask.token == token.upper()).first()
    if task is None:
        raise HTTPException(404, "签到码无效")
    act = db.get(Activity, task.activity_id)
    g = db.get(PositionGroup, task.group_id)
    return task, act, g


@router.get("/task/{token}")
def task_info(token: str, db: OrmSession = Depends(db_session),
              user: User = Depends(current_user)):
    task, act, g = _load_task(db, token)
    station = db.get(Station, task.station_id) if task.station_id else None
    t = now()
    rec = (db.query(CheckinRecord)
           .filter(CheckinRecord.user_id == user.id,
                   CheckinRecord.group_id == g.id).first())
    state = "active"
    if not task.active:
        state = "closed"
    elif task.valid_from and t < task.valid_from:
        state = "early"
    elif task.valid_to and t > task.valid_to:
        state = "expired"
    mine = (db.query(Enrollment)
            .filter(Enrollment.group_id == g.id, Enrollment.user_id == user.id,
                    Enrollment.status == "confirmed").first())
    return {
        "activity_title": act.title, "group_name": g.name,
        "group_time": g.span_text(),
        "station": ({"name": station.name, "time_text": station.time_text(),
                     "location": station.location} if station else None),
        "mode": task.mode, "state": state,
        "enrolled": mine is not None,
        "checked_in": rec is not None,
        "checked_out": bool(rec and rec.checkout_at),
        "my_checkin_at": rec.checkin_at.isoformat(timespec="minutes") if rec else None,
        "my_checkout_at": (rec.checkout_at.isoformat(timespec="minutes")
                           if rec and rec.checkout_at else None),
        # 签退宽限截止：场次结束 + 2 小时
        "checkout_deadline": ((g.end_time + CHECKOUT_GRACE)
                              .isoformat(timespec="minutes")),
        "server_time": now().isoformat(timespec="seconds"),
    }


@router.post("/task/{token}")
def do_checkin(token: str, db: OrmSession = Depends(db_session),
               user: User = Depends(current_user)):
    """同一签到码：无记录 → 签到；已签未退 → 签退；已签已退 → 拒绝重复。"""
    task, act, g = _load_task(db, token)
    if not checkin_gateway.verify_scan(task.token):
        raise HTTPException(400, "签到码核验失败")
    t = now()

    if not task.active:
        raise HTTPException(409, "该签到任务已关闭")
    if task.valid_from and t < task.valid_from:
        raise HTTPException(409, f"签到未开始（{task.valid_from:%H:%M} 起）")
    if task.valid_to and t > task.valid_to:
        raise HTTPException(409, f"签到已截止（{task.valid_to:%H:%M} 止）")

    # 工位模式只用于按摊位分别核验；工位无独立时间，可签时段 = 场次时段
    station = db.get(Station, task.station_id) if task.station_id else None

    # 必须是该组的正式成员
    enr = (db.query(Enrollment)
           .filter(Enrollment.group_id == g.id, Enrollment.user_id == user.id,
                   Enrollment.status == "confirmed").first())
    if enr is None:
        raise HTTPException(403, f"您不是「{act.title}·{g.name}」的确认志愿者")

    rec = (db.query(CheckinRecord)
           .filter(CheckinRecord.user_id == user.id,
                   CheckinRecord.group_id == g.id).first())

    if rec is None:
        # ---- 签到 ----
        rec = CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                            group_id=g.id,
                            station_id=(enr.station_id
                                        or (station.id if station else None)),
                            user_id=user.id, method="scan", checkin_at=t)
        db.add(rec)
        db.commit()
        return {"ok": True, "action": "checkin",
                "message": f"签到成功，{user.name} 已到岗！",
                "time": t.isoformat(timespec="minutes"),
                "location": (station.location if station and station.location
                             else act.location)}

    if rec.checkout_at:
        raise HTTPException(409, "您已完成本场次的签到与签退，请勿重复操作")

    # ---- 签退 ----
    rec.checkout_at = t
    rec.checkout_method = "scan"
    db.commit()
    return {"ok": True, "action": "checkout",
            "message": f"签退成功，{user.name} 本次服务时长将按签到签退计算。",
            "time": t.isoformat(timespec="minutes")}


@router.get("/qr.svg", response_class=Response)
def qr(token: str, base: str = ""):
    """签到码二维码。内容为落地页 URL，手机扫码直达。"""
    data = f"{base}/#/checkin/{token}" if base else f"/#/checkin/{token}"
    svg = qr_svg(data)
    return Response(content=svg, media_type="image/svg+xml",
                    headers={"Cache-Control": "no-store"})
