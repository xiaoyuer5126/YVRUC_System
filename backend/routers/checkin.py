"""签到/签退：任务信息、扫码核验（学生）、二维码 SVG。

同一个签到码：第一次扫 = 签到，第二次扫 = 签退。
签退宽限：场次结束后 2 小时内可自行签退；超时未签退由系统按场次结束时间
兜底（见 staff_ops.compute_hours 的 effective_checkout）。

**两类签到码都从这个入口进**：
  · 动态码——管理端每 30 秒换一张，只活在 Redis 里（见 locks.py）。默认用这个，
    因为固定码拍张照发群里就能代签；
  · 固定码——建任务时生成、存在 CheckinTask.token 里，可以打印出来贴到现场。
学生端拿到的就是串字符，不区分，由这里认。
"""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import current_user
from ..integrations import checkin_gateway
from ..locks import redeem_checkin_code
from ..models import (Activity, CheckinRecord, CheckinTask, Enrollment,
                      Shift, Position, User, now, uid)
from ..qrcodes import page_qr

router = APIRouter(prefix="/api/checkin", tags=["checkin"])

# 场次结束后可自行签退的宽限时长
CHECKOUT_GRACE = timedelta(hours=config.CHECKOUT_GRACE_HOURS)


def _task_by_code(db: OrmSession, code: str) -> CheckinTask | None:
    """签到码 → 任务。先查动态码：它有寿命，晚一步就没了。"""
    task_id = redeem_checkin_code(code)
    if task_id:
        return db.get(CheckinTask, task_id)
    return db.query(CheckinTask).filter(CheckinTask.token == code.upper()).first()


def _load_task(db: OrmSession, code: str) -> tuple[CheckinTask, Activity, Shift]:
    task = _task_by_code(db, code)
    if task is None:
        raise HTTPException(404, "签到码无效")
    act = db.get(Activity, task.activity_id)
    g = db.get(Shift, task.shift_id)
    return task, act, g


@router.get("/task/{token}")
def task_info(token: str, db: OrmSession = Depends(db_session),
              user: User = Depends(current_user)):
    task, act, g = _load_task(db, token)
    position = db.get(Position, task.position_id) if task.position_id else None
    t = now()
    rec = (db.query(CheckinRecord)
           .filter(CheckinRecord.user_id == user.id,
                   CheckinRecord.shift_id == g.id).first())
    state = "active"
    if not task.active:
        state = "closed"
    elif task.valid_from and t < task.valid_from:
        state = "early"
    elif task.valid_to and t > task.valid_to:
        state = "expired"
    mine = (db.query(Enrollment)
            .filter(Enrollment.shift_id == g.id, Enrollment.user_id == user.id,
                    Enrollment.status == "enrolled").first())
    return {
        "activity_title": act.title, "shift_name": g.name,
        "group_time": g.span_text(),
        "position": ({"name": position.name, "time_text": position.time_text(),
                     "location": position.location} if position else None),
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

    # 岗位模式只用于按摊位分别核验；岗位无独立时间，可签时段 = 场次时段
    position = db.get(Position, task.position_id) if task.position_id else None

    # 必须是该场次的正式成员
    enr = (db.query(Enrollment)
           .filter(Enrollment.shift_id == g.id, Enrollment.user_id == user.id,
                   Enrollment.status == "enrolled").first())
    if enr is None:
        raise HTTPException(403, f"您不是「{act.title}·{g.name}」的志愿者")

    rec = (db.query(CheckinRecord)
           .filter(CheckinRecord.user_id == user.id,
                   CheckinRecord.shift_id == g.id).first())

    if rec is None:
        # ---- 签到 ----
        rec = CheckinRecord(id=uid("ckr"), task_id=task.id, activity_id=act.id,
                            shift_id=g.id,
                            position_id=(enr.position_id
                                        or (position.id if position else None)),
                            user_id=user.id, method="scan", checkin_at=t)
        db.add(rec)
        db.commit()
        return {"ok": True, "action": "checkin",
                "message": f"签到成功，{user.name} 已到岗。",
                "time": t.isoformat(timespec="minutes"),
                "location": (position.location if position and position.location
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
def qr(request: Request, token: str = "", code: str = "", base: str = "",
       db: OrmSession = Depends(db_session)):
    """签到码二维码。内容是签到落地页的完整网址，手机扫码直达。

    两种码都**必须在册才画**。这个口子不需要登录，收下任意一串字符，就等于
    让任何人拿平台的域名生成一张指向任意路径的码贴出去，扫的人无从分辨。
    所以在册判定用的是和扫码同一个入口：动态码要 Redis 里活着，固定码要
    库里存在。
    """
    value = (code or token).strip().upper()
    if _task_by_code(db, value) is None:
        raise HTTPException(404, "签到码无效")
    return page_qr(request, f"/checkin/{value}", base)
