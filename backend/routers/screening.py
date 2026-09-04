"""报名后筛选：管理者设计报名表，志愿者填一次表并勾意向场次，截止后由管理者
挑人。

场次照旧分两种：「报名即选岗位」的场次要连意向岗位一起勾，录取时直接落到他选的
那个岗位；「截止后统一分配」的场次只勾场次，录取后落在占位岗位上等统一分配。

申请不限人数；申请时就按意向场次拦时段冲突（跟即选即得一个口径）；未录取只发
一条留余地的通知，不留任何影响以后报名的记录。
"""
from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as OrmSession

from .. import config, enroll_logic as logic, scoring
from ..audit import log as audit_log
from ..database import db_session
from ..deps import current_user, require_manager
from ..integrations import wecom
from ..locks import shift_lock
from ..models import (Activity, Application, Attachment, Enrollment, FormQuestion, Position, Shift, User, now, uid)
from ..perm import ensure_activity_manager, is_activity_manager
from ..serializers import application_out
from .materials import EXT_TYPES, MATERIAL_DIR

router = APIRouter(prefix="/api", tags=["screening"])

EDITABLE_FORM_STATUSES = ("draft", "pending", "approved")


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if not act.is_screened():
        raise HTTPException(409, "这不是「报名后筛选」的活动")
    return act


def _questions(db: OrmSession, activity_id: str) -> list[FormQuestion]:
    return (db.query(FormQuestion)
            .filter(FormQuestion.activity_id == activity_id)
            .order_by(FormQuestion.sort).all())


# ======================= 管理者：设计报名表 =======================

class QuestionBody(BaseModel):
    id: str = ""                  # 空 = 新题
    kind: str
    title: str = Field(max_length=200)
    options: list[str] = []
    required: bool = True
    show_if: dict = {}            # {"question_id": ..., "values": [...]}


class FormBody(BaseModel):
    questions: list[QuestionBody]


@router.get("/staff/activities/{activity_id}/form")
def staff_form(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    return {"editable": act.status in EDITABLE_FORM_STATUSES,
            "questions": [q.out() for q in _questions(db, activity_id)]}


@router.put("/staff/activities/{activity_id}/form")
def save_form(activity_id: str, body: FormBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """整份表一次保存。发布之后锁定——已经有人按这份表填了，改题等于改答案。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status not in EDITABLE_FORM_STATUSES:
        raise HTTPException(409, "活动已公开，报名表不能再改")
    existing = {q.id: q for q in _questions(db, activity_id)}
    keep: set[str] = set()
    # 显示条件只能挂在前面的选择题上：往后指会绕成环，答一道题跳一道题就说不清了
    earlier: dict[str, list[str]] = {}
    for i, q in enumerate(body.questions):
        if q.kind not in FormQuestion.KINDS:
            raise HTTPException(400, f"第 {i + 1} 题的题型不合法")
        title = q.title.strip()
        if not title:
            raise HTTPException(400, f"第 {i + 1} 题没有题目")
        options = [o.strip() for o in q.options if o.strip()]
        if q.kind in ("single", "multi") and len(options) < 2:
            raise HTTPException(400, f"「{title}」至少要有两个选项")
        show_if = _clean_show_if(q.show_if, earlier, title)
        row = existing.get(q.id) if q.id else None
        if row is None:
            row = FormQuestion(id=uid("q"), activity_id=activity_id, kind=q.kind)
            db.add(row)
        row.kind, row.title, row.options, row.required, row.sort = (
            q.kind, title, options, q.required, i)
        row.show_if = show_if
        keep.add(row.id)
        if q.kind in ("single", "multi"):
            earlier[row.id] = options
    for qid, row in existing.items():
        if qid not in keep:
            db.delete(row)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"保存报名表（{len(body.questions)} 题）")
    db.commit()
    return {"ok": True, "questions": [q.out() for q in _questions(db, activity_id)]}


def _clean_show_if(raw: dict, earlier: dict[str, list[str]], title: str) -> dict:
    """校验一道题的显示条件：必须挂在**前面**某道单选/多选题的某几个选项上。"""
    qid = str((raw or {}).get("question_id") or "").strip()
    if not qid:
        return {}
    if qid not in earlier:
        raise HTTPException(400, f"「{title}」的显示条件只能挂在前面的单选/多选题上")
    values = [str(v) for v in (raw.get("values") or []) if str(v) in earlier[qid]]
    if not values:
        raise HTTPException(400, f"请为「{title}」的显示条件选择至少一个选项")
    return {"question_id": qid, "values": values}


def visible_questions(questions: list[FormQuestion],
                      answers: dict) -> list[FormQuestion]:
    """按已填答案算出这份表当前该显示哪几道题。

    题目按 sort 排，条件只指向前面的题，所以一遍扫下来就够了：
    被条件挡掉的题，挂在它下面的题也跟着挡掉。
    """
    shown, out = set(), []
    for q in questions:
        cond = q.show_if or {}
        qid = cond.get("question_id")
        if qid:
            if qid not in shown:
                continue
            answer = answers.get(qid)
            picked = answer if isinstance(answer, list) else [answer]
            if not any(v in (cond.get("values") or []) for v in picked):
                continue
        shown.add(q.id)
        out.append(q)
    return out


# ======================= 志愿者：填表申请 =======================

@router.get("/activities/{activity_id}/form")
def student_form(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(current_user)):
    act = _act(db, activity_id)
    if act.status not in ("published", "closed", "finished") \
            and not is_activity_manager(db, user, act.id):
        raise HTTPException(404, "活动不存在")
    return application_out(db, act, user)


@router.post("/activities/{activity_id}/apply/upload")
async def upload_answer_file(activity_id: str, file: UploadFile = File(...),
                             db: OrmSession = Depends(db_session),
                             user: User = Depends(current_user)):
    """报名表里的文件题。先传文件拿 id，提交答案时把 id 填进去。"""
    act = _act(db, activity_id)
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 {'/'.join(sorted(EXT_TYPES))}）")
    data = await file.read()
    if len(data) > config.MAX_MATERIAL_BYTES:
        raise HTTPException(400, f"文件不能超过 {config.MAX_MATERIAL_BYTES // (1024 * 1024)}MB")
    MATERIAL_DIR.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid4().hex}.{ext}"
    (MATERIAL_DIR / stored).write_bytes(data)
    row = Attachment(id=uid("att"), owner_type="application", owner_id=user.id,
                     activity_id=act.id, kind="file",
                     title=(file.filename or stored)[:160], stored_name=stored,
                     size=len(data), uploaded_by=user.id)
    db.add(row)
    db.commit()
    return {"id": row.id, "title": row.title, "size": row.size}


class ApplyBody(BaseModel):
    shift_ids: list[str]
    # 「报名即选岗位」的场次要连岗位一起勾：场次 id → 岗位 id
    positions: dict[str, str] = {}
    answers: dict = {}
    leader_willing: bool = False


@router.post("/activities/{activity_id}/apply")
def apply(activity_id: str, body: ApplyBody,
          db: OrmSession = Depends(db_session),
          user: User = Depends(current_user)):
    from .activities import _check_eligibility

    act = _act(db, activity_id)
    if (db.query(Application)
            .filter(Application.activity_id == act.id,
                    Application.user_id == user.id).first()):
        raise HTTPException(409, "您已经申请过本活动")
    shifts = [db.get(Shift, i) for i in dict.fromkeys(body.shift_ids)]
    if not shifts or any(g is None or g.activity_id != act.id or g.is_attached()
                         for g in shifts):
        raise HTTPException(400, "请至少勾选一个可参加的场次")

    # 每个意向场次都要过一遍资格、岗位与时段冲突：申请就拦，不等录取
    wanted: list[tuple[Shift, Position | None]] = []
    for g in shifts:
        _check_eligibility(db, user, act, g)
        wanted.append((g, _wanted_position(db, user, g, body.positions or {})))
        clashes = logic.time_conflicts(db, user, g)
        if clashes:
            raise HTTPException(409, {
                "message": f"「{g.name}」与您已报名的活动时间冲突："
                           f"{logic.conflict_message(clashes)}",
                "conflicts": clashes})

    answers = _validate_answers(db, act, user, body.answers)
    app = Application(id=uid("app"), activity_id=act.id, user_id=user.id,
                      answers=answers, leader_willing=body.leader_willing)
    db.add(app)
    for g, position in wanted:
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=user.id, status="applied", joined_at=now(),
                          position_id=position.id if position else None,
                          allow_reassign=True))
    db.flush()
    audit_log(db, actor=user, action="enroll", target_type="enrollment",
              target_id=app.id, target_label=user.name, activity_id=act.id,
              summary=f"{user.name}（{user.student_id}）提交报名申请，"
                      f"意向 {'、'.join(g.name for g in shifts)}")
    db.commit()
    return {"ok": True, "status": "applied",
            "message": "申请已提交。报名截止后由负责老师筛选，结果会通知您。"}


def _wanted_position(db: OrmSession, user: User, g: Shift,
                     picks: dict) -> Position | None:
    """「报名即选岗位」的场次要连岗位一起勾；统一分配的场次不用挑。"""
    if g.needs_assign():
        return None
    position = db.get(Position, picks.get(g.id) or "")
    if position is None or position.shift_id != g.id or position.is_placeholder:
        raise HTTPException(400, f"请为「{g.name}」选择一个意向岗位")
    unmet = logic.unmet_requirements(user, position)
    if unmet:
        raise HTTPException(
            403, f"您不满足「{position.name}」的报名条件：{'；'.join(unmet)}")
    return position


def _validate_answers(db: OrmSession, act: Activity, user: User,
                      raw: dict) -> dict:
    """逐题校验。被显示条件挡掉的题不校验也不存——它对这个人不存在。"""
    out = {}
    for q in visible_questions(_questions(db, act.id), raw):
        v = raw.get(q.id)
        empty = v is None or v == "" or v == []
        if q.required and empty:
            raise HTTPException(400, f"「{q.title}」为必填")
        if empty:
            continue
        if q.kind == "single":
            if v not in (q.options or []):
                raise HTTPException(400, f"「{q.title}」的选项无效")
        elif q.kind == "multi":
            if not isinstance(v, list) or any(x not in (q.options or []) for x in v):
                raise HTTPException(400, f"「{q.title}」的选项无效")
        elif q.kind == "text":
            v = str(v)[:2000]
        elif q.kind == "file":
            att = db.get(Attachment, str(v))
            if att is None or att.owner_type != "application" \
                    or att.uploaded_by != user.id or att.activity_id != act.id:
                raise HTTPException(400, f"「{q.title}」的文件无效，请重新上传")
        out[q.id] = v
    return out


@router.delete("/activities/{activity_id}/apply")
def withdraw(activity_id: str, db: OrmSession = Depends(db_session),
             user: User = Depends(current_user)):
    """撤回申请：把申请与意向记录整条删掉。

    撤回等于没申请过——报名期内还能重新填一份，改答案、改意向场次都走这条路
    （一个活动只填一次，不给改，那就只能先撤回再重填）。录取之后不是申请了，
    走退选。
    """
    act = _act(db, activity_id)
    app = (db.query(Application)
           .filter(Application.activity_id == act.id,
                   Application.user_id == user.id).first())
    if app is None:
        raise HTTPException(409, "没有可撤回的申请")
    if app.status != "applied":
        raise HTTPException(
            409, "申请已经处理过了" + ("；退出请到「我的报名」退选"
                                      if app.status == "accepted" else ""))
    (db.query(Enrollment)
     .filter(Enrollment.activity_id == act.id, Enrollment.user_id == user.id,
             Enrollment.status == "applied")
     .delete(synchronize_session=False))
    audit_log(db, actor=user, action="cancel", target_type="enrollment",
              target_id=app.id, target_label=user.name, activity_id=act.id,
              summary=f"{user.name}（{user.student_id}）撤回了报名申请")
    db.delete(app)
    db.commit()
    return {"ok": True}


# ======================= 管理者：筛选 =======================

def _position_name(db: OrmSession, enr: Enrollment) -> str:
    """这条意向/报名落在哪个岗位。占位岗位对志愿者就是「待分配」，照样显示。"""
    p = db.get(Position, enr.position_id) if enr.position_id else None
    return p.name if p else ""


def _accept_position(db: OrmSession, g: Shift, target: Enrollment,
                     override: str) -> Position:
    """录取到哪个岗位：管理者指定的 > 他自己勾的意向岗位 > 场次的占位岗位。"""
    if override:
        position = db.get(Position, override)
        if position is None or position.shift_id != g.id:
            raise HTTPException(400, "指定的岗位不属于这个场次")
        return position
    position = db.get(Position, target.position_id) if target.position_id else None
    if position is None:
        position = g.placeholder()
    if position is None:
        raise HTTPException(409, f"场次「{g.name}」还没有岗位，请先建好岗位再录取")
    return position


@router.get("/staff/activities/{activity_id}/applications")
def list_applications(activity_id: str, db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    questions = _questions(db, activity_id)
    apps = (db.query(Application)
            .filter(Application.activity_id == activity_id)
            .order_by(Application.created_at).all())
    shifts = {g.id: g for g in act.main_shifts()}
    out = []
    for app in apps:
        u = db.get(User, app.user_id)
        rows = (db.query(Enrollment)
                .filter(Enrollment.activity_id == activity_id,
                        Enrollment.user_id == app.user_id,
                        Enrollment.shift_id.in_(list(shifts))).all())
        wanted = [{"shift_id": e.shift_id, "shift_name": shifts[e.shift_id].name,
                   "position_name": _position_name(db, e),
                   "status": e.status, "cancel_type": e.cancel_type}
                  for e in rows if e.shift_id in shifts]
        answers = []
        for q in questions:
            v = (app.answers or {}).get(q.id)
            if q.kind == "file" and v:
                att = db.get(Attachment, str(v))
                v = {"attachment_id": att.id, "title": att.title} if att else None
            answers.append({"question_id": q.id, "title": q.title,
                            "kind": q.kind, "value": v})
        out.append({
            "id": app.id, "status": app.status, "status_label": app.status_label(),
            "created_at": app.created_at.isoformat(timespec="minutes"),
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "campus": u.campus, "stage": u.stage,
                     "phone": u.phone, **scoring.brief(db, u.id)},
            "wanted": wanted, "answers": answers,
            "leader_willing": app.leader_willing,
        })
    return {"questions": [q.out() for q in questions], "applications": out,
            "shifts": [{"id": g.id, "name": g.name, "time_text": g.span_text(),
                        "mode": g.mode,
                        "positions": [{"id": p.id, "name": p.name,
                                       "quota": p.quota}
                                      for p in g.real_positions()],
                        "capacity": g.total_capacity(),
                        "enrolled": logic.shift_enrolled_count(db, g.id)}
                       for g in sorted(shifts.values(), key=lambda x: x.sort)]}


class DecideBody(BaseModel):
    application_ids: list[str]
    accept: bool
    shift_id: str = ""        # 录取到哪一场；空 = 各自的第一意向
    position_id: str = ""     # 一并定岗位；空 = 用他自己勾的意向岗位


@router.post("/staff/activities/{activity_id}/applications/decide")
def decide(activity_id: str, body: DecideBody, bg: BackgroundTasks,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """批量录取或不录取。

    录取：「报名即选岗位」的场次落到他勾的那个岗位（也可以在这里统一改成
    别的岗位），「截止后统一分配」的场次落到占位岗位上等一键分配。名额不够
    就拦下，让管理者先扩人数上限。其余意向场次作废。
    不录取：意向场次全部作废，通知一句留余地的话；档案里不留痕迹。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status not in ("published", "closed"):
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能筛选")
    apps = [db.get(Application, i) for i in body.application_ids]
    if not apps or any(a is None or a.activity_id != act.id for a in apps):
        raise HTTPException(400, "申请记录无效")
    pending = [a for a in apps if a.status == "applied"]
    if not pending:
        raise HTTPException(409, "所选申请都已经处理过了")

    done = []
    for app in pending:
        u = db.get(User, app.user_id)
        rows = (db.query(Enrollment)
                .filter(Enrollment.activity_id == act.id,
                        Enrollment.user_id == app.user_id,
                        Enrollment.status == "applied").all())
        if body.accept:
            target = None
            if body.shift_id:
                target = next((e for e in rows if e.shift_id == body.shift_id), None)
                if target is None:
                    raise HTTPException(400, f"{u.name} 没有申请这个场次")
            else:
                target = rows[0] if rows else None
            if target is None:
                raise HTTPException(409, f"{u.name} 的申请没有可用的场次")
            g = db.get(Shift, target.shift_id)
            position = _accept_position(db, g, target, body.position_id)
            with shift_lock(g.id):
                if not logic.has_room(db, g, position):
                    raise HTTPException(
                        409, f"「{g.name}·{position.name}」名额已满"
                             f"（{position.quota} 人），请先调大人数上限"
                             f"或换一个岗位")
                target.status = "enrolled"
                target.leader_willing = app.leader_willing
                target.position_id = position.id
                target.assigned_at = now()
                target.assigned_by = user.id
                for e in rows:
                    if e.id != target.id:
                        e.status, e.cancel_type = "cancelled", "not_chosen"
                app.status = "accepted"
                app.decided_by, app.decided_at = user.id, now()
                db.flush()
                logic.sync_attached(db, target)
            wecom.send_user(db, u, "application_accepted",
                            data={"activity_id": act.id},
                            activity=act.title, shift=g.name, time=g.span_text(),
                            detail=("岗位：" + position.name
                                    if not position.is_placeholder
                                    else "具体岗位将于稍后公布"))
            done.append(f"{u.name}→{g.name}·{position.name}")
        else:
            for e in rows:
                e.status, e.cancel_type = "cancelled", "rejected"
            app.status = "rejected"
            app.decided_by, app.decided_at = user.id, now()
            wecom.send_user(db, u, "application_rejected",
                            data={"activity_id": act.id}, activity=act.title)
            done.append(u.name)
    audit_log(db, actor=user, action="assign", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=("录取 " if body.accept else "未录取 ") + "、".join(done))
    db.commit()
    return {"ok": True, "count": len(done)}
