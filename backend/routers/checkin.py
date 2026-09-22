"""签到/签退：任务信息、扫码核验（学生）、定位核验（学生）、二维码 SVG。

同一个签到码：第一次扫 = 签到，第二次扫 = 签退——但刚签完就再扫一遍不算数，
按「重复操作」挡回去（见 _apply_checkin 里的最短间隔）。
签退宽限：场次结束后 2 小时内可自行签退；超时未签退由系统按场次结束时间
兜底（见 staff_ops.compute_hours 的 effective_checkout）。

**两类签到码都从这个入口进**：
  · 动态码——管理端每 30 秒换一张，只活在 Redis 里（见 locks.py）。默认用这个，
    因为固定码拍张照发群里就能代签；
  · 固定码——建任务时生成、存在 CheckinTask.token 里，可以打印出来贴到现场。
学生端拿到的就是串字符，不区分，由这里认。

**定位签到是另一条入口**（/enrollments/{id}）：那种岗位现场没人能出示二维码，
学生点一下按钮、平台比对他的坐标与管理者量下的锚点。判定规则和扫码共用
_apply_checkin，不另写一份。
"""
from __future__ import annotations

import math
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from .. import config
from ..database import db_session
from ..deps import current_user
from ..locks import redeem_checkin_code
from ..models import (Activity, CheckinGeo, CheckinRecord, CheckinTask,
                      Enrollment, Shift, Position, User, now, uid)
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


def resolve_task(db: OrmSession, shift_id: str, position_id: str | None
                 ) -> CheckinTask | None:
    """这一场次、这个岗位该认哪张签到任务：优先本岗位那张码，没有就退回统一的那张。

    「我的报名」那边亮不亮按钮、这边落不落记录，两边必须指向同一个任务——
    各判各的，就会出现「按钮亮的是定位签到、提交时算的却是扫码那张」。
    """
    tasks = (db.query(CheckinTask)
             .filter(CheckinTask.shift_id == shift_id,
                     CheckinTask.active.is_(True))
             .order_by(CheckinTask.created_at.desc()).all())
    if not tasks:
        return None
    return (next((t for t in tasks if t.position_id == position_id), None)
            or next((t for t in tasks if not t.position_id), None) or tasks[0])


def geo_anchor(db: OrmSession, task: CheckinTask) -> CheckinGeo | None:
    """这个任务是不是定位签到：有锚点就是（见 CheckinGeo 的说明）。"""
    return (db.query(CheckinGeo)
            .filter(CheckinGeo.task_id == task.id).first())


def _distance_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两个坐标之间的地面距离（米），Haversine。

    不用等距圆柱投影之类的近似：几百米这个尺度上两者的差远小于手机定位
    本身的误差（几十米起步），但 Haversine 写起来一样短。
    """
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _apply_checkin(db: OrmSession, task: CheckinTask, act: Activity, g: Shift,
                   user: User, *, method: str, note: str = "") -> dict:
    """签到/签退的判定只写这一份。

    扫码和定位是两条入口，但「什么时候算签到、什么时候算签退、什么时候该
    挡回去」是同一件事。各写一遍早晚会分叉——比如 10 分钟那道保护窗只加在
    扫码那一侧，定位签到就悄悄成了「当场签退」的后门。
    """
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
                            user_id=user.id, method=method, checkin_at=t)
        db.add(rec)
        db.commit()
        return {"ok": True, "action": "checkin",
                "message": f"签到成功，{user.name} 已到岗。" + note,
                "time": t.isoformat(timespec="minutes"),
                "location": (position.location if position and position.location
                             else act.location)}

    if rec.checkout_at:
        raise HTTPException(409, "您已完成本场次的签到与签退，请勿重复操作")

    # 刚签完又扫一遍，多半是「以为没成功」或相机连读了两下。这条要是当成签退，
    # 人就当场被签退了、时长按 0 算——而他自己看不出发生了什么。所以挡回去。
    gap = t - rec.checkin_at
    floor = timedelta(minutes=config.CHECKOUT_MIN_GAP_MINUTES)
    if gap < floor:
        left = math.ceil((floor - gap).total_seconds() / 60)
        raise HTTPException(
            409, f"您 {int(gap.total_seconds() // 60)} 分钟前刚签到，无需重复扫码；"
                 f"{left} 分钟后才能签退")

    # ---- 签退 ----
    rec.checkout_at = t
    rec.checkout_method = method
    db.commit()
    return {"ok": True, "action": "checkout",
            "message": f"签退成功，{user.name} 本次服务时长将按签到签退计算。" + note,
            "time": t.isoformat(timespec="minutes")}


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
        # 定位签到的场次现场没有码可扫。扫开了这个页面（老二维码打印件还在），
        # 得告诉人该怎么办，而不是等他点了按钮再报错
        "method": "geo" if geo_anchor(db, task) else "scan",
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
    """扫码签到：同一签到码，无记录 → 签到；已签未退 → 签退；已签已退 → 拒绝重复。

    但「已签未退」里分两种：刚签完又扫一遍（手滑）和散场回来签退。前者挡回去，
    见下面 CHECKOUT_MIN_GAP_MINUTES。
    """
    task, act, g = _load_task(db, token)
    # 这个任务已经改成定位签到了：码要是还能用，定位那道「人在不在场」的
    # 判定当场作废——谁都会挑松的那条路走（老二维码打印件、截图都还在传）。
    # 判的是**扫到的这个任务**有没有锚点，不是这一场有没有：同一场次里
    # 「东门那三个用定位、其余人扫统一码」是正当安排，那种统一码不该被误伤。
    if geo_anchor(db, task):
        raise HTTPException(409, "本场次是定位签到，请在「我的报名」里点「签到」")
    return _apply_checkin(db, task, act, g, user, method="scan")


class GeoBody(BaseModel):
    lat: float
    lng: float
    accuracy: float = 0        # 手机报的误差半径（米）；拿不到时传 0


@router.post("/enrollments/{enrollment_id}")
def geo_checkin(enrollment_id: str, body: GeoBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(current_user)):
    """定位签到：志愿者到场点一下，平台拿他的坐标和锚点比距离。

    为什么不复用 /task/{code}：那个入口认的是「码」，而定位签到恰恰是**没有
    码**的场景。要让学生点一下就签，就得把码发给每个报名的人——动态码防代签
    那套当场作废。所以这条入口不带凭据，改用坐标，锚点只有管理者到现场才能量。

    它比扫码弱（手机定位能伪造），定位精度也看环境，所以：
      · 手机报的误差比半径还大就直接拒——那样的定位证明不了他在场；
      · 只对自己名下的报名记录生效，且必须落在这个场次的岗位上。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None or enr.user_id != user.id:
        raise HTTPException(404, "报名记录不存在")
    if enr.status != "enrolled":
        raise HTTPException(403, "您不是本场次的正式志愿者")
    g = db.get(Shift, enr.shift_id)
    act = db.get(Activity, enr.activity_id)
    if g is None or act is None:
        raise HTTPException(404, "场次不存在")

    task = resolve_task(db, enr.shift_id, enr.position_id)
    if task is None:
        raise HTTPException(409, "本场次还没有签到任务，请联系负责人")
    geo = geo_anchor(db, task)
    if geo is None:
        # 关键的一道门：扫码签到的场次绝不能从这条入口进来。不挡住的话，
        # 谁都能拿两个坐标把「不用扫码」变成默认选项。
        raise HTTPException(409, "本场次是扫码签到，请扫描现场的签到二维码")

    if body.accuracy and body.accuracy > geo.radius_m:
        raise HTTPException(
            409, f"手机定位误差约 {round(body.accuracy)} 米，比签到范围"
                 f"（{geo.radius_m} 米）还大，证明不了您在现场。"
                 f"请到空旷处或窗边重试；实在取不到定位，请联系负责人手动补签")

    dist = _distance_m(body.lat, body.lng, geo.lat, geo.lng)
    if dist > geo.radius_m:
        raise HTTPException(
            409, f"您距签到点约 {round(dist)} 米，超出签到范围"
                 f"（{geo.radius_m} 米）。请到岗位上再签，"
                 f"或联系负责人核对")

    d = _apply_checkin(db, task, act, g, user, method="geo",
                       note=f"（距签到点约 {round(dist)} 米）")
    d["distance_m"] = round(dist)
    return d


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
    task = _task_by_code(db, value)
    # 定位签到的任务不画码：画出来也没人能签（do_checkin 会挡回去），
    # 而一张扫了必定失败的码比没有码更坏事——现场会以为是自己手机的问题
    if task is None or geo_anchor(db, task):
        raise HTTPException(404, "签到码无效")
    return page_qr(request, f"/checkin/{value}", base)
