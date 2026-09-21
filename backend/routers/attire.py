"""志愿服装的领取与归还。

一场活动两个二维码：领取码、归还码。领取和归还是两个物理动作，发生在活动
的两头、常常是两张桌子，各贴各的码谁也不会扫错——所以这里不做签到那种
「同一个码扫两次」。

台账一人一条（activity_id + user_id 唯一）：领了记时间，还了再记一次。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user, require_manager
from ..models import (Activity, Enrollment, Position, User, AttireRecord,
                      AttireTask, iso, now, uid)
from ..perm import ensure_activity_manager
from ..qrcodes import page_qr

router = APIRouter(prefix="/api/attire", tags=["attire"])

ACTIONS = ("take", "return")


def _token() -> str:
    """六位好念的码，去掉容易看错的 0/O、1/I。"""
    import random
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(random.choice(alphabet) for _ in range(6))


def _need_attire_user_ids(db: OrmSession, activity_id: str) -> set[str]:
    """本活动里岗位服装为「统一发放」的正式志愿者；自备的不进台账。"""
    rows = (db.query(Enrollment.user_id)
            .join(Position, Enrollment.position_id == Position.id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.status == "enrolled",
                    Position.attire == "issued").distinct().all())
    return {r[0] for r in rows}


# ======================= 学生扫码 =======================

@router.get("/task/{token}")
def task_info(token: str, db: OrmSession = Depends(db_session),
              user: User = Depends(current_user)):
    task = db.query(AttireTask).filter(AttireTask.token == token.upper()).first()
    if task is None:
        raise HTTPException(404, "服装码无效")
    act = db.get(Activity, task.activity_id)
    rec = (db.query(AttireRecord)
           .filter(AttireRecord.activity_id == task.activity_id,
                   AttireRecord.user_id == user.id).first())
    return {
        "activity_title": act.title if act else "",
        "action": task.action, "action_label": task.action_label(),
        "active": task.active,
        "need_attire": user.id in _need_attire_user_ids(db, task.activity_id),
        "taken_at": iso(rec.taken_at) if rec else None,
        "returned_at": iso(rec.returned_at) if rec and rec.returned_at else None,
    }


@router.post("/scan/{token}")
def scan(token: str, db: OrmSession = Depends(db_session),
         user: User = Depends(current_user)):
    """扫码领取／归还。码本身已经指明了动作，不需要再传任何东西。"""
    task = db.query(AttireTask).filter(AttireTask.token == token.upper()).first()
    if task is None:
        raise HTTPException(404, "服装码无效")
    if not task.active:
        raise HTTPException(409, "该服装码已关闭")
    if user.id not in _need_attire_user_ids(db, task.activity_id):
        raise HTTPException(403, "您在本活动的服装不由平台统一发放")

    rec = (db.query(AttireRecord)
           .filter(AttireRecord.activity_id == task.activity_id,
                   AttireRecord.user_id == user.id).first())
    if task.action == "take":
        if rec is not None and rec.returned_at is None:
            raise HTTPException(409, "您已经领过服装了")
        if rec is not None:       # 还过又来领：同一场活动允许再领一次
            rec.taken_at = now()
            rec.returned_at = None
            rec.returned_by = None
            rec.returned_method = ""
        else:
            rec = AttireRecord(id=uid("atr"), activity_id=task.activity_id,
                             user_id=user.id, taken_method="scan")
            db.add(rec)
        db.commit()
        return {"ok": True, "action": "take", "message": "服装已领取，请按要求归还"}

    if rec is None:
        raise HTTPException(409, "没有您的领取记录，无法归还")
    if rec.returned_at is not None:
        raise HTTPException(409, "您已经归还过了")
    rec.returned_at = now()
    rec.returned_method = "scan"
    db.commit()
    return {"ok": True, "action": "return", "message": "服装已归还，感谢参与"}


@router.get("/qr.svg", response_class=Response)
def qr(request: Request, token: str, base: str = ""):
    """领取／归还码。内容是落地页的完整网址，手机扫码直达。"""
    return page_qr(request, f"/attire/{token}", base)


# ======================= 管理端 =======================

staff = APIRouter(prefix="/api/staff", tags=["attire-staff"])


@staff.post("/activities/{activity_id}/attire-tasks")
def ensure_tasks(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """生成本活动的领取码与归还码（幂等：已有就直接返回）。"""
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    out = {}
    for action in ACTIONS:
        task = (db.query(AttireTask)
                .filter(AttireTask.activity_id == activity_id,
                        AttireTask.action == action).first())
        if task is None:
            task = AttireTask(id=uid("atk"), activity_id=activity_id,
                            action=action, token=_token(), created_by=user.id)
            db.add(task)
            db.flush()
            audit_log(db, actor=user, action="checkin_task", target_type="attire",
                      target_id=task.id, target_label=act.title,
                      activity_id=activity_id,
                      summary=f"生成服装{task.action_label()}码 {task.token}")
        out[action] = {"token": task.token, "active": task.active}
    db.commit()
    return out


@staff.get("/activities/{activity_id}/attire")
def attire_board(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """服装台账：应领 / 已领 / 已还 / 未还，以及每个人的状态。"""
    ensure_activity_manager(db, user, activity_id)
    need = _need_attire_user_ids(db, activity_id)
    recs = {r.user_id: r for r in db.query(AttireRecord)
            .filter(AttireRecord.activity_id == activity_id).all()}
    tasks = {t.action: {"token": t.token, "active": t.active}
             for t in db.query(AttireTask)
             .filter(AttireTask.activity_id == activity_id).all()}

    items = []
    for uid_ in need | set(recs):
        u = db.get(User, uid_)
        if u is None:
            continue
        r = recs.get(uid_)
        enr = (db.query(Enrollment, Position)
               .join(Position, Enrollment.position_id == Position.id)
               .filter(Enrollment.activity_id == activity_id,
                       Enrollment.user_id == uid_,
                       Enrollment.status == "enrolled").first())
        items.append({
            "name": u.name, "student_id": u.student_id, "college": u.college,
            "position": enr[1].name if enr else "",
            "expected": uid_ in need,
            "taken": r is not None and r.returned_at is None,
            "returned": bool(r and r.returned_at),
            "taken_at": iso(r.taken_at) if r else None,
            "returned_at": iso(r.returned_at) if r and r.returned_at else None,
        })
    items.sort(key=lambda x: (x["returned"], not x["taken"], x["name"]))
    taken = sum(1 for x in items if x["taken"])
    returned = sum(1 for x in items if x["returned"])
    # 未领取：岗位统一发放服装、但既没在领也没还过的人
    not_taken = sum(1 for x in items
                    if x["expected"] and not x["taken"] and not x["returned"])
    return {"tasks": tasks, "expected": len(need), "taken": taken,
            "returned": returned, "outstanding": taken,
            "not_taken": not_taken, "items": items}


class AttireOpBody(BaseModel):
    action: str                     # take / return / cancel_take / cancel_return
    student_ids: list[str]


@staff.post("/activities/{activity_id}/attire/batch")
def attire_batch(activity_id: str, body: AttireOpBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """手动勾选领取／归还／撤销。现场没网、码贴反了都得有人工兜底。"""
    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    if body.action not in ("take", "return", "cancel_take", "cancel_return"):
        raise HTTPException(400, "未知操作")
    sids = [s.strip() for s in body.student_ids if s.strip()]
    if not sids:
        raise HTTPException(400, "未选择志愿者")

    done, skipped = [], []
    for sid in sids:
        u = db.query(User).filter(User.student_id == sid).first()
        if u is None:
            skipped.append(f"{sid}（学号不存在）")
            continue
        rec = (db.query(AttireRecord)
               .filter(AttireRecord.activity_id == activity_id,
                       AttireRecord.user_id == u.id).first())
        if body.action == "take":
            if rec is not None and rec.returned_at is None:
                skipped.append(f"{u.name}（已领取）")
                continue
            if rec is None:
                rec = AttireRecord(id=uid("atr"), activity_id=activity_id,
                                 user_id=u.id, taken_method="manual",
                                 taken_by=user.id)
                db.add(rec)
            else:
                rec.taken_at = now()
                rec.returned_at = None
                rec.returned_method = ""
        elif body.action == "return":
            if rec is None:
                skipped.append(f"{u.name}（没有领取记录）")
                continue
            if rec.returned_at is not None:
                skipped.append(f"{u.name}（已归还）")
                continue
            rec.returned_at = now()
            rec.returned_method = "manual"
            rec.returned_by = user.id
        elif body.action == "cancel_take":
            if rec is None:
                skipped.append(f"{u.name}（没有领取记录）")
                continue
            db.delete(rec)
        else:                       # cancel_return
            if rec is None or rec.returned_at is None:
                skipped.append(f"{u.name}（没有归还记录）")
                continue
            rec.returned_at = None
            rec.returned_by = None
            rec.returned_method = ""
        done.append(u.name)

    LABELS = {"take": "手动登记领取", "return": "手动登记归还",
              "cancel_take": "撤销领取", "cancel_return": "撤销归还"}
    audit_log(db, actor=user, action="manual_checkin", target_type="attire",
              target_id=activity_id, target_label=act.title if act else "",
              activity_id=activity_id,
              summary=f"服装{LABELS[body.action]} {len(done)} 人"
                      + (f"；跳过：{'、'.join(skipped[:5])}" if skipped else ""))
    db.commit()
    return {"ok": True, "done": len(done), "skipped": skipped}


class AttireRemindBody(BaseModel):
    action: str = "return"      # take 催领取 / return 催归还
    need_ack: bool = False


@staff.post("/activities/{activity_id}/attire/remind")
def attire_remind(activity_id: str, body: AttireRemindBody | None = None,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """一键催领取或催归还。

    催领取：岗位统一发放服装、但还没领的人——活动当天没穿统一服装，现场认不出是志愿者。
    催归还：领了还没还的人。
    """
    from ..integrations import msgcenter

    ensure_activity_manager(db, user, activity_id)
    act = db.get(Activity, activity_id)
    body = body or AttireRemindBody()
    if body.action not in ACTIONS:
        raise HTTPException(400, "未知操作")

    if body.action == "return":
        targets = [db.get(User, r.user_id) for r in
                   db.query(AttireRecord)
                   .filter(AttireRecord.activity_id == activity_id,
                           AttireRecord.returned_at.is_(None)).all()]
        empty_msg = "没有未归还的服装"
        title = f"请归还志愿服装｜{act.title}"
        text = (f"您在「{act.title}」领取的志愿服装尚未归还，"
                f"请尽快到指定地点扫「归还码」交还。")
    else:
        taken = {r.user_id for r in db.query(AttireRecord)
                 .filter(AttireRecord.activity_id == activity_id,
                         AttireRecord.returned_at.is_(None)).all()}
        targets = [db.get(User, uid_)
                   for uid_ in _need_attire_user_ids(db, activity_id)
                   if uid_ not in taken]
        empty_msg = "统一发放服装的人都已经领了"
        title = f"请领取志愿服装｜{act.title}"
        text = (f"您在「{act.title}」的岗位由平台统一发放志愿服装，尚无领取记录。\n"
                f"请到指定地点扫「领取码」领取，并按要求归还。")

    targets = [u for u in targets if u is not None]
    if not targets:
        raise HTTPException(400, empty_msg)
    batch_id = uid("bat") if body.need_ack else None
    for u in targets:
        msgcenter.send_user(db, u, "custom", data={"activity_id": activity_id},
                        title=title, content=text,
                        need_ack=body.need_ack, batch_id=batch_id)
    label = "领取" if body.action == "take" else "归还"
    audit_log(db, actor=user, action="update", target_type="attire",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"提醒 {len(targets)} 位志愿者{label}服装"
                      + ("，要求签收" if body.need_ack else ""))
    db.commit()
    return {"ok": True, "count": len(targets), "batch_id": batch_id}
