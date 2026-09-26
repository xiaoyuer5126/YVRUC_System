"""报名问卷与报名后筛选。

报名问卷两种活动都能用：即选即得的活动在报名弹窗里答题，答案记在报名记录上；
报名后筛选的活动填一次表并勾意向场次，截止后由管理者挑人。

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
from ..integrations import msgcenter
from ..locks import shift_lock
from ..models import (Activity, Application, Attachment, Enrollment, FormQuestion, Position, Shift, User, now, uid)
from ..perm import ensure_activity_manager, is_activity_manager
from ..serializers import application_out
from .materials import EXT_TYPES, MATERIAL_DIR

router = APIRouter(prefix="/api", tags=["screening"])

EDITABLE_FORM_STATUSES = ("draft", "pending", "approved")


def _act(db: OrmSession, activity_id: str, *, screened: bool = True) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    if screened and not act.is_screened():
        raise HTTPException(409, "这不是「报名后筛选」的活动")
    return act


def _questions(db: OrmSession, activity_id: str) -> list[FormQuestion]:
    return (db.query(FormQuestion)
            .filter(FormQuestion.activity_id == activity_id)
            .order_by(FormQuestion.sort).all())


# ======================= 管理者：设计报名问卷 =======================

class QuestionBody(BaseModel):
    id: str = ""                  # 空 = 新题
    kind: str
    title: str = Field(max_length=200)
    options: list[str] = []
    required: bool = True
    show_if: dict = {}            # {"question_id": ..., "values": [...]}
    applies_to_positions: list[str] = []   # 空 = 人人都答


class FormBody(BaseModel):
    questions: list[QuestionBody]


@router.get("/staff/activities/{activity_id}/form")
def staff_form(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, screened=False)
    return {"editable": act.status in EDITABLE_FORM_STATUSES,
            "questions": [q.out() for q in _questions(db, activity_id)],
            # 出题时要挑「这道题问哪些岗位的人」，把岗位按场次给出来
            "shifts": [{"id": g.id, "name": g.name,
                        "positions": [{"id": p.id, "name": p.name}
                                      for p in sorted(g.positions,
                                                      key=lambda x: x.sort)]}
                       for g in sorted(act.main_shifts(), key=lambda x: x.sort)]}


@router.put("/staff/activities/{activity_id}/form")
def save_form(activity_id: str, body: FormBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """整份表一次保存。发布之后锁定——已经有人按这份表填了，改题等于改答案。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, screened=False)
    if act.status not in EDITABLE_FORM_STATUSES:
        raise HTTPException(409, "活动已公开，报名问卷不能再改")
    existing = {q.id: q for q in _questions(db, activity_id)}
    known_positions = {p.id for g in act.shifts for p in g.positions}
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
        scope = [p for p in dict.fromkeys(q.applies_to_positions)
                 if p in known_positions]
        if q.applies_to_positions and not scope:
            raise HTTPException(400, f"「{title}」的适用岗位不属于本活动")
        row = existing.get(q.id) if q.id else None
        if row is None:
            row = FormQuestion(id=uid("q"), activity_id=activity_id, kind=q.kind)
            db.add(row)
        row.kind, row.title, row.options, row.required, row.sort = (
            q.kind, title, options, q.required, i)
        row.show_if = show_if
        row.applies_to_positions = scope
        keep.add(row.id)
        if q.kind in ("single", "multi"):
            earlier[row.id] = options
    for qid, row in existing.items():
        if qid not in keep:
            db.delete(row)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"保存报名问卷（{len(body.questions)} 题）")
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


def visible_questions(questions: list[FormQuestion], answers: dict,
                      positions: list[str] | None = None) -> list[FormQuestion]:
    """按已选岗位与已填答案算出这份表当前该显示哪几道题。

    两层筛：先看岗位——挂了适用岗位的题，只问选了那些岗位的人；再看显示条件。
    题目按 sort 排，条件只指向前面的题，所以一遍扫下来就够了：
    被条件挡掉的题，挂在它下面的题也跟着挡掉。
    """
    chosen = set(positions or [])
    shown, out = set(), []
    for q in questions:
        scope = q.applies_to_positions or []
        if scope and positions is not None and not (chosen & set(scope)):
            continue
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
def student_form(activity_id: str, positions: str = "",
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(current_user)):
    """申请页要的东西。

    带 positions（逗号分隔的岗位 id）时只返回这些岗位该答的题——填表是第二步，
    第一步勾完岗位才知道要问什么。不带就是整份表。
    """
    act = _act(db, activity_id)
    if act.status not in ("published", "closed", "finished", "completed") \
            and not is_activity_manager(db, user, act.id):
        raise HTTPException(404, "活动不存在")
    out = application_out(db, act, user)
    if positions:
        picked = [p for p in positions.split(",") if p]
        out["questions"] = [q.out() for q in visible_questions(
            _questions(db, act.id), {}, picked)]
    return out


def _pending_attachments(db: OrmSession, activity_id: str,
                         user_id: str) -> list[Attachment]:
    """这个人在这个活动里传过的问卷附件。"""
    return (db.query(Attachment)
            .filter(Attachment.owner_type == "application",
                    Attachment.activity_id == activity_id,
                    Attachment.uploaded_by == user_id).all())


def _ensure_upload_quota(db: OrmSession, act: Activity, user: User,
                         size: int) -> None:
    """传了不提交的文件不会自己消失。没有上限，一个人就能把磁盘写满。"""
    rows = _pending_attachments(db, act.id, user.id)
    if len(rows) >= config.MAX_PENDING_ATTACHMENTS:
        raise HTTPException(400, f"本活动最多只能上传 "
                                 f"{config.MAX_PENDING_ATTACHMENTS} 个文件，"
                                 f"请提交申请后再传，或联系负责人")
    if sum(r.size for r in rows) + size > config.MAX_PENDING_ATTACHMENT_BYTES:
        raise HTTPException(
            400, f"本活动的上传总量不能超过 "
                 f"{config.MAX_PENDING_ATTACHMENT_BYTES // (1024 * 1024)}MB")


def drop_unused_attachments(db: OrmSession, activity_id: str, user_id: str,
                            keep: set[str]) -> int:
    """清掉这个人在这个活动里没被答案引用的附件：文件与记录一起删。

    传了又重选、填到一半改主意，都会留下没人用的文件。
    """
    dropped = 0
    for att in _pending_attachments(db, activity_id, user_id):
        if att.id in keep:
            continue
        (MATERIAL_DIR / att.stored_name).unlink(missing_ok=True)
        db.delete(att)
        dropped += 1
    return dropped


@router.post("/activities/{activity_id}/apply/upload")
async def upload_answer_file(activity_id: str, file: UploadFile = File(...),
                             db: OrmSession = Depends(db_session),
                             user: User = Depends(current_user)):
    """报名问卷里的文件题。先传文件拿 id，提交答案时把 id 填进去。"""
    act = _act(db, activity_id, screened=False)
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 {'/'.join(sorted(EXT_TYPES))}）")
    data = await file.read()
    if len(data) > config.MAX_MATERIAL_BYTES:
        raise HTTPException(400, f"文件不能超过 {config.MAX_MATERIAL_BYTES // (1024 * 1024)}MB")
    _ensure_upload_quota(db, act, user, len(data))
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
    # 勾中的意向岗位：跨场次，同一场次也可以勾好几个（意向不占名额）。
    # 还没建岗位的场次只有一个「待分配」占位岗位，勾它等于「这一场我能来」。
    position_ids: list[str]
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
    picked = _wanted_positions(db, act, body.position_ids)

    # 每个意向场次都要过一遍资格、岗位条件与时段冲突：申请就拦，不等录取
    for g, plist in picked.items():
        _check_eligibility(db, user, act, g)
        for p in plist:
            unmet = [] if p.is_placeholder else logic.unmet_requirements(user, p)
            if unmet:
                raise HTTPException(
                    403, f"您不满足「{p.name}」的报名条件：{'；'.join(unmet)}")
        clashes = logic.time_conflicts(db, user, g)
        if clashes:
            raise HTTPException(409, {
                "message": f"「{g.name}」与您已报名的活动时间冲突："
                           f"{logic.conflict_message(clashes)}",
                "conflicts": clashes})

    wanted_ids = [p.id for plist in picked.values() for p in plist]
    answers = validate_answers(db, act, user, body.answers, wanted_ids)
    app = Application(id=uid("app"), activity_id=act.id, user_id=user.id,
                      answers=answers, wanted_positions=wanted_ids,
                      leader_willing=body.leader_willing)
    db.add(app)
    # 传了又重选的文件到此为止：答案定了，没被引用的那些谁也用不上
    drop_unused_attachments(db, act.id, user.id,
                            {str(v) for v in answers.values() if isinstance(v, str)})
    # 一个场次一条意向记录（唯一索引限制），position_id 放他在这一场的首选，
    # 同场次勾的其余岗位记在申请上，录取时管理者从中挑
    for g, plist in picked.items():
        db.add(Enrollment(id=uid("enr"), activity_id=act.id, shift_id=g.id,
                          user_id=user.id, status="applied", joined_at=now(),
                          position_id=plist[0].id, allow_reassign=True))
    db.flush()
    audit_log(db, actor=user, action="enroll", target_type="enrollment",
              target_id=app.id, target_label=user.name, activity_id=act.id,
              summary=f"{user.name}（{user.student_id}）提交报名申请，意向 "
                      + "、".join(f"{g.name}·{'/'.join(p.name for p in plist)}"
                                  for g, plist in picked.items()))
    db.commit()
    return {"ok": True, "status": "applied",
            "message": "申请已提交。报名截止后由负责人筛选，结果会通知您。"}


def _wanted_positions(db: OrmSession, act: Activity,
                      ids: list[str]) -> dict[Shift, list[Position]]:
    """勾中的岗位按场次归拢，保持勾选顺序（每场的第一个就是他的首选）。"""
    out: dict[Shift, list[Position]] = {}
    for pid in dict.fromkeys(ids):
        p = db.get(Position, pid)
        if p is None:
            raise HTTPException(400, "岗位无效")
        g = db.get(Shift, p.shift_id)
        if g is None or g.activity_id != act.id or g.is_attached():
            raise HTTPException(400, "岗位不属于本活动")
        out.setdefault(g, []).append(p)
    if not out:
        raise HTTPException(400, "请至少勾选一个意向岗位")
    return out


def validate_answers(db: OrmSession, act: Activity, user: User, raw: dict,
                     positions: list[str] | None = None) -> dict:
    """逐题校验。岗位或显示条件挡掉的题不校验也不存——它对这个人不存在。"""
    out = {}
    for q in visible_questions(_questions(db, act.id), raw, positions):
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
    drop_unused_attachments(db, act.id, user.id, set())
    db.delete(app)
    db.commit()
    return {"ok": True}


# ======================= 管理者：筛选 =======================

def answers_out(db: OrmSession, questions: list[FormQuestion],
                answers: dict | None) -> list[dict]:
    """一份答案按题目顺序展开给管理端看；文件题带上附件 id 与文件名。"""
    out = []
    for q in questions:
        v = (answers or {}).get(q.id)
        if q.kind == "file" and v:
            att = db.get(Attachment, str(v))
            v = {"attachment_id": att.id, "title": att.title} if att else None
        out.append({"question_id": q.id, "title": q.title,
                    "kind": q.kind, "value": v})
    return out

def _position_name(db: OrmSession, enr: Enrollment) -> str:
    """这条意向/报名落在哪个岗位。占位岗位对志愿者就是「待分配」，照样显示。"""
    p = db.get(Position, enr.position_id) if enr.position_id else None
    return p.name if p else ""


def _accept_position(db: OrmSession, g: Shift, target: Enrollment,
                     override: str) -> Position:
    """录取到哪个岗位：逐人指定 > 整批指定 > 他自己勾的意向岗位 > 占位岗位。"""
    if override:
        position = db.get(Position, override)
        if position is None or position.shift_id != g.id:
            raise HTTPException(400, "指定的岗位不属于这个场次")
        return position
    position = db.get(Position, target.position_id) if target.position_id else None
    if position is None:
        position = g.placeholder()
    if position is None:
        raise HTTPException(409, f"场次「{g.name}」还没有岗位，请先创建岗位")
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
        answers = answers_out(db, questions, app.answers)
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


class Pick(BaseModel):
    shift_id: str = ""
    position_id: str = ""


class DecideBody(BaseModel):
    application_ids: list[str]
    accept: bool
    shift_id: str = ""        # 整批录取到哪一场；空 = 各自的第一意向
    position_id: str = ""     # 整批定岗位；空 = 用他自己勾的意向岗位
    # 逐人指定，优先于上面两项：申请 id → {场次, 岗位}
    picks: dict[str, Pick] = {}


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

    # 录取要占名额：涉及的场次整批先锁上，判名额、落库、留痕同一个事务提交。
    # 从前锁只包住「判名额 + 改状态」，提交在放锁之后，中间别人看到的还是旧
    # 人数；场次锁现在随事务走，正好一直持有到下面那次 commit。
    # 一次锁多个场次按 id 排序（shift_lock 里做），两位管理者同时批量录取时
    # 加锁顺序一致，不会互相等成死锁。
    # 不录取也要锁：它不动名额，但会和别人的录取改同一批记录——一位录取、
    # 另一位同时不录取，不锁的话后提交的那个会把申请状态盖成相反的结论
    shift_ids = [sid for (sid,) in (
        db.query(Enrollment.shift_id)
        .filter(Enrollment.activity_id == act.id,
                Enrollment.user_id.in_([a.user_id for a in pending]),
                Enrollment.status == "applied").distinct())]
    db.commit()          # 还连接再排锁，理由同 enroll
    with shift_lock(db, *shift_ids):
        # 锁外筛出来的「待处理」可能已经旧了：另一位管理者刚处理过其中几条。
        # 重读后只处理仍在申请中的；一条都不剩就明说，别让人以为操作成功了
        for app in pending:
            db.refresh(app)
        handled = [a for a in pending if a.status != "applied"]
        pending = [a for a in pending if a.status == "applied"]
        if not pending:
            raise HTTPException(409, "所选申请已被其他管理者处理，请刷新")
        if handled and body.accept:
            # 录取是整批判名额的，混着已处理的人继续会让结果和管理者看到的不一致
            names = "、".join(db.get(User, a.user_id).name for a in handled)
            raise HTTPException(409, f"{names} 的申请已被其他管理者处理，请刷新后重新选择")
        done = []
        for app in pending:
            u = db.get(User, app.user_id)
            rows = (db.query(Enrollment)
                    .filter(Enrollment.activity_id == act.id,
                            Enrollment.user_id == app.user_id,
                            Enrollment.status == "applied").all())
            if body.accept:
                pick = body.picks.get(app.id) or Pick()
                want_shift = pick.shift_id or body.shift_id
                target = None
                if want_shift:
                    target = next((e for e in rows if e.shift_id == want_shift), None)
                    if target is None:
                        raise HTTPException(400, f"{u.name} 没有申请这个场次")
                else:
                    target = rows[0] if rows else None
                if target is None:
                    raise HTTPException(409, f"{u.name} 的申请没有可用的场次")
                g = db.get(Shift, target.shift_id)
                position = _accept_position(db, g, target,
                                            pick.position_id or body.position_id)
                # 条件不满足照样录得进去：筛选本来就是人的判断。但要留痕，
                # 名单上也跟着标出来，别让「怎么排了个不符合条件的人」成为悬案
                unmet = logic.unmet_requirements(u, position)
                if not logic.has_room(db, g, position):
                    raise HTTPException(
                        409, f"「{g.name}·{position.name}」名额已满"
                             f"（{position.quota} 人），请先调整名额或更换岗位")
                target.status = "enrolled"
                target.leader_willing = app.leader_willing
                target.position_id = position.id
                target.assigned_at = now()
                target.assigned_by = user.id
                target.assign_forced = bool(unmet)
                for e in rows:
                    if e.id != target.id:
                        e.status, e.cancel_type = "cancelled", "not_chosen"
                app.status = "accepted"
                app.decided_by, app.decided_at = user.id, now()
                db.flush()
                logic.sync_attached(db, target)
                msgcenter.send_user(db, u, "application_accepted",
                                data={"activity_id": act.id},
                                activity=act.title, shift=g.name, time=g.span_text(),
                                detail=("岗位：" + position.name
                                        if not position.is_placeholder
                                        else "具体岗位将于稍后公布"))
                done.append(f"{u.name}→{g.name}·{position.name}"
                            + (f"（不满足：{'；'.join(unmet)}）" if unmet else ""))
            else:
                for e in rows:
                    e.status, e.cancel_type = "cancelled", "rejected"
                app.status = "rejected"
                app.decided_by, app.decided_at = user.id, now()
                msgcenter.send_user(db, u, "application_rejected",
                                data={"activity_id": act.id}, activity=act.title)
                done.append(u.name)
        audit_log(db, actor=user, action="assign", target_type="activity",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=("录取 " if body.accept else "未录取 ") + "、".join(done))
        db.commit()
    return {"ok": True, "count": len(done)}
