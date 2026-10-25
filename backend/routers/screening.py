"""报名问卷与报名后筛选。

报名问卷两种活动都能用：即选即得的活动在报名弹窗里答题，答案记在报名记录上；
报名后筛选的活动填一次表并勾意向场次，截止后由管理者挑人。

场次照旧分两种：「报名即选岗位」的场次要连意向岗位一起勾，录取时直接落到他选的
那个岗位；「截止后统一分配」的场次只勾场次，录取后落在占位岗位上等统一分配。

申请不限人数；申请时就按意向场次提示时段冲突（跟即选即得一个口径，确认后照样
能交）；未录取只发一条留余地的通知，不留任何影响以后报名的记录。
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
from ..locks import shift_lock, user_lock
from ..models import (Activity, Application, Attachment, Enrollment, FormQuestion, Position, Shift, User, now, uid)
from ..perm import ensure_activity_manager, is_activity_manager
from ..serializers import application_out, reg_started
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
    # 新题在编辑页上的临时编号：后面的题可以拿它当显示条件的来源，保存时换成正式 id
    ref: str = ""
    kind: str
    title: str = Field(max_length=200)
    options: list[str] = []
    required: bool = True
    show_if: dict = {}            # {"question_id": ..., "values": [...]}
    applies_to_positions: list[str] = []   # 空 = 人人都答
    applies_to_campuses: list[str] = []    # 空 = 不限校区
    applies_to_stages: list[str] = []      # 空 = 不限学段


class FormBody(BaseModel):
    questions: list[QuestionBody]


@router.get("/staff/activities/{activity_id}/form")
def staff_form(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, screened=False)
    return {"editable": act.status in EDITABLE_FORM_STATUSES,
            "questions": [q.out() for q in _questions(db, activity_id)],
            # 出题时要挑「这道题问哪些岗位的人」，把岗位按场次给出来。
            # 只列真实岗位：占位岗位（「待分配」）是 assign 场次里装「还没安排的
            # 人」的桶，开放选岗、切回「报名即选岗位」时会被删掉，题目挂在它上面
            # 就成了悬空的 id（见 save_form）
            "shifts": [{"id": g.id, "name": g.name,
                        "positions": [{"id": p.id, "name": p.name}
                                      for p in sorted(g.positions,
                                                      key=lambda x: x.sort)
                                      if not p.is_placeholder]}
                       for g in sorted(act.main_shifts(), key=lambda x: x.sort)],
            # 「适用校区」「适用学段」的选项，和个人信息里能填的是同一份
            "campuses": config.CAMPUSES, "stages": config.STAGES}


@router.put("/staff/activities/{activity_id}/form")
def save_form(activity_id: str, body: FormBody,
              db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """整份表一次保存。发布之后锁定——已经有人按这份表填了，改题等于改答案。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, screened=False)
    if act.is_record():
        raise HTTPException(409, "补记录活动不收报名，不设报名问卷")
    if act.status not in EDITABLE_FORM_STATUSES:
        raise HTTPException(409, "活动已公开，报名问卷不能再改")
    existing = {q.id: q for q in _questions(db, activity_id)}
    # 本题能挂的岗位：本活动现在还有的岗位，按 id 取
    positions = {p.id: p for g in act.shifts for p in g.positions}
    keep: set[str] = set()
    # 显示条件只能挂在前面的选择题上：往后指会绕成环，答一道题跳一道题就说不清了
    earlier: dict[str, list[str]] = {}
    refs: dict[str, str] = {}     # 新题的临时编号 → 正式 id
    for i, q in enumerate(body.questions):
        if q.kind not in FormQuestion.KINDS:
            raise HTTPException(400, f"第 {i + 1} 题的题型不合法")
        title = q.title.strip()
        if not title:
            raise HTTPException(400, f"第 {i + 1} 题没有题目")
        options = [o.strip() for o in q.options if o.strip()]
        if q.kind in ("single", "multi") and len(options) < 2:
            raise HTTPException(400, f"「{title}」至少要有两个选项")
        raw_if = dict(q.show_if or {})
        if raw_if.get("question_id") in refs:
            raw_if["question_id"] = refs[raw_if["question_id"]]
        show_if = _clean_show_if(raw_if, earlier, title)
        scope = _clean_scope(positions, q.applies_to_positions, title)
        campuses = _clean_choices(q.applies_to_campuses, config.CAMPUSES,
                                  title, "校区")
        stages = _clean_choices(q.applies_to_stages, config.STAGES, title, "学段")
        row = existing.get(q.id) if q.id else None
        if row is None:
            row = FormQuestion(id=uid("q"), activity_id=activity_id, kind=q.kind)
            db.add(row)
            if q.ref:
                refs[q.ref] = row.id
        row.kind, row.title, row.options, row.required, row.sort = (
            q.kind, title, options, q.required, i)
        row.show_if = show_if
        row.applies_to_positions = scope
        row.applies_to_campuses = campuses
        row.applies_to_stages = stages
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


def _clean_scope(positions: dict[str, Position], raw: list[str],
                 title: str) -> list[str]:
    """校验一道题的适用岗位，返回去重后的岗位 id。空 = 人人都答。

    两种坏引用分开报：岗位已经被删（id 悬空）、挂到了占位岗位上。后者是
    assign 场次里装「还没安排的人」的桶，开放选岗、切回「报名即选岗位」时
    会连同它一起被删——挂上去等于给这道题埋一个悬空的 id，只收真实岗位。
    """
    out: list[str] = []
    for pid in dict.fromkeys(raw):
        p = positions.get(pid)
        if p is None:
            raise HTTPException(400, f"「{title}」的适用岗位已被删除，请重新选")
        if p.is_placeholder:
            raise HTTPException(
                400, f"「{title}」的适用岗位不能选「{p.name}」："
                     f"「{p.name}」不是真岗位，请选具体岗位")
        out.append(pid)
    return out


def _clean_choices(raw: list[str], allowed: list[str], title: str,
                   what: str) -> list[str]:
    """校验一道题的适用校区或学段，返回去重后的取值。空 = 不限。"""
    out = list(dict.fromkeys(raw))
    bad = [v for v in out if v not in allowed]
    if bad:
        raise HTTPException(400, f"「{title}」的适用{what}不认识：{'、'.join(bad)}")
    return out


def scoped_questions(questions: list[FormQuestion], user: User,
                     positions: list[str] | None = None) -> list[FormQuestion]:
    """按这个人的校区、学段与已选岗位筛出他该看的题，不看显示条件。

    填表这一步把题先发给页面，页面按作答实时的答案决定露出哪几道（见
    web/src/components/QuestionFields.vue）。服务端这时候还没有答案，照着
    显示条件筛会把带条件的题连同挂在它下面的题一并筛掉——选了触发选项也
    再也出不来，题上写了必填就卡死在「为必填」上，志愿者连题都看不见。

    positions 是这个人当前的岗位，None 表示不按岗位筛。校区、学段总要筛
    （见 FormQuestion.fits）。
    """
    questions = [q for q in questions if q.fits(user)]
    if positions is None:
        return questions
    chosen = set(positions)
    # 适用岗位是管理者圈定的一小撮岗位，与已选岗位没有交集就不问这个人。
    # 圈里的岗位被删掉（占位岗位被清理、岗位被删）时，这道题的 scope 和谁都
    # 不相交，于是对所有人都问不到——这道题就此失效，而不是退化成人人都答：
    # 范围是管理者圈的，圈里的岗位没了不能替他放大到全体。这种题在出题界面
    # 一保存就被拦下要求重选（见 _clean_scope）。
    return [q for q in questions
            if not q.applies_to_positions or chosen & set(q.applies_to_positions)]


def visible_questions(questions: list[FormQuestion], answers: dict, user: User,
                      positions: list[str] | None = None) -> list[FormQuestion]:
    """按这个人的校区学段、已选岗位与已填答案算出这份表当前该显示哪几道题。

    三层筛：先看校区、学段（FormQuestion.fits）；再看岗位——挂了适用岗位的题，
    只问选了那些岗位的人；最后看显示条件。positions 是这个人当前的岗位，None
    表示不按岗位筛。题目按 sort 排，条件只指向前面的题，所以一遍扫下来就够了：
    被挡掉的题（不论挡在哪一层），挂在它下面的题也跟着挡掉。

    提交时按这份名单校验与存答案：页面按同一套规则露出题，两边说的是同一批题。
    发题给页面用 scoped_questions——那一步还没有答案，筛不了显示条件。
    """
    chosen = set(positions or [])
    shown, out = set(), []
    for q in questions:
        if not q.fits(user):
            continue
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
    第一步勾完岗位才知道要问什么。这一步只按岗位筛，带显示条件的题照样发下去，
    由页面按作答实时决定露不露（见 scoped_questions）。不带就是整份表。
    """
    act = _act(db, activity_id)
    if act.status not in ("published", "closed", "finished", "vb_pending",
                          "completed") \
            and not is_activity_manager(db, user, act.id):
        raise HTTPException(404, "活动不存在")
    out = application_out(db, act, user)
    if positions:
        picked = [p for p in positions.split(",") if p]
        out["questions"] = [q.out() for q in scoped_questions(
            _questions(db, act.id), user, picked)]
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
    # 传文件是为报名服务的，报名关着就不该还能往里丢东西：草稿、待审批的
    # 活动连详情都看不着，报名截止或还没开始的活动收下文件也没人用得上。
    # 与报名同一口径：只看活动状态和报名窗（不看资料是否齐全——那是报名
    # 那一步的拦截，先把文件收下来再让他去补资料也是通的）
    if act.status != "published":
        raise HTTPException(409, f"活动当前为「{act.label_status()}」，不在报名期")
    if user.user_type == "teacher":
        raise HTTPException(403, "老师类型用户不参与志愿报名")
    if not reg_started(act.reg_start, now()):
        raise HTTPException(409, f"报名尚未开始（{act.reg_start:%m-%d %H:%M} 起）")
    if (db.query(Application)
            .filter(Application.activity_id == act.id,
                    Application.user_id == user.id).first()):
        raise HTTPException(409, "您已经申请过本活动")
    ext = Path(file.filename or "").suffix.lower().lstrip(".")
    if ext not in EXT_TYPES:
        raise HTTPException(400, f"不支持的文件类型（可上传 {'/'.join(sorted(EXT_TYPES))}）")
    # 多读一字节：先判大小再落盘，别把整个动辄几百兆的文件读进内存。
    # 超限的那 50M + 1 字节读完就丢，不会留在内存里
    data = await file.read(config.MAX_MATERIAL_BYTES + 1)
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
    # 接受岗位调剂（与即选即得报名同一个字段、同一个默认）：排班时用来提示与留痕
    allow_reassign: bool = True
    confirm_conflict: bool = False    # 看过时段冲突的提示、仍要申请


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

    # 每个意向场次都要过一遍资格、岗位条件与时段冲突：资格不符就拦，时段冲突
    # 先提示，本人确认后再交。意向之间重叠不算——他只是说这几场都能来，
    # 录取时管理者那边再看。
    # 先拿本人的用户锁再比时段：同一个人可能正同时在别的活动报名，不锁的话
    # 两边都看不见对方、各自落库（见 locks 的「用户锁」）。这条路不拿场次锁，
    # 锁顺序无从违反
    user_lock(db, user.id)
    rows = [] if body.confirm_conflict else logic.active_rows(db, user)
    clashes, where = [], ""
    for g, plist in picked.items():
        _check_eligibility(db, user, act, g)
        for p in plist:
            unmet = [] if p.is_placeholder else logic.unmet_requirements(user, p)
            if unmet:
                raise HTTPException(
                    403, f"您不满足「{p.name}」的报名条件：{'；'.join(unmet)}")
        hit = logic.conflicts_among(rows, g)
        if hit and not clashes:
            where = g.name
        clashes += hit
    if clashes:
        clashes = logic.dedupe_conflicts(clashes)
        raise logic.conflict_error(
            f"「{where}」与您已报名的活动时间冲突："
            f"{logic.conflict_message(clashes)}", clashes)

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
                          position_id=plist[0].id,
                          allow_reassign=body.allow_reassign))
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
        if g.needs_assign() and not p.is_placeholder:
            raise HTTPException(400, f"「{g.name}」的岗位截止后统一分配，只能勾「待分配」")
        why = logic.closed_reason(g, p)
        if why:
            raise HTTPException(409, f"{why}，请勾选别的场次或岗位")
        out.setdefault(g, []).append(p)
    if not out:
        raise HTTPException(400, "请至少勾选一个意向岗位")
    return out


def validate_answers(db: OrmSession, act: Activity, user: User, raw: dict,
                     positions: list[str] | None = None) -> dict:
    """逐题校验。校区学段、岗位或显示条件挡掉的题不校验也不存——它对这个人
    不存在。"""
    out = {}
    for q in visible_questions(_questions(db, act.id), raw, user, positions):
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
    """录取到哪个岗位：名单上那一行选的 > 他自己勾的意向岗位 > 占位岗位。"""
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
    for seq, app in enumerate(apps, 1):
        u = db.get(User, app.user_id)
        rows = (db.query(Enrollment)
                .filter(Enrollment.activity_id == activity_id,
                        Enrollment.user_id == app.user_id,
                        Enrollment.shift_id.in_(list(shifts))).all())
        wanted = [{"shift_id": e.shift_id, "shift_name": shifts[e.shift_id].name,
                   "position_id": e.position_id,
                   "position_name": _position_name(db, e),
                   "status": e.status, "cancel_type": e.cancel_type}
                  for e in rows if e.shift_id in shifts]
        answers = answers_out(db, questions, app.answers)
        out.append({
            # 第几个交申请的：提交时间只精确到分钟，同一分钟交的靠它分先后
            "id": app.id, "seq": seq, "status": app.status, "status_label": app.status_label(),
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
    shift_id: str
    position_id: str = ""     # 空 = 用他自己在这一场勾的意向岗位


class DecideBody(BaseModel):
    application_ids: list[str]
    accept: bool
    # 录取时每人录到哪几场：申请 id → 名单上他那一行勾的场次（各带岗位）
    picks: dict[str, list[Pick]] = {}
    # 看过时段冲突的提示、仍要录取：对这次提交的每个人都生效，所以前端只拿
    # 确认过的那几个人重提
    confirm_conflict: bool = False


def _accept_targets(db: OrmSession, rows: list[Enrollment],
                    picks: list[Pick]) -> list[tuple[Enrollment, Shift, Pick]]:
    """这份申请录到哪几场：每场对上他的那条意向记录。"""
    if not picks:
        raise HTTPException(400, "没有勾选录取到哪一场")
    out = []
    for pick in picks:
        target = next((e for e in rows if e.shift_id == pick.shift_id), None)
        if target is None:
            raise HTTPException(400, "没有申请这个场次，或这一场的意向已作废")
        if any(t.id == target.id for t, _g, _p in out):
            raise HTTPException(400, "同一场次只能录到一个岗位")
        out.append((target, db.get(Shift, target.shift_id), pick))
    return out


def _check_clash(db: OrmSession, act: Activity, u: User, rows: list[Enrollment],
                 chosen: list[tuple[Enrollment, Shift, Pick]]) -> None:
    """选中的几场之间、以及和他在库里其他有效报名之间，时段重叠就提示。

    申请时意向之间可以重叠（他只是说这几场都能来），录取时一个人同时在两个
    地方多半是选错了，所以先回「时段冲突」，管理者确认后（confirm_conflict）
    照录。他这份申请自己的意向记录不跟库里比：没选中的马上作废，选中的就是
    要录的这几场。
    """
    clashes, parts = [], []
    for i, (_ta, a, _pa) in enumerate(chosen):
        for tb, b, _pb in chosen[i + 1:]:
            if logic.periods_overlap(a.periods, b.periods):
                parts.append(f"选中的「{a.name}」（{a.span_text()}）与"
                             f"「{b.name}」（{b.span_text()}）时间重叠")
                clashes.append({"enrollment_id": tb.id, "activity_id": act.id,
                                "activity_title": act.title, "shift_name": b.name,
                                "time": b.span_text(), "status": tb.status,
                                "waiting": False})
    own = {e.id for e in rows}
    others = [r for r in logic.active_rows(db, u) if r[0].id not in own]
    for _t, g, _p in chosen:
        hit = logic.conflicts_among(others, g)
        if hit:
            parts.append(f"「{g.name}」与{logic.conflict_message(hit, act.id)}冲突")
            clashes += hit
    if clashes:
        raise logic.conflict_error("；".join(parts), logic.dedupe_conflicts(clashes))


def _accept_one(db: OrmSession, act: Activity, app: Application, u: User,
                rows: list[Enrollment], picks: list[Pick], confirm: bool,
                manager: User):
    """录取一个人：落库并返回（录到哪儿的说明, 通知正文）。录不了就抛 HTTPException，
    调用方回滚这一个人的保存点。"""
    chosen = _accept_targets(db, rows, picks)
    if not confirm:
        _check_clash(db, act, u, rows, chosen)
    lines, placed = [], []
    for target, g, pick in chosen:
        position = _accept_position(db, g, target, pick.position_id)
        # 条件不满足照样录得进去：筛选本来就是人的判断。但要留痕，
        # 名单上也跟着标出来，别让「怎么排了个不符合条件的人」成为悬案
        unmet = logic.unmet_requirements(u, position)
        if not logic.has_room(db, g, position):
            raise HTTPException(
                409, f"「{g.name}·{position.name}」名额已满"
                     f"（{position.quota} 人），请先调整名额或更换岗位")
        # 截止后统一分配的场次，直接录到真实岗位也占这一场的招募名额
        holder = g.placeholder()
        if holder is not None and position is not holder \
                and not logic.has_room(db, g, holder):
            raise HTTPException(
                409, f"「{g.name}」已招满（{holder.quota} 人），请先调整人数上限")
        target.status = "enrolled"
        target.leader_willing = app.leader_willing
        target.position_id = position.id
        target.assigned_at = now()
        target.assigned_by = manager.id
        target.assign_forced = bool(unmet)
        # 立即落库：同一批后面的人判名额时要数得到他
        db.flush()
        lines.append(f"{g.name}（{g.span_text()}）· "
                     + ("具体岗位将于稍后公布" if position.is_placeholder
                        else f"岗位：{position.name}"))
        placed.append(f"{g.name}·{position.name}"
                      + (f"（不满足：{'；'.join(unmet)}）" if unmet else ""))
    taken = {t.id for t, _g, _p in chosen}
    for e in rows:
        if e.id not in taken:
            e.status, e.cancel_type = "cancelled", "not_chosen"
    app.status = "accepted"
    app.decided_by, app.decided_at = manager.id, now()
    db.flush()
    # 培训/彩排看的是他在本活动全部正式报名，几场都录完再同步，
    # 而且只同步一次：几场共用同一场培训时，逐场各同步一遍的话
    # 后一遍查不到前一遍刚加的报名（会话不自动 flush），又插一条，
    # 撞上 uq_enrollment_live。这里取各场不按岗位过滤的培训并集，
    # 按岗位圈不圈得到由 sync_attached 看他全部正式岗位来定
    attached: dict[str, Shift] = {}
    for _t, g, _p in chosen:
        for s in logic.attached_shifts(db, g.id):
            attached.setdefault(s.id, s)
    logic.sync_attached(db, chosen[0][0],
                        sorted(attached.values(), key=lambda s: s.start_time))
    db.flush()
    return "、".join(placed), "\n".join(lines)


def _failure(exc: HTTPException) -> dict:
    """录不了的原因，按逐人结果的样子摊开：时段冲突另带 code 与冲突列表。"""
    d = exc.detail
    if isinstance(d, dict):
        return {"reason": d.get("message", ""), "code": d.get("code", ""),
                "conflicts": d.get("conflicts", [])}
    return {"reason": str(d), "code": "", "conflicts": []}


@router.post("/staff/activities/{activity_id}/applications/decide")
def decide(activity_id: str, body: DecideBody, bg: BackgroundTasks,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """录取或不录取，一次一个人或勾选的几个人，**逐人各自成败**。

    录取：每人录到名单上他那一行勾的场次（只能是他的意向场次）。「报名即选
    岗位」的场次落到行里选的岗位（默认他勾的意向岗位），「截止后统一分配」的
    场次落到占位岗位上等一键分配。逐场判名额，哪一场不够只拦这个人；选中的
    几场彼此重叠、或与他别处的报名重叠，回「时段冲突」，管理者确认后带
    confirm_conflict 重提。没选中的意向场次当场作废。
    不录取：意向场次全部作废，通知一句留余地的话；档案里不留痕迹。

    返回 results：每人一条，成功的写录到哪儿，失败的写原因（时段冲突另带
    code=time_conflict 与冲突列表）。
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

    # 录取要占名额：涉及的场次（这批人全部意向场次，录到的那几场必在其中）
    # 整批先锁上，判名额、落库、留痕同一个事务提交。
    # 锁要一直持有到提交：要是只包住「判名额 + 改状态」、提交在放锁之后，中间
    # 别人看到的还是旧人数。场次锁随事务走，正好一直持有到下面那次 commit。
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
    results: list[dict] = []
    with shift_lock(db, *shift_ids):
        # 锁外筛出来的「待处理」可能已经旧了：另一位管理者刚处理过其中几条。
        # 重读后只处理仍在申请中的，处理过的记进结果；一条都不剩就明说
        for app in pending:
            db.refresh(app)
        handled = [a for a in pending if a.status != "applied"]
        pending = [a for a in pending if a.status == "applied"]
        if not pending:
            raise HTTPException(409, "所选申请已被其他管理者处理，请刷新")
        for app in handled:
            u = db.get(User, app.user_id)
            results.append({"application_id": app.id, "name": u.name,
                            "student_id": u.student_id, "ok": False,
                            "reason": "已被其他管理者处理", "code": "",
                            "conflicts": []})
        if body.accept:
            # 录取要跟各人在别处的报名比时段：按人 id 排序拿用户锁，两位管理者
            # 录同一批人时顺序一致。拿完不再等任何场次锁，见 locks 的「用户锁」。
            # 场次锁、用户锁都在下面的保存点之外拿，一直持有到最后那次提交
            for who in sorted({a.user_id for a in pending}):
                user_lock(db, who)
        done = []
        for app in pending:
            u = db.get(User, app.user_id)
            item = {"application_id": app.id, "name": u.name,
                    "student_id": u.student_id}
            rows = (db.query(Enrollment)
                    .filter(Enrollment.activity_id == act.id,
                            Enrollment.user_id == app.user_id,
                            Enrollment.status == "applied").all())
            if not body.accept:
                for e in rows:
                    e.status, e.cancel_type = "cancelled", "rejected"
                app.status = "rejected"
                app.decided_by, app.decided_at = user.id, now()
                msgcenter.send_user(db, u, "application_rejected",
                                    data={"activity_id": act.id}, activity=act.title)
                done.append(u.name)
                results.append({**item, "ok": True, "placed": ""})
                continue
            # 一个人一个保存点：名额不够、时段冲突只退掉这个人的改动，前面录上的
            # 人照样留在这个事务里，后面的人照常处理
            sp = db.begin_nested()
            try:
                placed, arrangement = _accept_one(
                    db, act, app, u, rows, body.picks.get(app.id) or [],
                    body.confirm_conflict, user)
                sp.commit()
            except HTTPException as exc:
                sp.rollback()
                results.append({**item, "ok": False, **_failure(exc)})
                continue
            # 通知在保存点提交之后才写：真实推送挂在会话的发件箱里，保存点回滚
            # 撤不掉它，先写了的话录不上的人也会收到「已录取」
            msgcenter.send_user(db, u, "application_accepted",
                                data={"activity_id": act.id},
                                activity=act.title, arrangement=arrangement)
            done.append(f"{u.name}→{placed}")
            results.append({**item, "ok": True, "placed": placed})
        if done:
            audit_log(db, actor=user, action="assign", target_type="activity",
                      target_id=act.id, target_label=act.title, activity_id=act.id,
                      summary=("录取 " if body.accept else "未录取 ") + "、".join(done))
            db.commit()
        else:
            db.rollback()
    order = {a: i for i, a in enumerate(body.application_ids)}
    results.sort(key=lambda r: order.get(r["application_id"], 0))
    return {"ok": True, "count": len(done), "results": results}


# ======================= 定时：开场仍未处理的申请自动落选 =======================

def expire_undecided(db: OrmSession) -> int:
    """意向场次开始了还没处理的申请，按场次自动落选。

    管理者一直不点，志愿者就永远等不到结果。所以按场次收口：某个意向场次
    开始时，这个人在该场次仍挂着的意向记录作废（跟手动不录取同一种
    cancel_type=rejected，档案、退选画像、提示分都已经把它排除在「本人退出」
    之外）；他在本活动的意向场次全部作废、申请也还没处理的，申请置为未录取，
    发一条跟手动不录取一样的通知。晚开始的场次还在的，管理者照样能录。

    每个场次在自己的场次锁里重读再改、同一个事务提交：跟管理者同时点
    「录取」只会一先一后——先录取的，这里重读时已不是 applied；先作废的，
    录取那边锁里重读只剩还没开始的场次。只认 applied，重跑不会重复通知。
    只看报名中/已截止的活动：定时任务排在状态流转之前，停机一段时间再起来
    也会先在这里收口，再轮到活动结束。
    """
    t = now()
    shift_ids = [sid for (sid,) in (
        db.query(Enrollment.shift_id)
        .join(Shift, Enrollment.shift_id == Shift.id)
        .join(Activity, Enrollment.activity_id == Activity.id)
        .filter(Enrollment.status == "applied", Shift.start_time <= t,
                Activity.status.in_(("published", "closed")))
        .distinct())]
    db.commit()          # 还连接再排锁，理由同 enroll
    rejected = 0
    for sid in shift_ids:
        with shift_lock(db, sid):
            g = db.get(Shift, sid)
            act = db.get(Activity, g.activity_id)
            enrs = (db.query(Enrollment)
                    .filter(Enrollment.shift_id == sid,
                            Enrollment.status == "applied").all())
            if not enrs:
                db.rollback()
                continue
            voided, dropped = [], []
            for e in enrs:
                e.status, e.cancel_type, e.cancelled_at = "cancelled", "rejected", t
                u = db.get(User, e.user_id)
                voided.append(u.name)
                db.flush()
                left = (db.query(Enrollment)
                        .filter(Enrollment.activity_id == act.id,
                                Enrollment.user_id == u.id,
                                Enrollment.status == "applied").count())
                app = (db.query(Application)
                       .filter(Application.activity_id == act.id,
                               Application.user_id == u.id).first())
                if left or app is None or app.status != "applied":
                    continue
                app.status = "rejected"
                app.decided_by, app.decided_at = None, t
                msgcenter.send_user(db, u, "application_rejected",
                                    data={"activity_id": act.id}, activity=act.title)
                dropped.append(u.name)
            summary = (f"场次「{g.name}」已开始，未处理的意向自动作废："
                       + "、".join(voided))
            if dropped:
                summary += "；意向场次均已开始，申请自动落选并通知：" + "、".join(dropped)
            audit_log(db, actor=None, action="assign", target_type="activity",
                      target_id=act.id, target_label=act.title, activity_id=act.id,
                      summary=summary)
            db.commit()
            rejected += len(dropped)
    return rejected
