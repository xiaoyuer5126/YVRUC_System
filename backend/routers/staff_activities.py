"""教师/青协端：活动筹备（活动与岗位树 CRUD）、报名名单、二次分配。

岗位树校验：
  · 岗位时间必须被包裹在场次时间范围内；
  · 场次总人数需大于等于下属岗位人数之和。
所有增删改走审计留痕；影响志愿者的时间/地点类修改会推送「活动变更警告」，
附变更前后对比摘要。
"""
from __future__ import annotations

from datetime import datetime

from fastapi import (APIRouter, BackgroundTasks, Depends, File, HTTPException,
                     UploadFile)
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from .. import enroll_logic as logic
from .. import (assign, config, copying, downloads, hours as hours_flow,
                scoring, uploads)
from ..stage import ensure_hours_editable, ensure_not_finished
from ..audit import diff_changes, log as audit_log, value_text
from ..database import db_session
from ..deps import current_user_download, require_council, require_manager
from ..locks import invalidate_gates, shift_lock, urgent_lock
from ..notify import notify_edit_changes, notify_enrolled, notify_finished
from ..perm import (activity_manager_ids, add_manager,
                     ensure_activity_manager, is_activity_manager,
                     managed_activity_ids, remove_manager)
from ..integrations import msgcenter
from ..models import (Activity, ActivityManager, Application,
                      ApprovalEvent, Attachment, AttireRecord, AttireTask,
                      AuditLog, CheckinRecord, CheckinTask, Department,
                      Enrollment, FormQuestion, Notification, Position, Shift,
                      ShiftPeriod, User, VbConfirm, iso, like_pattern, now,
                      parse_local_dt, uid)
from ..serializers import (activity_brief, admin_status_of, applies_text,
                           load_trees, published_hint)
from .screening import answers_out

router = APIRouter(prefix="/api/staff", tags=["staff"])

ACTIVITY_FIELDS = {
    "title": "名称", "recruit_mode": "活动类型", "intro": "简介",
    "location": "地点", "location_point": "地点定位", "organizer": "组织单位",
    "co_organizer": "联合主办方",
    "contacts": "联系人",
    "contact_group_qr": "志愿者联络群二维码",
    "volunteer_requirement": "志愿者要求", "precautions": "注意事项",
    "volunteer_benefit": "志愿者保障",
    "recruit_campuses": "招募校区", "recruit_stages": "招募学段",
    "recruit_colleges": "招募学院",
    "reg_start": "报名开始", "reg_end": "报名截止",
    "held_periods": "活动时间",
}
# 必填的文字字段：新建时逐个查过，修改时也不能改成空的
REQUIRED_TEXT = {"title": "活动名称", "organizer": "组织单位", "location": "活动地点",
                 "intro": "活动简介", "volunteer_benefit": "志愿者保障"}
# 补记录活动（config.RECORD_MODE）用不上的那些：不公开、不报名，就没有联络群、
# 报名时间与招募范围。编辑时传了也不认，免得前端一个旧表单把它们又写回来
RECORD_UNUSED_FIELDS = ("contact_group_qr", "reg_start", "reg_end", "location_point",
                        "recruit_campuses", "recruit_stages", "recruit_colleges")
# 库里这几列是 String(n)：新建走 Pydantic 的 max_length，修改是 PATCH 任意字段、
# 直接吃 body 里的值，超了要到插入那一刻才报错，管理者看到的是一个 500
_ACTIVITY_MAX = {"title": 128, "location": 128, "organizer": 64}


def _as_int(v, label: str) -> int:
    """整数字段：不是整数就拦在校验层。true/false 不认（bool 是 int 的子类），
    小数、字符串数字、null 也都当填错。"""
    if isinstance(v, bool) or (isinstance(v, float) and not v.is_integer()):
        raise HTTPException(400, f"「{label}」要填整数")
    try:
        return int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, f"「{label}」要填整数")


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


def _shift(db: OrmSession, gid: str) -> Shift:
    g = db.get(Shift, gid)
    if g is None:
        raise HTTPException(404, "场次不存在")
    return g


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    try:
        return parse_local_dt(v)
    except ValueError:
        raise HTTPException(400, f"时间格式不合法：{v}")


def _notify_assignment_bg(items: list[dict]):
    """后台发送岗位分配通知（apply_plan 收集，路由层调度）。"""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        for it in items:
            u = db.get(User, it["user_id"])
            st = db.get(Position, it["position_id"])
            g = db.get(Shift, it["shift_id"])
            if not (u and st and g):
                continue
            msgcenter.send_user(db, u, "assignment",
                            activity=db.get(Activity, g.activity_id).title,
                            shift=g.name, position=st.name,
                            position_time=st.time_text(),
                            position_loc=st.place())
        db.commit()
    finally:
        db.close()


# ======================= 活动列表与基本信息 =======================

# 状态筛选的每个值，库里可能落到哪几种 status。展示状态要按当前时刻与场次时间
# 算（见 serializers.admin_status_of），SQL 只能先按这个把候选行砍下来，算完
# 再筛一遍——和广场列表的做法一样。
ADMIN_FILTER_BASIS = {
    "draft": ("draft",), "pending": ("pending",), "approved": ("approved",),
    "upcoming": ("published",), "published": ("published",),
    "ongoing": ("published", "closed"), "closed": ("published", "closed"),
    "finished": ("published", "closed", "finished"),
    "vb_pending": ("vb_pending",), "completed": ("completed",),
}


@router.get("/activities")
def list_all(q: str = "", status: str = "", page: int = 1, size: int = 20,
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """管理端活动列表。

    状态列与 status 筛选都用与广场同一套展示状态（见 serializers.admin_status_of）：
    「已发布但还没到报名时间」的活动在这里也标「未开始报名」，跟学生在广场上
    看到的那条一致。所以每条返回的 display_status / display_label 是管理端口径
    ——草稿、待审批、待发布分开，别的几天几档与广场相同。

    分页返回：每条都要数场次、报名与候补，只为这一页装场次岗位，条数不随活动
    总数涨。不筛状态时直接在 SQL 里分页；筛状态时展示状态要按场次时间现算，
    得先把候选行的状态都算完、筛过，再切这一页。
    """
    size = max(1, min(size, 100))
    page = max(1, page)
    # created_at 相同的几条再按 id 定序：分页靠 offset，次序不稳的话翻页会重复或漏条
    rows = (db.query(Activity.id, Activity.status, Activity.reg_start,
                     Activity.reg_end)
            .order_by(Activity.created_at.desc(), Activity.id.desc()))
    managed = managed_activity_ids(db, user)
    if managed is not None:
        rows = rows.filter(Activity.id.in_(managed or ['__none__']))
    if q.strip():
        rows = rows.filter(Activity.title.ilike(like_pattern(q.strip()),
                                                escape="\\"))
    if status:
        rows = rows.filter(Activity.status.in_(ADMIN_FILTER_BASIS.get(status, ())))
        rows = rows.all()
    else:
        total = rows.order_by(None).count()
        rows = rows.offset((page - 1) * size).limit(size).all()

    # 服务场次的跨度与最晚开场时刻：算展示状态要用（同 Activity.time_span /
    # last_shift_start，只算服务场次）。只查上面取出来的这些活动
    ids = [r[0] for r in rows]
    spans = {aid: ((first, last), last_start) for aid, first, last, last_start in
             db.query(Shift.activity_id, func.min(Shift.start_time),
                      func.max(Shift.end_time), func.max(Shift.start_time))
             .filter(Shift.kind == "service", Shift.activity_id.in_(ids))
             .group_by(Shift.activity_id).all()} if ids else {}

    if status:
        matched = []
        for aid, st, reg_start, reg_end in rows:
            span, last_start = spans.get(aid, (None, None))
            if admin_status_of(st, reg_start, reg_end, span, last_start)[0] == status:
                matched.append(aid)
        total = len(matched)
        picked = matched[(page - 1) * size: page * size]
    else:
        picked = ids
    acts = (db.query(Activity).filter(Activity.id.in_(picked)).all()
            if picked else [])
    # 场次、岗位、人数各一次查完，条数不随一页多少个活动涨
    load_trees(db, acts)
    counts = logic.shift_counts(db, [a.id for a in acts])
    by_id = {a.id: a for a in acts}
    out = []
    for aid in picked:
        item = activity_brief(db, by_id[aid], counts)
        item["waitlist_count"] = sum(counts.get(g.id, (0, 0, 0))[2]
                                     for g in by_id[aid].shifts)
        # 管理端口径的状态覆盖掉广场那套：草稿 / 待审批 / 待发布在这里分得开
        item["display_status"], item["display_label"] = admin_status_of(
            by_id[aid].status, by_id[aid].reg_start, by_id[aid].reg_end,
            spans.get(aid, (None, None))[0], spans.get(aid, (None, None))[1])
        out.append(item)
    return {"items": out, "total": total, "page": page, "size": size}


class PeriodBody(BaseModel):
    """一个服务时段或一段活动时间，精确到分钟。"""
    start_time: str = ""
    end_time: str = ""


class ActivityBody(BaseModel):
    title: str = Field(max_length=128)
    organizer: str = Field(default="", max_length=64)
    # 联合主办方：选填，与别的单位合办时才填。必填校验只认 organizer
    co_organizer: str = Field(default="", max_length=64)
    recruit_mode: str = "instant"
    intro: str = ""
    location: str = ""
    location_point: dict | None = None
    contacts: list[dict] = []
    contact_group_qr: str = ""
    cover_hue: int = 0
    # 整体要求（着装、集合）选填；保障（时长、餐食、纪念品）必填，没传就是默认那句
    volunteer_requirement: str = ""
    precautions: str = ""
    volunteer_benefit: str = config.DEFAULT_VOLUNTEER_BENEFIT
    # 招募范围：限定校区/学段/学院，空 = 不限
    recruit_campuses: list[str] = []
    recruit_stages: list[str] = []
    recruit_colleges: list[str] = []
    reg_start: str | None = None
    reg_end: str | None = None
    # 补记录活动的活动时间（它没有场次，只能直接填），可以有好几段
    held_periods: list[PeriodBody] = []


def _clean_point(raw) -> dict | None:
    """地点定位：{"lat", "lng"}，或空（没定位）。坐标留 6 位小数，约 0.1 米。"""
    if not raw:
        return None
    try:
        lat, lng = float(raw["lat"]), float(raw["lng"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(400, "地点定位的坐标不对，请重新在地图上选")
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        raise HTTPException(400, "地点定位的坐标不对，请重新在地图上选")
    return {"lat": round(lat, 6), "lng": round(lng, 6)}


def _point_text(p: dict | None) -> str:
    """变更记录与通知里怎么写这个点：一串坐标比 {'lat': …} 好认。"""
    return f"{p['lat']:.5f}, {p['lng']:.5f}" if p else "未定位"


def _clean_held_periods(items) -> list[dict]:
    """补记录活动的活动时间：至少一段，每段结束晚于开始，段与段不重叠。
    落库的是紧凑的本地时间串，读的时候再解析回来。"""
    rows = _clean_periods(items, label="活动时段")
    return [{"start_time": iso(r["start_time"]), "end_time": iso(r["end_time"])}
            for r in rows]


def _check_held_periods(raw) -> None:
    """改完活动时间后再校验一遍落库的这一版。"""
    _clean_held_periods([PeriodBody(**p) for p in raw or []])


def _clean_scope(raw: list[str], allowed: list[str], label: str) -> list[str]:
    """校验并去重一组条件值（校区/学段）。

    必须落在允许集合里：这些值是拿去跟志愿者档案里的字段逐字比对的，
    放一个错别字进来，条件永远匹配不上，所有人都被挡在外面且看不出为什么。
    """
    out = []
    for v in raw or []:
        v = str(v).strip()
        if not v:
            continue
        if v not in allowed:
            raise HTTPException(400, f"{label}「{v}」不在可选范围内")
        if v not in out:
            out.append(v)
    return out


COLLEGE_MAX_LEN = 64      # 与 User.college 的列宽一致，更长的不可能匹配上谁
COLLEGE_MAX_COUNT = 100


def _clean_colleges(raw: list[str]) -> list[str]:
    """学院条件：去空白、去重、限长度与个数，但**不**限定在固定清单里。

    学院取值来自统一身份认证（orgName），全校有哪些学院是从已有用户里汇总的
    （见 college_options），不拿任何写死的清单卡人——真实学院名
    一个都选不进来。前端只给汇总出来的选项，打错字的风险在那一层就挡住了。
    """
    out = []
    for v in raw or []:
        v = str(v).strip()
        if not v:
            continue
        if len(v) > COLLEGE_MAX_LEN:
            raise HTTPException(400, f"学院「{v[:20]}…」名称过长")
        if v not in out:
            out.append(v)
    if len(out) > COLLEGE_MAX_COUNT:
        raise HTTPException(400, f"学院最多选 {COLLEGE_MAX_COUNT} 个")
    return out


def college_options(db: OrmSession) -> list[str]:
    """学院可选项：已有用户档案里出现过的学院，排好序。

    条件是拿去跟 User.college 逐字比对的，所以选项**只能**从 User.college 里来
    ——统一身份认证写进来的是什么，这里就是什么，一字不差。

    不并任何写死的清单：写死的学院名档案里未必有，选了筛不出人；档案里真实
    出现的（比如「校团委」）又未必在清单里。
    """
    seen = {c for (c,) in db.query(User.college).distinct() if (c or "").strip()}
    return sorted(seen)


@router.get("/colleges")
def list_colleges(db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """招募范围与岗位条件里「学院」多选的选项。"""
    return {"colleges": college_options(db)}


# 场次、岗位各管各的条件，和两个表单上能填的一致：校区、学段跟着时段走（哪个
# 校区的人能来这一场），性别、学院跟着分工走。None = 学院，按学院清单校验
SHIFT_TAGS = {"校区": config.CAMPUSES, "学段": config.STAGES}
POSITION_TAGS = {"性别": ["男", "女"], "学院": None}


def _clean_requirement_tags(raw: dict, level: str) -> dict:
    """场次/岗位（level）的条件要求。只认这一层的标签，值按各自的可选集合校验。"""
    known, other = (SHIFT_TAGS, "岗位") if level == "场次" else (POSITION_TAGS, "场次")
    out = {}
    for tag, value in (raw or {}).items():
        if tag not in known:
            if tag in SHIFT_TAGS or tag in POSITION_TAGS:
                raise HTTPException(400, f"{level}上只能设{'、'.join(known)}条件，"
                                         f"「{tag}」请设在{other}上")
            raise HTTPException(400, f"未知的条件标签「{tag}」")
        vals = value if isinstance(value, list) else [value]
        vals = (_clean_colleges(vals) if known[tag] is None
                else _clean_scope(vals, known[tag], tag))
        if vals:
            out[tag] = vals
    return out


def _clean_contacts(raw: list[dict]) -> list[dict]:
    """清洗联系人名单：至少一位，且每位的姓名与电话均非空。"""
    contacts = []
    for c in raw or []:
        name = str(c.get("name") or "").strip() if isinstance(c, dict) else ""
        phone = str(c.get("phone") or "").strip() if isinstance(c, dict) else ""
        if not name:
            raise HTTPException(400, "请填写每位联系人的姓名")
        if not phone:
            raise HTTPException(400, f"请填写联系人「{name}」的电话/微信")
        contacts.append({"name": name, "phone": phone})
    if not contacts:
        raise HTTPException(400, "请至少添加一位联系人（姓名+电话）")
    return contacts


def ensure_unique_title(db: OrmSession, title: str, *,
                        exclude_id: str = "") -> str:
    """活动名全平台唯一（草稿也算）。

    重名的活动在广场、名单、导出文件名、留痕摘要里全都分不清是哪一场，
    志愿者更是没法判断自己报的是哪个。
    """
    name = title.strip()
    q = db.query(Activity.id).filter(Activity.title == name)
    if exclude_id:
        q = q.filter(Activity.id != exclude_id)
    if q.first():
        raise HTTPException(409, f"已有同名活动「{name}」，请换一个名字")
    return name


def unique_copy_title(db: OrmSession, title: str) -> str:
    """复制用：撞名就往后加序号，不打断操作。"""
    name = title.strip()
    if not db.query(Activity.id).filter(Activity.title == name).first():
        return name
    for i in range(2, 100):
        candidate = f"{name} {i}"
        if not db.query(Activity.id).filter(Activity.title == candidate).first():
            return candidate
    raise HTTPException(409, f"已有太多同名活动「{name}」，请自己起个名字")


@router.post("/activities")
def create_activity(body: ActivityBody, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    # 创建活动信息完整性校验：全部必填
    if not body.title.strip():
        raise HTTPException(400, "请填写活动名称")
    ensure_unique_title(db, body.title)
    if not body.organizer.strip():
        raise HTTPException(400, "请填写组织单位")
    if body.recruit_mode not in config.RECRUIT_MODES:
        raise HTTPException(400, "请选择活动类型")
    if not body.location.strip():
        raise HTTPException(400, "请填写活动地点")
    contacts = _clean_contacts(body.contacts)
    if not body.intro.strip():
        raise HTTPException(400, "请填写活动简介")
    record = body.recruit_mode == config.RECORD_MODE
    # 补记录活动不招人、没有招募文案，表单上也没有这一项
    benefit = body.volunteer_benefit.strip() or (
        config.DEFAULT_VOLUNTEER_BENEFIT if record else "")
    if not benefit:
        raise HTTPException(400, "请填写志愿者保障")
    held_periods = []
    if record:
        # 补记录：线下已经办完，不公开、不报名——没有联络群、报名时间与招募
        # 范围，传了也不收；要的是这场活动实际是什么时候办的
        held_periods = _clean_held_periods(body.held_periods)
        body.contact_group_qr = ""
        body.reg_start = body.reg_end = None
        body.recruit_campuses, body.recruit_stages, body.recruit_colleges = [], [], []
    elif not body.contact_group_qr.strip():
        raise HTTPException(400, "请上传志愿者联络群二维码")
    reg_start, reg_end = _parse_dt(body.reg_start), _parse_dt(body.reg_end)
    if not record and (reg_start is None or reg_end is None):
        raise HTTPException(400, "请选择报名开始与截止时间")
    act = Activity(id=uid("act"), title=body.title.strip(),
                   recruit_mode=body.recruit_mode,
                   organizer=body.organizer.strip(),
                   co_organizer=(body.co_organizer or "").strip(),
                   intro=body.intro.strip(), location=body.location.strip(),
                   location_point=None if record else _clean_point(body.location_point),
                   contact_group_qr=body.contact_group_qr.strip(),
                   cover_hue=body.cover_hue,
                   volunteer_requirement=body.volunteer_requirement.strip(),
                   precautions=body.precautions.strip(),
                   volunteer_benefit=benefit,
                   recruit_campuses=_clean_scope(body.recruit_campuses,
                                                 config.CAMPUSES, "校区"),
                   recruit_stages=_clean_scope(body.recruit_stages,
                                               config.STAGES, "学段"),
                   recruit_colleges=_clean_colleges(body.recruit_colleges),
                   reg_start=reg_start, reg_end=reg_end,
                   held_periods=held_periods,
                   # 归口部门：青协的人建的活动归自己部门，部长从草稿期就看得到；
                   # 会长团与发起方建的留空，提交审批时再选
                   approval_dept_id=(user.department_id
                                     if user.has_role("qingxie") else None),
                   created_by=user.id, status="draft")
    ensure_reg_times(act)
    act.contacts = contacts
    db.add(act)
    db.flush()
    # 创建者自动成为本活动管理者（多人共管的基础，见 perm.add_manager 同款逻辑）
    db.add(ActivityManager(id=uid("mgr"), activity_id=act.id,
                           user_id=user.id, added_by=user.id))
    audit_log(db, actor=user, action="create", target_type="activity",
              target_id=act.id, target_label=act.title,
              activity_id=act.id, summary=f"新建活动「{act.title}」")
    db.commit()
    return {"id": act.id}


@router.patch("/activities/{activity_id}")
def update_activity(activity_id: str, body: dict, bg: BackgroundTasks,
                    db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_not_finished(act)
    before = {k: getattr(act, k) for k in ACTIVITY_FIELDS}
    # 类型先改：后面哪些字段算数要看改完之后是不是补记录
    if "recruit_mode" in body:
        _apply_recruit_mode(db, act, body["recruit_mode"])
    for key in ACTIVITY_FIELDS:
        if key not in body or key == "recruit_mode":
            continue
        if act.is_record() and key in RECORD_UNUSED_FIELDS:
            continue
        if not act.is_record() and key == "held_periods":
            continue
        value = body[key]
        if key in REQUIRED_TEXT:
            value = str(value or "").strip()
            if not value and key == "volunteer_benefit" and act.is_record():
                value = config.DEFAULT_VOLUNTEER_BENEFIT
            if not value:
                raise HTTPException(400, f"{REQUIRED_TEXT[key]}不能为空")
            limit = _ACTIVITY_MAX.get(key)
            if limit and len(value) > limit:
                raise HTTPException(400, f"{REQUIRED_TEXT[key]}不能超过 {limit} 个字")
        if key == "title" and str(value).strip() != act.title:
            ensure_unique_title(db, str(value), exclude_id=act.id)
        if key in ("reg_start", "reg_end"):
            setattr(act, key, _parse_dt(value))
        elif key == "held_periods":
            act.held_periods = _clean_held_periods(value)
        elif key == "contacts":
            act.contacts = _clean_contacts(value)
        elif key == "location_point":
            act.location_point = _clean_point(value)
        elif key == "recruit_campuses":
            act.recruit_campuses = _clean_scope(value, config.CAMPUSES, "校区")
        elif key == "recruit_stages":
            act.recruit_stages = _clean_scope(value, config.STAGES, "学段")
        elif key == "recruit_colleges":
            act.recruit_colleges = _clean_colleges(value)
        elif key == "contact_group_qr":
            if not str(value).strip():
                raise HTTPException(400, "志愿者联络群二维码不能为空")
            act.contact_group_qr = str(value).strip()
        elif key == "co_organizer":
            # 选填：清空就是各自单独主办，不留空白。长度同新建时的上限，超长
            # 直接写库会撞列宽报 500
            value = str(value or "").strip()
            if len(value) > 64:
                raise HTTPException(400, "联合主办方不能超过 64 个字")
            act.co_organizer = value
        else:
            setattr(act, key, value)
    if "reg_start" in body or "reg_end" in body:
        ensure_reg_times(act, future=act.reg_end != before["reg_end"])
    if "reg_end" in body and not act.is_record():
        ensure_reg_end_in_range(act)
    if act.is_record() and ("held_periods" in body or "recruit_mode" in body):
        _check_held_periods(act.held_periods)
    if any(k in body for k in ("recruit_campuses", "recruit_stages",
                               "recruit_colleges")) \
            and not body.get("keep_outsiders"):
        _refuse_if_enrolled_outside_scope(db, act)
    def shown(k, v):
        if isinstance(v, datetime):
            return iso(v)
        if k == "held_periods":
            return "、".join(
                f"{_parse_dt(p['start_time']):%Y-%m-%d %H:%M}-"
                f"{_parse_dt(p['end_time']):%H:%M}" for p in v or [])
        return _point_text(v) if k == "location_point" else v
    changes = diff_changes({k: shown(k, v) for k, v in before.items()},
                           {k: shown(k, getattr(act, k)) for k in ACTIVITY_FIELDS},
                           ACTIVITY_FIELDS)
    if not changes:
        return {"ok": True, "changes": []}

    # 活动变更警告（带前后对比摘要），对已报名志愿者推送。编辑态下不单发：
    # 保存并发布时和场次、岗位的改动一起按人汇总（见 finish_editing）
    if not act.is_unpublished() and act.editing_since is None:
        summary = "；".join(
            f"{c['label']}：{value_text(c['old']) or '（空）'}"
            f"→{value_text(c['new']) or '（空）'}" for c in changes[:6])
        bg.add_task(_notify_activity_bg, act.id, "activity_changed",
                    activity=act.title, change_summary=summary)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"修改活动信息：{'、'.join(c['label'] for c in changes)}",
              changes=changes,
              visible=True)
    db.commit()
    return {"ok": True, "changes": changes}


def enrolled_outside_scope(db: OrmSession, act: Activity) -> list[dict]:
    """已报名、候补、待确认或已申请，但落在（新的）招募范围之外的人。"""
    ids = {r[0] for r in db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status.in_(("enrolled", "waitlisted",
                                          "pending_confirm", "applied"))).all()}
    ids |= {r[0] for r in db.query(Application.user_id)
            .filter(Application.activity_id == act.id,
                    Application.status.in_(("applied", "accepted"))).all()}
    out = []
    for u in db.query(User).filter(User.id.in_(ids)).order_by(User.name).all():
        unmet = logic.unmet_recruit_scope(u, act)
        if unmet:
            out.append({"name": u.name, "student_id": u.student_id,
                        "reason": "；".join(unmet)})
    return out


def _refuse_if_enrolled_outside_scope(db: OrmSession, act: Activity) -> None:
    """缩小招募范围时，已经报上名的人不能悄悄变成「不该在名单上的人」。

    不自动退任何人：拦下来把名单交给管理者，要么先去名单页处理，
    要么明确说「这些人保留」再改。
    """
    outsiders = enrolled_outside_scope(db, act)
    if outsiders:
        raise HTTPException(409, {
            "code": "OUTSIDERS_ENROLLED",
            "message": f"有 {len(outsiders)} 位已报名的志愿者不在新的招募范围内",
            "outsiders": outsiders})


def _apply_recruit_mode(db: OrmSession, act: Activity, value: str) -> None:
    """改活动类型。公开之后不给改：即选即得已经有人占了岗位，报名后筛选
    已经有人填了表，两边的账互相对不上。"""
    if value not in config.RECRUIT_MODES:
        raise HTTPException(400, "请选择活动类型")
    if value == act.recruit_mode:
        return
    if act.status not in ("draft", "pending", "approved"):
        raise HTTPException(409, "活动已公开，不能再改活动类型")
    # 补记录与招募活动之间只在草稿里切换：审批人批的是哪一种，就是哪一种
    if config.RECORD_MODE in (value, act.recruit_mode) and act.status != "draft":
        raise HTTPException(409, "「不公开招募（补记录）」只能在草稿阶段切换，"
                                 "请等驳回回到草稿后再改")
    if value == config.RECORD_MODE:
        if act.shifts:
            raise HTTPException(409, "补记录活动不设场次与岗位，请先删掉已建的场次")
        if db.query(FormQuestion).filter(FormQuestion.activity_id == act.id).first():
            raise HTTPException(409, "补记录活动不设报名问卷，请先删掉已建的题目")
        # 不公开的活动用不上这些，留着只会在别处被当成真的
        act.contact_group_qr = ""
        act.reg_start = act.reg_end = None
        act.recruit_campuses, act.recruit_stages, act.recruit_colleges = [], [], []
    elif act.recruit_mode == config.RECORD_MODE:
        act.held_periods = []
    act.recruit_mode = value


def _notify_finished_bg(activity_id: str):
    """后台发活动结束通知，见 notify.notify_finished；放后台的理由同下。"""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        act = db.get(Activity, activity_id)
        if act is not None:
            notify_finished(db, act)
            db.commit()
    finally:
        db.close()


def _notify_activity_bg(activity_id: str, template: str, **kw):
    """后台任务入口：自带数据库会话，避免拖慢接口响应。

    变更通知要遍历全部报名者逐个落库，人多时耗时数秒——放后台，
    接口先返回，前端按钮立刻恢复。
    """
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        act = db.get(Activity, activity_id)
        if act is not None:
            notify_enrolled(db, act, template, **kw)
            db.commit()
    finally:
        db.close()


# ======================= 编辑冻结 =======================

# 「结束编辑并发布改动」那条留痕里最多摘几项改动，见 finish_editing
EDIT_SUMMARY_MAX = 8


@router.post("/activities/{activity_id}/edit-lock")
def start_editing(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """进入编辑态：本活动全部场次暂停报名与退选。

    冻结整个活动而不是单个场次——批量复制岗位、调名额这类改动本来就跨场次，
    而且同一活动里「有的能报有的不能报」学生只会更困惑。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_not_finished(act)
    if act.is_record():
        raise HTTPException(409, "补记录活动没有场次与岗位可调整")
    if act.is_unpublished():
        raise HTTPException(409, "活动还没有发布，可直接修改，无需暂停报名")
    if act.editing_since is not None:
        who = db.get(User, act.editing_by)
        raise HTTPException(409, f"{who.name if who else '其他管理者'} "
                                 f"正在编辑本活动，请等对方保存后再试")
    act.editing_since = now()
    act.editing_by = user.id
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="进入编辑态，暂停报名")
    db.commit()
    return {"ok": True, "editing": True,
            "auto_release_hours": config.EDIT_LOCK_HOURS}


@router.post("/activities/{activity_id}/publish-changes")
def finish_editing(activity_id: str, bg: BackgroundTasks,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """保存并发布：解冻，并把这段时间的改动汇总推给已报名的志愿者。

    改动里常有扩容——加名额、加岗位。名额空出来就该轮到候补，否则队伍
    一直排着、位子一直空着，得等下一个人退选才动。

    活动行加锁：两位管理者同时点发布，或者撞上定时任务的超时解冻，只有一边
    解冻并发出汇总，另一边看到「不在编辑态」，志愿者不会收到两份。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    db.refresh(act, with_for_update=True)
    if act.editing_since is None:
        raise HTTPException(409, "本活动不在编辑态")
    since = act.editing_since
    act.editing_since = None
    act.editing_by = None

    rows = (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id,
                    AuditLog.created_at >= since,
                    AuditLog.visible.is_(True))
            .order_by(AuditLog.created_at).all())
    # 只摘前几条：编辑期间改几十处是常事，整摞拼进来这条留痕会长到没法读，
    # 而且它还要原样回给前端提示。完整的明细本来就一条一条记在上面那些留痕里
    summary = "；".join(r.summary for r in rows[:EDIT_SUMMARY_MAX]) or "无实质改动"
    if len(rows) > EDIT_SUMMARY_MAX:
        summary += f"；等共 {len(rows)} 项"
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"结束编辑并发布改动：{summary}")
    db.commit()
    # 编辑态已经解除，这次的汇总只有这一次机会发：先排上，再去补位。
    # 补位要逐场拿场次锁，哪一场排不上会跳过而不是让请求报错（见 _fill_vacancies），
    # 请求报错的话排好的后台任务也会跟着作废
    if rows and not act.is_unpublished():
        bg.add_task(_notify_edit_changes_bg, act.id, since)
    promoted, busy = _fill_vacancies(db, act)
    return {"ok": True, "editing": False, "changes": len(rows),
            "promoted": promoted, "busy": busy, "summary": summary}


def _notify_edit_changes_bg(activity_id: str, since: datetime):
    """后台把编辑期间的改动按人汇总发出去，见 notify.notify_edit_changes。"""
    from ..database import SessionLocal
    db = SessionLocal()
    try:
        act = db.get(Activity, activity_id)
        if act is not None:
            notify_edit_changes(db, act, since)
            db.commit()
    finally:
        db.close()


def _fill_vacancies(db: OrmSession, act: Activity) -> tuple[int, list[str]]:
    """活动下所有场次各自补齐名额，返回（通知了多少位候补去确认, 没补成的场次名）。

    调用方都是先提交了「恢复报名」再来补，这时正是抢报的时候：哪一场的锁
    一时排不上就跳过，接着补别的场次。整个请求报 503 的话，报名其实已经恢复，
    管理者却以为没成功，再点一次只会被拒（已经不在编辑态 / 已经在报名中），
    排好的变更汇总也跟着作废。跳过的场次名回给前端，提醒管理者稍后到名单里
    手动转正。
    """
    total = 0
    busy: list[str] = []
    for g in act.shifts:
        if g.is_attached():
            continue      # 培训/彩排跟着主场次走，没有自己的队列
        # 一个场次一个事务：锁随 commit 放掉，下一个场次重新拿
        try:
            with shift_lock(db, g.id):
                logic.reload_shift(db, g)
                total += logic.fill_vacancies(db, g)
                db.commit()
        except HTTPException as exc:
            if exc.status_code != 503:
                raise
            db.rollback()
            busy.append(g.name)
    return total, busy


# ======================= 复制 =======================

class CopyActivityBody(BaseModel):
    title: str = ""
    shift_days: int = 7      # 所有时间整体平移多少天
    contact_group_qr: str = ""   # 新活动新群，复制时就要传新图


@router.post("/activities/{activity_id}/copy")
def copy_activity_route(activity_id: str, body: CopyActivityBody,
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """复制一个活动的结构。报名、签到、时长、留痕都不带过来。"""
    ensure_activity_manager(db, user, activity_id)
    src = _act(db, activity_id)
    if body.title.strip():
        title = ensure_unique_title(db, body.title)     # 自己起的名字就得是新的
    else:
        title = unique_copy_title(db, f"{src.title}（副本）")
    if len(title) > 128:
        raise HTTPException(400, "活动名称过长")
    # 补记录活动不公开，没有联络群
    if not src.is_record() and not body.contact_group_qr.strip():
        raise HTTPException(400, "请上传新活动的志愿者联络群二维码")
    act = copying.copy_activity(db, src, user, title=title,
                                shift_days=body.shift_days,
                                contact_group_qr=body.contact_group_qr.strip())
    audit_log(db, actor=user, action="copy", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"复制自「{src.title}」（{copying.describe(src)}），"
                      f"时间整体平移 {body.shift_days} 天")
    db.commit()
    return {"id": act.id, "title": act.title}


class CopyPositionsBody(BaseModel):
    position_ids: list[str] = []      # 空 = 该场次下全部岗位
    target_shift_ids: list[str]


@router.post("/shifts/{shift_id}/positions/copy")
def copy_positions_route(shift_id: str, body: CopyPositionsBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    """把本场次的岗位批量复制到同一活动的其它场次。"""
    src = _shift(db, shift_id)
    ensure_activity_manager(db, user, src.activity_id)
    _ensure_editing(db, src.activity_id)
    sources = sorted(src.positions, key=lambda p: p.sort)
    if body.position_ids:
        wanted = set(body.position_ids)
        sources = [p for p in sources if p.id in wanted]
    if not sources:
        raise HTTPException(400, "该场次下没有可复制的岗位")

    targets = []
    for tid in body.target_shift_ids:
        target = db.get(Shift, tid)
        if target is None or target.activity_id != src.activity_id:
            raise HTTPException(400, "目标场次不属于本活动")
        if target.id == src.id:
            continue
        targets.append(target)
    if not targets:
        raise HTTPException(400, "请选择至少一个目标场次")

    result = copying.copy_positions_to(db, sources, targets, user)
    audit_log(db, actor=user, action="copy", target_type="position",
              target_id=src.id, target_label=src.name,
              activity_id=src.activity_id,
              summary=f"从「{src.name}」复制 {len(sources)} 个岗位到 "
                      f"{len(targets)} 个场次，新建 {result['copied']} 个"
                      + (f"（；同名跳过 {len(result['skipped'])} 个）"
                         if result["skipped"] else ""))
    db.commit()
    return result


# ======================= 状态流转 =======================

@router.post("/activities/{activity_id}/publish")
def publish(activity_id: str, db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    """手动发布。只有提交审批时关掉了「通过即发布」的活动才会走到这里。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    # 所有活动发布前都要过校青协：草稿要先提交审批，通过后才轮到这一步
    if act.is_record():
        raise HTTPException(409, "补记录活动不公开发布，审批通过后直接导入名单")
    if act.status == "draft":
        raise HTTPException(409, "请先提交校青协审批，通过后再发布")
    if act.status != "approved":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能发布")
    publish_approved(db, act, user)
    db.commit()
    # 发布成功的提示由服务端给：设了将来的报名开始时间就说清是哪一刻开始，
    # 别让页面写死「学生现在可以报名」
    return {"ok": True, "hint": published_hint(act.reg_start)}


def publish_approved(db: OrmSession, act: Activity, user: User) -> None:
    """把审批通过的活动放到广场上。审批人拍板时自动走这里，
    关掉了「通过即发布」的则由发起方点按钮走这里。不提交事务，交给调用方。"""
    ensure_ready_to_publish(act)
    act.status = "published"
    if act.reg_start is None:
        act.reg_start = now()
    audit_log(db, actor=user, action="publish", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="发布活动，开始报名")


def ensure_ready_to_publish(act: Activity) -> None:
    """结构上够不够格公开。提交审批与发布都走这一道——审批期间发起方还能
    继续改，真上线的那一刻不能带着窟窿出去。

    补记录活动不上线，只看基本信息：活动时间得有。"""
    if act.is_record():
        _check_held_periods(act.held_periods)
        return
    if not act.contact_group_qr:
        raise HTTPException(400, "请先在基本信息里上传志愿者联络群二维码")
    if not act.main_shifts():
        raise HTTPException(400, "请先至少创建一个场次")
    # 「报名即选岗位」的场次没有岗位就等于没有名额，发出去学生一个都报不了
    empty = [g.name for g in act.main_shifts()
             if not g.needs_assign() and not g.positions]
    if empty:
        raise HTTPException(
            400, f"场次「{'、'.join(empty)}」还没有岗位，学生无法报名。"
                 f"请先添加岗位，或把这些场次改为「截止后统一分配」")
    ensure_reg_times(act)
    ensure_reg_end_in_range(act)


def ensure_reg_end_in_range(act: Activity) -> None:
    """报名截止不能晚于最后一个场次的开始时间。

    晚于它的那一段是死窗口：每个场次在自己开场时就停止收人，最后一场都开始了，
    活动却还挂着「报名中」，学生点进去每一场都是灰的。
    """
    last = act.last_shift_start()
    if act.reg_end and last and act.reg_end > last:
        raise HTTPException(
            400, f"报名截止时间（{act.reg_end:%Y-%m-%d %H:%M}）晚于最后一个场次的"
                 f"开始时间（{last:%Y-%m-%d %H:%M}），请将报名截止时间提前，"
                 f"或将场次时间后移")


def ensure_reg_times(act: Activity, future: bool = True) -> None:
    """报名截止要晚于报名开始；future 时还要晚于此刻。

    截止已过的活动一发布就是「已截止」，没人报得上；截止早于开始则一刻
    都不开放。改报名时间时只有截止时间动了才查「晚于此刻」：活动报名
    截止之后再改别的（比如只改开始时间），不该被已经过去的截止时间拦住。
    """
    if act.is_record() or act.reg_end is None:
        return
    if act.reg_start is not None and act.reg_end <= act.reg_start:
        raise HTTPException(
            400, f"报名截止时间（{act.reg_end:%Y-%m-%d %H:%M}）须晚于报名开始时间"
                 f"（{act.reg_start:%Y-%m-%d %H:%M}）")
    if future and act.reg_end <= now():
        raise HTTPException(
            400, f"报名截止时间（{act.reg_end:%Y-%m-%d %H:%M}）已经过去，"
                 f"请重新选择报名截止时间")


def _transition(activity_id: str, target: str, action: str, summary: str,
                allowed_from: tuple[str, ...], db: OrmSession, user: User,
                bg: BackgroundTasks):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status not in allowed_from:
        raise HTTPException(409,
                            f"当前状态为「{act.label_status()}」，不允许该操作")
    act.status = target
    audit_log(db, actor=user, action=action, target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)
    db.commit()
    if action == "finish":
        bg.add_task(_notify_finished_bg, act.id)
    return {"ok": True}


@router.post("/activities/{activity_id}/close")
def close(activity_id: str, bg: BackgroundTasks,
          db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    out = _transition(activity_id, "closed", "close", "截止报名",
                      ("published",), db, user, bg)
    # 手动截止和系统自动截止是同一件事，排岗提醒不能只挂在定时任务上
    from ..scheduled import notify_managers_to_assign
    notify_managers_to_assign(db, _act(db, activity_id))
    db.commit()
    return out


class PinBody(BaseModel):
    pinned: bool


@router.post("/activities/{activity_id}/pin")
def pin(activity_id: str, body: PinBody,
        db: OrmSession = Depends(db_session),
        user: User = Depends(require_council)):
    """置顶（急招）/ 取消置顶。只有会长团和管理员能点：急招是全校层面的
    调度，不是某个活动自己说了算。只对报名中的活动有意义，截止时自动取消
    （见 models._drop_pin_off_published）。

    同时最多 config.URGENT_LIMIT 个；已经置顶的再点一次不算新占名额，
    置顶时间也不动（广场上急招之间按置顶先后排）。
    """
    act = _act(db, activity_id)
    if body.pinned == (act.pinned_at is not None):
        return {"ok": True, "pinned": body.pinned}
    if body.pinned:
        # 拿到名额锁之后重读：锁里看到的才是别人提交之后的状态与名额
        urgent_lock(db)
        db.refresh(act)
        if act.status != "published":
            raise HTTPException(409, "只有报名中的活动才能置顶为急招")
        if act.pinned_at is None:
            taken = [t for (t,) in (db.query(Activity.title)
                                    .filter(Activity.status == "published",
                                            Activity.pinned_at.isnot(None))
                                    .order_by(Activity.pinned_at))]
            if len(taken) >= config.URGENT_LIMIT:
                raise HTTPException(
                    409, f"急招最多同时 {config.URGENT_LIMIT} 个，请先取消其中一个："
                         + "、".join(f"「{t}」" for t in taken))
            act.pinned_at = now()
    else:
        act.pinned_at = None
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary="置顶为急招" if body.pinned else "取消急招置顶")
    db.commit()
    return {"ok": True, "pinned": act.pinned_at is not None}


class ReopenBody(BaseModel):
    reg_end: str          # 新的报名截止时间，必须晚于此刻


@router.post("/activities/{activity_id}/reopen")
def reopen(activity_id: str, body: ReopenBody,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """已截止 → 重新开放报名。

    必须重设截止时间：定时任务是按 reg_end 判断的，只把状态改回「报名中」
    而不动 reg_end，下一分钟就会被再截止一次，等于白点。
    最后一个场次也开场之后不给恢复——那时每一场都已经各自关闸，恢复出来
    一个人也报不进来。前面几场开过了不要紧，后面还没开的照样可以补人。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status != "closed":
        raise HTTPException(409, f"当前状态为「{act.label_status()}」，不能恢复报名")
    last = act.last_shift_start()
    t = now()
    if last and t >= last:
        raise HTTPException(409, "所有场次都已开始，无法恢复报名")
    new_end = _parse_dt(body.reg_end)
    if new_end is None:
        raise HTTPException(400, "请选择新的报名截止时间")
    if new_end <= t:
        raise HTTPException(400, "新的报名截止时间必须晚于当前时间")
    if last and new_end > last:
        raise HTTPException(400, f"报名截止时间不能晚于最后一个场次的开始时间"
                                 f"（{last:%Y-%m-%d %H:%M}）")
    old_end = act.reg_end
    act.status = "published"
    act.reg_end = new_end
    audit_log(db, actor=user, action="publish", target_type="activity",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"恢复报名，报名截止 "
                      f"{old_end:%Y-%m-%d %H:%M} → {new_end:%Y-%m-%d %H:%M}"
                      if old_end else f"恢复报名，报名截止 {new_end:%Y-%m-%d %H:%M}",
              visible=True)
    db.commit()
    # 恢复报名前先把空着的名额按候补顺序补齐，别让新来的人抢在排队的人前面
    promoted, busy = _fill_vacancies(db, act)
    return {"ok": True, "reg_end": iso(act.reg_end), "promoted": promoted,
            "busy": busy}


@router.post("/activities/{activity_id}/finish")
def finish(activity_id: str, bg: BackgroundTasks,
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    # 只能从「已截止」结束：报名还开着就宣布结束，等于让正在报名的人
    # 报进一个已经收摊的活动。先截止，再结束。
    return _transition(activity_id, "finished", "finish", "结束活动",
                       ("closed",), db, user, bg)


@router.delete("/activities/{activity_id}")
def delete_activity(activity_id: str, db: OrmSession = Depends(db_session),
                    user: User = Depends(require_manager)):
    """仅草稿可删——发布过的活动承载着报名记录与留痕，不允许物理删除。

    场次与岗位有 ORM 级联，其余指向本活动的表要自己清：共管名单、审批流水、
    报名问卷、服装台账、签到任务。少清一张表，删除就撞外键报 500——草稿活动
    身上至少永远挂着一条创建者的共管记录。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.status != "draft":
        raise HTTPException(409, "仅「筹备中」的活动可以删除；"
                                 "已发布的活动请使用「结束活动」归档")
    label = act.title
    for model in (ActivityManager, ApprovalEvent, AttireRecord, AttireTask,
                  CheckinTask, Enrollment, FormQuestion, VbConfirm):
        (db.query(model).filter(model.activity_id == activity_id)
         .delete(synchronize_session=False))
    # 立项书没有外键拦着，不清就是磁盘上一份没人指向的文件
    from ..liaison import SLOTS, drop_project_file
    for slot in SLOTS:
        drop_project_file(db, activity_id, slot)
    db.flush()
    db.delete(act)
    audit_log(db, actor=user, action="delete", target_type="activity",
              target_id=activity_id, target_label=label, activity_id=None,
              summary=f"删除活动草稿「{label}」")
    db.commit()
    return {"ok": True}


# ======================= 场次 =======================

SHIFT_MODES = ("pick", "assign")


SHIFT_KINDS = ("service", "training", "rehearsal")
HOURS_MODES = ("checkin", "fixed", "none")


class ShiftBody(BaseModel):
    name: str = Field(max_length=128)
    periods: list[PeriodBody] = []
    mode: str = "pick"          # pick 报名即选岗位 / assign 截止后统一分配
    # 只有 assign 模式用得上：报名时还没有岗位可选，名额只能记在场次上
    capacity: int = Field(default=0, ge=0)
    note: str = ""
    location: str = Field(default="", max_length=128)
    waitlist_quota: int = Field(default=3, ge=0)   # 候补队列人数上限
    hours_mode: str = "checkin"
    # —— 培训/彩排：挂在整个活动上，applies_to_positions 圈要来的岗位（空 = 全体）——
    kind: str = "service"
    applies_to_positions: list[str] = []
    # 场次级硬性条件：{"校区": [...], "学段": [...]}，空 = 不限
    requirement_tags: dict = {}
    # 开场前多少小时自动锁定名单，0 = 不自动锁定
    lock_hours: int = Field(default=config.LOCK_HOURS_DEFAULT, ge=0,
                            le=config.LOCK_HOURS_MAX)
    # 时间灵活/待定的场次：每张签到码各签一次到、一次退，可以签好几轮
    flexible_time: bool = False


def _clean_lock_hours(v) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        raise HTTPException(400, "自动锁定时间必须是整数小时") from None
    if not 0 <= n <= config.LOCK_HOURS_MAX:
        raise HTTPException(400, f"自动锁定时间要在 0～{config.LOCK_HOURS_MAX} 小时之间"
                                 f"（0 = 不自动锁定）")
    return n


def _clean_periods(items, *, label: str = "服务时段") -> list[dict]:
    """校验并摊平客户端传来的时段。

    每段都要有起止、结束晚于开始。同一组里的时段不能互相压着——
    那样同一段时间会被签到、时长算两遍。
    """
    if not items:
        raise HTTPException(400, f"请至少填写一个{label}")
    rows = []
    for item in items:
        if isinstance(item, dict):
            raw_start = str(item.get("start_time") or "").strip()
            raw_end = str(item.get("end_time") or "").strip()
        else:
            raw_start = (getattr(item, "start_time", "") or "").strip()
            raw_end = (getattr(item, "end_time", "") or "").strip()
        if not raw_start:
            raise HTTPException(400, f"{label}要填开始时间")
        if not raw_end:
            raise HTTPException(400, f"请填写{label}的结束时间")
        start, end = _parse_dt(raw_start), _parse_dt(raw_end)
        if start is None or end is None:
            raise HTTPException(400, f"{label}的时间格式不对")
        if end <= start:
            raise HTTPException(400, f"{label}的结束时间必须晚于开始时间")
        rows.append({"start_time": start, "end_time": end})
    rows.sort(key=lambda r: r["start_time"])
    for a, b in zip(rows, rows[1:]):
        if b["start_time"] < a["end_time"]:
            raise HTTPException(400, "同一组里的时段不能重叠")
    return rows


def _set_periods(db: OrmSession, g: Shift, rows: list[dict]) -> None:
    """把这一场的时段设成 rows。

    段数不变时就地改时间，段的 id 不动：签到记录和时长明细都按段的 id 认
    是哪一段，换了 id 它们就成了找不到时段的孤儿。已经生成过签到码的场次
    不允许增减段数，要重组就先停用签到码、删了重建。
    """
    existing = g.sorted_periods()
    if len(existing) == len(rows):
        for i, (p, row) in enumerate(zip(existing, rows)):
            p.start_time, p.end_time, p.sort = (
                row["start_time"], row["end_time"], i)
    else:
        if g.id and db.query(CheckinTask).filter(
                CheckinTask.shift_id == g.id).first():
            raise HTTPException(
                409, "这一场已经生成过签到码，不能再增减时段；"
                     "可以改已有时段的时间，或先停用签到码")
        g.periods.clear()
        for i, row in enumerate(rows):
            g.periods.append(ShiftPeriod(id=uid("sp"), shift_id=g.id,
                                         sort=i, **row))
    db.flush()


def _validate_flexible_hours(g: Shift) -> None:
    """时间灵活的场次一人能签好几轮到，「按场次时长固定计」会把每一轮都算成
    整场，所以只能按签到签退计或不计时长。"""
    if g.flexible_time and g.hours_mode == "fixed":
        raise HTTPException(400, "时间灵活的场次时长只能按签到签退计或不计时长，"
                                 "不能按场次时长固定计")


def _clean_applies(act: Activity, ids: list[str]) -> list[str]:
    """培训/彩排要叫的岗位：本活动服务场次里的岗位，可以跨场次。"""
    known = {p.id for g in act.main_shifts() for p in g.positions}
    if [i for i in ids if i not in known]:
        raise HTTPException(400, "所选岗位不属于本活动")
    return list(dict.fromkeys(ids))


def _apply_attachment(db: OrmSession, g: Shift, body: ShiftBody,
                      act: Activity) -> None:
    """场次类型；培训/彩排再落下它要叫的岗位。

    培训/彩排挂在整个活动上，不设名额与候补：来不来由服务场次的报名决定。
    """
    if body.hours_mode not in HOURS_MODES:
        raise HTTPException(400, "时长认定方式不合法")
    if body.kind not in SHIFT_KINDS:
        raise HTTPException(400, "场次类型不合法")
    g.hours_mode = body.hours_mode
    g.kind = body.kind
    if body.kind == "service":
        g.requirement_tags = _clean_requirement_tags(body.requirement_tags, "场次")
        g.applies_to_positions = []
        return
    g.requirement_tags = {}
    g.applies_to_positions = _clean_applies(act, body.applies_to_positions)
    g.mode = "pick"          # 培训/彩排不选岗位，模式对它没有意义


def _resync_training(db: OrmSession, act: Activity, s: Shift) -> None:
    """培训/彩排新建或改了要叫的岗位：已经报上名的人按新的范围连带报上或退出，
    否则他们看不到这一场、也签不了到。"""
    db.flush()
    for enr in (db.query(Enrollment)
                .join(Shift, Enrollment.shift_id == Shift.id)
                .filter(Enrollment.activity_id == act.id,
                        Enrollment.status == "enrolled",
                        Shift.kind == "service").all()):
        logic.sync_attached(db, enr, [s])


def _prune_trainings(db: OrmSession, act: Activity, gone: set[str]) -> None:
    """岗位要删了：把它们从培训/彩排的范围里拿掉。拿掉之后范围空了的
    （只叫这几个岗位的培训）不能悄悄变成「全体」，先拦下让人自己处理。"""
    for s in act.trainings():
        left = [i for i in s.applies_to_positions or [] if i not in gone]
        if s.applies_to_positions and not left:
            raise HTTPException(
                409, f"{s.kind_label()}「{s.name}」只叫要删的这些岗位，"
                     f"请先删掉它或改一下要参加的岗位")
        if len(left) != len(s.applies_to_positions or []):
            s.applies_to_positions = left


def _sync_placeholder(db: OrmSession, g: Shift, capacity: int,
                      waitlist_quota: int) -> None:
    """「截止后统一分配」的场次自动带一个占位岗位，名额记在它头上。

    这样名额只有一个来源（岗位），报名、候补、签到、服装全都不必再为
    「没有岗位的报名」单开一套账。学生看到的是一个写明「暂不选岗位」的
    选项，不是一个真岗位。
    """
    if g.is_attached() or not g.needs_assign():
        return
    if capacity <= 0:
        raise HTTPException(400, "「截止后统一分配」的场次必须填写人数上限")
    holder = g.placeholder()
    if holder is None:
        holder = Position(id=uid("p"), shift_id=g.id, name=Position.PLACEHOLDER_NAME,
                          is_placeholder=True, sort=-1,
                          service_task="报名截止后由负责人统一分配岗位",
                          training_note="岗位确定后另行通知")
        db.add(holder)
        g.positions.append(holder)
    # 名额按整场数（见 enroll_logic.has_room）：已经分到真实岗位的人也算
    enrolled = logic.shift_enrolled_count(db, g.id) if g.id else 0
    if capacity < enrolled:
        raise HTTPException(400, f"人数上限不能小于已报名的 {enrolled} 人")
    holder.quota = capacity
    holder.waitlist_quota = waitlist_quota


def _prune_questions(db: OrmSession, act_id: str, s: Position,
                     successors: list[str] | None = None) -> None:
    """岗位要删了：把它从报名问卷题目的「适用岗位」里拿掉。

    发布后问卷不能改，不能指望管理者先去改题，所以这里直接修剪。题目还圈着
    别的岗位时只拿掉这一个；它是唯一的一个时原样留着：空范围的意思是「人人
    都答」，清空等于替管理者把范围放大到全体。留下的 id 和谁都对不上，这道题
    对谁都不显示（见 screening.visible_questions），和删掉整个场次时
    圈着那一场岗位的题一样。

    successors：「待分配」占位岗位退场时，这一场的报名改由这些真实岗位接着收，
    圈着「待分配」的题换成圈这些岗位——问的还是报这一场的人。
    """
    for q in (db.query(FormQuestion)
              .filter(FormQuestion.activity_id == act_id).all()):
        scope = q.applies_to_positions or []
        if s.id not in scope:
            continue
        left = [i for i in scope if i != s.id]
        left += [i for i in successors or [] if i not in left]
        if left:
            q.applies_to_positions = left


def _apply_shift_mode(db: OrmSession, g: Shift, mode: str) -> None:
    """切换场次模式：只在这一场一个人都没报（含候补）时允许。

    两种场次的人记在不同的账上：pick 的人各自占着自己选的岗位；assign 的人
    都从占位岗位进来、名额按整场数，分到真实岗位后仍算这一场的名额。有人
    之后再切，要么作废大家选好的岗位，要么让「还没安排的人」无处可放——
    建错模式的场次趁没人报的时候改，或者删了重建。

    「截止后统一分配」的场次不用切模式就能建岗位：直接建，再一键分配或导入。
    切到 pick 时占位岗位用不上了，删掉；它上面有过报名记录（报了又退的）
    就删不掉，也就不让切。
    """
    if mode not in SHIFT_MODES:
        raise HTTPException(400, "场次模式必须是 pick 或 assign")
    if mode == g.mode:
        return
    if g.id:
        rows = db.query(Enrollment).filter(Enrollment.shift_id == g.id)
        live = rows.filter(Enrollment.status.in_(logic.ACTIVE)).count()
        if live:
            raise HTTPException(
                409, f"「{g.name}」已有 {live} 人报名，不能再切换岗位模式；"
                     f"如确需调整请新建场次")
        holder = g.placeholder()
        if mode == "pick" and holder is not None:
            if rows.filter(Enrollment.position_id == holder.id).count():
                raise HTTPException(
                    409, f"「{g.name}」有过报名记录，不能再改成「报名即选岗位」；"
                         f"如确需调整请新建场次")
            _prune_questions(db, g.activity_id, holder,
                             [p.id for p in g.real_positions()])
            g.positions.remove(holder)
            db.delete(holder)
        if mode == "assign":
            # 暂停、锁定在 assign 场次里只按整场算（见 _toggle_target），
            # 岗位上留着的开关没有入口能关掉
            for p in g.positions:
                p.enroll_paused = p.list_locked = False
    g.mode = mode


@router.post("/activities/{activity_id}/shifts")
def create_shift(activity_id: str, body: ShiftBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _ensure_editing(db, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动不设场次与岗位，时长在审批通过后导入名单")
    periods = _clean_periods(body.periods)
    g = Shift(id=uid("g"), activity_id=act.id, name=body.name.strip(),
                      note=body.note.strip(), sort=len(act.shifts),
                      location=body.location.strip(), lock_hours=body.lock_hours,
                      flexible_time=bool(body.flexible_time) and body.kind == "service")
    _apply_shift_mode(db, g, body.mode)
    _apply_attachment(db, g, body, act)
    _validate_flexible_hours(g)
    db.add(g)
    _set_periods(db, g, periods)
    # 占位岗位要等场次落库拿到 id 之后再建
    _sync_placeholder(db, g, body.capacity, body.waitlist_quota)
    db.flush()
    if g.is_attached():
        summary = (f"新增{g.kind_label()}「{g.name}」"
                   f"（{g.span_text()}，{g.location or '地点待定'}，"
                   f"{applies_text(act, g)}参加，{g.hours_label()}）")
    else:
        summary = (f"新建场次「{g.name}」（{g.span_text()}，"
                   f"{g.mode_label()}，上限 {body.capacity} 人）")
    audit_log(db, actor=user, action="create", target_type="group",
              target_id=g.id, target_label=f"{act.title}·{g.name}",
              activity_id=act.id, summary=summary, visible=True)
    if g.is_attached():
        _resync_training(db, act, g)
    db.commit()
    return {"id": g.id}


# 名额与候补不在这张表里：它们记在占位岗位上，由 _sync_placeholder 处理
SHIFT_FIELDS = {"name": "场次名",
                "mode": "岗位模式", "note": "备注",
                "location": "地点", "hours_mode": "时长认定",
                "requirement_tags": "条件要求", "lock_hours": "开场前自动锁定（小时）",
                "flexible_time": "时间灵活（可多次签到）"}
SHIFT_LABELS = {**SHIFT_FIELDS, "periods": "时间"}
# 库里 name/location 是 String(n)、note 是 String(512)：修改是 PATCH 任意字段，
# 超了直接写库会撞列宽 500
_SHIFT_MAX = {"name": 128, "location": 128, "note": 512}


@router.patch("/shifts/{shift_id}")
def update_shift(shift_id: str, body: dict, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    before_raw = {k: getattr(g, k) for k in SHIFT_FIELDS}
    before_periods = g.span_text()
    new_periods = (_clean_periods([PeriodBody(**x) for x in body["periods"]])
                   if "periods" in body else None)
    # 锁定时刻跟着 N 和开场时间走，两者真变了才重算：先按改之前的时刻把锁定
    # 状态落实下来，已经锁上的不会因为锁定点往后挪而自己解开；新锁定点已过的
    # 立即锁定（见 logic.set_lock_hours）。只改了名字之类的不碰，不然手动解锁过的
    # 场次一保存又被锁回去
    new_hours = (_clean_lock_hours(body["lock_hours"]) if "lock_hours" in body
                 else g.lock_hours)
    new_start = new_periods[0]["start_time"] if new_periods else g.start_time
    if new_hours != g.lock_hours or new_start != g.start_time:
        logic.set_lock_hours(g, new_hours)
    if new_periods is not None:
        _set_periods(db, g, new_periods)
    for key in SHIFT_FIELDS:
        if key not in body or key == "lock_hours":
            continue
        v = body[key]
        if key == "mode":
            _apply_shift_mode(db, g, v)
        elif key == "hours_mode":
            if v not in HOURS_MODES:
                raise HTTPException(400, "时长认定方式不合法")
            g.hours_mode = v
        elif key == "requirement_tags":
            g.requirement_tags = _clean_requirement_tags(v, "场次")
        elif key == "flexible_time":
            g.flexible_time = bool(v) and not g.is_attached()
        else:
            if key == "name" and not str(v or "").strip():
                raise HTTPException(400, "场次名不能为空")
            limit = _SHIFT_MAX.get(key)
            if limit and len(str(v or "")) > limit:
                raise HTTPException(400, f"「{SHIFT_FIELDS[key]}」不能超过 {limit} 个字")
            setattr(g, key, str(v or "") if key == "location" else v)
    if "applies_to_positions" in body and g.is_attached():
        g.applies_to_positions = _clean_applies(act, body["applies_to_positions"])
        _resync_training(db, act, g)
    _validate_flexible_hours(g)
    # 名额与候补落到占位岗位上；没传就沿用它当前的值。非整数直接 int() 会抛
    # ValueError，管理者看到的是 500，所以先过一道校验
    holder = g.placeholder()
    capacity = (_as_int(body["capacity"], "人数上限") if "capacity" in body
                else (holder.quota if holder else 0))
    waitlist_quota = (_as_int(body["waitlist_quota"], "候补名额")
                      if "waitlist_quota" in body
                      else (holder.waitlist_quota if holder else 3))
    if capacity < 0 or waitlist_quota < 0:
        raise HTTPException(400, "人数上限与候补名额不能是负数")
    _sync_placeholder(db, g, capacity, waitlist_quota)

    def fmt(v):
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M")
        if isinstance(v, bool):
            return "是" if v else "否"
        return v
    changes = diff_changes(
        {"periods": before_periods,
         **{k: fmt(v) for k, v in before_raw.items()}},
        {"periods": g.span_text(),
         **{k: fmt(getattr(g, k)) for k in SHIFT_FIELDS}},
        SHIFT_LABELS)
    audit_log(db, actor=user, action="update", target_type="group",
              target_id=g.id, target_label=f"{act.title}·{g.name}",
              activity_id=act.id,
              summary=f"修改场次「{g.name}」："
                      f"{'、'.join(c['label'] for c in changes) or '无实质变化'}",
              changes=changes,
              visible=True)
    db.commit()
    return {"ok": True, "changes": changes}


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: str, force: int = 0, bg: BackgroundTasks = None,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    # 签到记录挂在场次上（checkin_records / checkin_geos 都随签到任务走），
    # 场次没了它们没处安放：生成过签到码的场次一律不删，签到历史不能跟着没
    if db.query(CheckinTask).filter(CheckinTask.shift_id == g.id).first():
        raise HTTPException(
            409, "该场次已经生成过签到码，签到记录挂在场次上，不能删除。"
                 "如确定这一场不作数，请在签到面板停用签到码，场次保留即可")
    # 这一场的培训/彩排先查出来再修剪范围：修剪完它们就不再圈这一场的岗位，
    # attached_shifts 查不回来，下面重算时只因这一场才去的那些就退不掉了
    attached = [] if g.is_attached() else logic.attached_shifts(db, g.id)
    if not g.is_attached():
        _prune_trainings(db, act, {p.id for p in g.positions})
    n_active = db.query(Enrollment).filter(
        Enrollment.shift_id == g.id,
        Enrollment.status.in_(logic.ACTIVE)).count()
    if n_active and not force:
        raise HTTPException(409, f"该场次已有 {n_active} 人有效报名，"
                                 f"删除需强制执行（force=1），全部记录将被取消")
    db.flush()
    # 报名记录挂在场次和它的岗位上，场次删掉它们也留不住（外键），连同已经
    # 退选的一起删。还有效的那些人记进留痕：保存并发布时按人汇总的变更通知
    # 从库里已经查不到他们，只能靠这份名单告诉他们这一场没了。
    # 他们的培训/彩排按剩下的正式报名重新算：只因为这一场才去的培训一起退掉
    rows = db.query(Enrollment).filter(Enrollment.shift_id == g.id).all()
    affected = [e.user_id for e in rows if e.status in logic.ACTIVE]
    for e in rows:
        was_on = e.status == "enrolled"
        e.status = "cancelled"
        if was_on and not g.is_attached():
            logic.sync_attached(db, e, attached)
        db.delete(e)
    db.flush()
    label = f"{act.title}·{g.name}"
    db.delete(g)
    audit_log(db, actor=user, action="delete", target_type="group",
              target_id=g.id, target_label=label, activity_id=act.id,
              summary=f"删除场次「{g.name}」" +
                      (f"（；含 {n_active} 条报名记录）" if n_active else ""),
              visible=True, affected=affected)
    db.commit()
    return {"ok": True}


# ======================= 岗位 =======================

class PositionBody(BaseModel):
    """岗位没有时间——时间随场次。名额、候补、条件、组长都在这一层。"""
    # 带 max_length 的几项对着库里 String(n) 的长度：不拦下来就是插入时报错，
    # 管理者看到的是一个 500。service_task 这些是 Text，不限
    name: str = Field(max_length=128)
    location: str = Field(default="", max_length=128)
    quota: int = Field(gt=0)
    waitlist_quota: int = Field(default=3, ge=0)
    group_name: str = Field(default="", max_length=64)
    requirement_tags: dict = {}
    service_task: str = ""
    position_requirement: str = ""       # 选填
    training_note: str = ""
    needs_leader: bool = False
    # 需要组长时必填：组长要带队做什么
    leader_task: str = Field(default="", max_length=256)
    attire: str = "none"                 # none / issued / own
    # 统一发放或自备时必填
    attire_note: str = Field(default="", max_length=256)
    pay_optional: bool = False           # 可选劳务
    # 可选劳务时必填：标准与发放方式
    pay_note: str = Field(default="", max_length=256)
    copy_from: str = ""                  # 复制自哪个岗位：学习材料跟着带过来


POSITION_FIELDS = {"name": "岗位名", "location": "地点", "quota": "名额",
                   "group_name": "岗位组",
                   "waitlist_quota": "候补名额", "needs_leader": "需要组长",
                   "leader_task": "组长任务",
                   "attire": "志愿服装", "attire_note": "服装备注",
                   "pay_optional": "可选劳务", "pay_note": "劳务备注",
                   "requirement_tags": "条件要求",
                   "service_task": "服务任务", "position_requirement": "岗位要求",
                   "training_note": "培训内容"}


def _ensure_editing(db: OrmSession, activity_id: str) -> Activity:
    """改场次/岗位之前，活动必须处于编辑态。

    没发布的活动（草稿、待审批、待发布）随便改——还没公开，没人报名。
    已发布之后每一次改动都可能
    让学生看到半成品：名额改到一半、岗位删了又建。所以强制走
    「调整岗位 → 改 → 保存并发布」这条路，改动期间报名暂停，
    保存时把这批改动一次性通知到人。
    """
    act = _act(db, activity_id)
    ensure_not_finished(act)
    if not act.is_unpublished() and act.editing_since is None:
        raise HTTPException(
            409, "请先点击右上角「调整岗位（暂停报名）」，再修改场次与岗位")
    return act


# 库里这几列是 String(n)：超了要到插入那一刻才报错，管理者看到的是一个 500。
# 新建走 PositionBody 的 max_length，改岗位是 PATCH 任意字段，所以这里再拦一道
_POSITION_MAX = {"name": 128, "location": 128, "group_name": 64,
                 "leader_task": 256, "attire_note": 256, "pay_note": 256}


def _sync_group_leader_task(db: OrmSession, s: Position) -> int:
    """岗位组共用一名组长，组长任务也只有一份：把它对齐到全组。

    这个岗位没写就沿用组里已有的那份，写了就覆盖全组。否则同组各写一份，
    任命通知只念得到其中一个，另几份白写。不分组的岗位自成一组，不用管。
    返回跟着改了几个岗位。
    """
    if not (s.group_name or "").strip():
        return 0
    siblings = [p for p in logic.group_positions(db, s) if p.id != s.id]
    if not siblings:
        return 0
    task = (s.leader_task or "").strip()
    if not task:
        s.leader_task = logic.group_leader_task(siblings)
        return 0
    changed = [p for p in siblings if (p.leader_task or "") != task]
    for p in changed:
        p.leader_task = task
    return len(changed)


def _require_position_text(s: Position) -> None:
    """服务任务是志愿者到岗前唯一必看的一句话；地点留空用活动地点，
    培训内容可以之后再补。"""
    for key, limit in _POSITION_MAX.items():
        if len(getattr(s, key) or "") > limit:
            raise HTTPException(400, f"「{POSITION_FIELDS[key]}」最多 {limit} 个字")
    if not (s.service_task or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的服务任务")
    if s.attire not in Position.ATTIRE_LABELS:
        raise HTTPException(400, "志愿服装只能是 不需要 / 统一发放 / 志愿者自备")
    if s.attire != "none" and not (s.attire_note or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的服装备注"
                                 "（领取与归还方式，或自备的样式要求）")
    if s.pay_optional and not (s.pay_note or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的劳务备注"
                                 "（标准与发放方式）")
    if s.needs_leader and not (s.leader_task or "").strip():
        raise HTTPException(400, f"请填写岗位「{s.name}」的组长任务"
                                 "（组长要带队做什么，任命时告诉他）")


def _validate_position(db: OrmSession, g: Shift, s: Position):
    """岗位自带名额，不受场次总数约束——场次只管时间。

    「截止后统一分配」场次里的真实岗位也一样：它们是分配用的坑位，名额之和
    可以多于招募人数（有的岗位排不满），也可以少（排不下的留在「待分配」）。
    """
    if s.quota <= 0:
        raise HTTPException(400, "岗位名额必须大于 0")
    if s.waitlist_quota < 0:
        raise HTTPException(400, "候补名额不能是负数")
    # 同上：只跟已报名的真人比。空着没填的位子不该挡住与名额无关的修改
    enrolled = logic.position_enrolled_count(db, s.id) if s.id else 0
    if s.quota < enrolled:
        raise HTTPException(400, f"岗位名额不能小于已报名的 {enrolled} 人")


@router.post("/shifts/{shift_id}/positions")
def create_position(shift_id: str, body: PositionBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    source = db.get(Position, body.copy_from) if body.copy_from else None
    if source is not None and source.shift.activity_id != g.activity_id:
        raise HTTPException(400, "只能复制本活动的岗位")
    s = Position(id=uid("s"), shift_id=g.id, name=body.name.strip(),
                location=body.location.strip(), quota=body.quota,
                waitlist_quota=body.waitlist_quota,
                needs_leader=body.needs_leader,
                leader_task=body.leader_task.strip(),
                attire=body.attire, attire_note=body.attire_note.strip(),
                pay_optional=body.pay_optional, pay_note=body.pay_note.strip(),
                group_name=body.group_name.strip(),
                requirement_tags=_clean_requirement_tags(body.requirement_tags, "岗位"),
                service_task=body.service_task.strip(),
                position_requirement=body.position_requirement.strip(),
                training_note=body.training_note.strip(),
                sort=len(g.positions))
    _validate_position(db, g, s)
    # 加进已有的岗位组时，没写组长任务就沿用这个组已有的那份
    _sync_group_leader_task(db, s)
    _require_position_text(s)
    db.add(s)
    db.flush()   # 让 group 关系可用，供 time_text() 使用
    copied = (copying.copy_attachments(db, "position", source.id, s.id,
                                       act.id, user) if source else 0)
    # 复制出来的是同一个岗位的另一个时段（「按岗位看」里的加时段）：叫原岗位
    # 的培训/彩排也叫它
    if source is not None:
        for t in act.trainings():
            if source.id in (t.applies_to_positions or []):
                t.applies_to_positions = [*t.applies_to_positions, s.id]
    audit_log(db, actor=user, action="create", target_type="position",
              target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
              activity_id=act.id,
              summary=f"新建岗位「{s.name}」（{s.time_text()}，"
                      f"{s.location or act.location or '地点待定'}，"
                      f"{s.quota} 人）"
                      + (f"（；复制自「{source.name}」"
                         + (f"，含 {copied} 份学习材料" if copied else "")
                         + "）"
                         if source else ""),
              visible=True)
    db.commit()
    return {"id": s.id}


@router.patch("/positions/{position_id}")
def update_position(position_id: str, body: dict, bg: BackgroundTasks,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _shift(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    before_raw = {k: getattr(s, k) for k in POSITION_FIELDS}
    for key in POSITION_FIELDS:
        if key not in body:
            continue
        if key == "requirement_tags":
            s.requirement_tags = _clean_requirement_tags(body[key], "岗位")
        elif key in ("quota", "waitlist_quota"):
            # 名额填错（小数、文字、布尔）直接写库或比较时，要么报 500 要么
            # 悄悄算出个怪结果，统一在这里换成 400
            setattr(s, key, _as_int(body[key], POSITION_FIELDS[key]))
        else:
            value = body[key]
            limit = _POSITION_MAX.get(key)
            if limit and len(str(value or "")) > limit:
                raise HTTPException(400, f"「{POSITION_FIELDS[key]}」最多 {limit} 个字")
            setattr(s, key, value)
    _validate_position(db, g, s)
    synced = _sync_group_leader_task(db, s)
    _require_position_text(s)

    # 组长开关变化要落到人身上：关掉了还留着组长标记，导出表和大屏就会
    # 显示一个「不存在的岗位角色」；打开了则提醒管理者去指定一位。
    if s.needs_leader != before_raw["needs_leader"]:
        if s.needs_leader:
            db.flush()
            logic.notify_leader_vacancy(db, s)
        else:
            (db.query(Enrollment)
             .filter(Enrollment.position_id == s.id,
                     Enrollment.is_leader.is_(True))
             .update({"is_leader": False}, synchronize_session=False))

    def fmt(k, v):
        if isinstance(v, datetime):
            return v.strftime("%Y-%m-%d %H:%M")
        if k == "attire":
            return Position.ATTIRE_LABELS.get(v, v)
        return v
    changes = diff_changes({k: fmt(k, v) for k, v in before_raw.items()},
                           {k: fmt(k, getattr(s, k)) for k in POSITION_FIELDS},
                           POSITION_FIELDS)
    audit_log(db, actor=user, action="update", target_type="position",
              target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
              activity_id=act.id,
              summary=f"修改岗位「{s.name}」："
                      f"{'、'.join(c['label'] for c in changes) or '无实质变化'}"
                      + (f"（；组长任务已同步到同组另 {synced} 个岗位）"
                         if synced else ""),
              changes=changes,
              # 组长任务改的是「这一组」的事，留痕却挂在被编辑的那个岗位上
              # （兄弟岗位是静默同步的）。组长可能正好在兄弟岗位上，按岗位发
              # 他就收不到，把他的 id 记进 affected，通知那一层照发
              affected=([lead.user_id]
                        if synced and (lead := logic.group_leader(db, s)) else None),
              visible=True)
    db.commit()
    if (s.quota, s.waitlist_quota) != (before_raw["quota"],
                                       before_raw["waitlist_quota"]):
        # 名额在闸门之外变了：作废计数器，下一个报名按数据库重新对齐。
        # 不作废的话，加了名额之后要等核库限流窗口过去才报得上
        invalidate_gates(g.id, s.id)
    return {"ok": True, "changes": changes}


class PauseBody(BaseModel):
    paused: bool


class LockBody(BaseModel):
    locked: bool


def _pause_target_check(act: Activity) -> None:
    """暂停、锁定不用进编辑态：它们只管收不收新报名、候补顶不顶上，不改任何人的安排。"""
    ensure_not_finished(act)
    if act.is_record():
        raise HTTPException(409, "补记录活动不接受报名")


def _toggle_target(db: OrmSession, user: User, shift_id: str | None = None,
                   position_id: str | None = None) -> tuple[Activity, Shift, Position | None]:
    """暂停、锁定接口共用的取对象与校验。岗位接口传 position_id。"""
    s = None
    if position_id is not None:
        s = db.get(Position, position_id)
        if s is None:
            raise HTTPException(404, "岗位不存在")
        shift_id = s.shift_id
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _act(db, g.activity_id)
    _pause_target_check(act)
    if g.is_attached():
        raise HTTPException(400, "培训/彩排跟着服务场次走，没有单独的报名")
    # 「截止后统一分配」的场次报名、候补都是整场一条队（排在「待分配」上），
    # 真实岗位上只有分过去的人：单锁一个岗位，空出的位子照样被整场的候补顶上，
    # 和「锁定后候补不自动转正」对不上
    if s is not None and g.needs_assign():
        raise HTTPException(400, "「截止后统一分配」的场次报名和候补都是整场一条队，"
                                 "请对整个场次暂停或锁定")
    return act, g, s


def _reopened(db: OrmSession, g: Shift, s: Position | None) -> dict:
    """解除暂停或锁定之后：两者都解除了就按候补顺序补齐空位；还有一项没解除，
    候补就不动，有人在排队时提醒管理者为什么没人顶上。

    调用方持有场次锁，补位和状态变化在同一个事务里提交。
    """
    if not logic.promotion_blocked(g, s):
        return {"promoted": logic.fill_vacancies(db, g), "hint": ""}
    queue = logic.waitlist_rows(db, g.id)
    if s is not None:
        queue = [e for e in queue if e.position_id == s.id]
    if not any(e.status == "waitlisted" for e in queue):
        return {"promoted": 0, "hint": ""}
    if logic.shift_locked(g):
        why = "这一场仍在锁定中"
    elif g.enroll_paused:
        why = "这一场仍在暂停中"
    else:
        why = f"岗位「{s.name}」仍在{'锁定' if s.list_locked else '暂停'}中"
    return {"promoted": 0,
            "hint": f"{why}，候补要等暂停和锁定都解除后才会补位。"}


def _toggle_done(db: OrmSession, user: User, act: Activity, g: Shift,
                 s: Position | None, reopened: bool, summary: str) -> dict:
    """留痕、补位、提交、作废闸门计数。场次锁随 commit 放掉。"""
    if s is None:
        audit_log(db, actor=user, action="update", target_type="group",
                  target_id=g.id, target_label=f"{act.title}·{g.name}",
                  activity_id=act.id, summary=summary)
    else:
        audit_log(db, actor=user, action="update", target_type="position",
                  target_id=s.id, target_label=f"{act.title}·{g.name}·{s.name}",
                  activity_id=act.id, summary=summary)
    db.flush()
    out = _reopened(db, g, s) if reopened else {"promoted": 0, "hint": ""}
    db.commit()
    for p in ([s] if s is not None else g.positions):
        invalidate_gates(g.id, p.id)
    return out


@router.post("/shifts/{shift_id}/pause")
def pause_shift(shift_id: str, body: PauseBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """暂停 / 恢复一个场次的报名（含它下面所有岗位）。

    暂停用来调整岗位：不收新报名、新候补，候补也不自动顶上，期间名单不动；
    已报的人可以退出，不记录。恢复时按候补顺序把空位补齐。
    """
    act, g, _ = _toggle_target(db, user, shift_id=shift_id)
    db.commit()          # 还连接再排锁，理由同 enroll
    with shift_lock(db, g.id):
        logic.reload_shift(db, g)
        g.enroll_paused = body.paused
        out = _toggle_done(db, user, act, g, None, not body.paused,
                           f"{'暂停' if body.paused else '恢复'}场次「{g.name}」的报名")
    return {"ok": True, "paused": g.enroll_paused, **out}


@router.post("/positions/{position_id}/pause")
def pause_position(position_id: str, body: PauseBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """暂停 / 恢复一个岗位的报名，口径同 pause_shift。"""
    act, g, s = _toggle_target(db, user, position_id=position_id)
    db.commit()
    with shift_lock(db, g.id):
        logic.reload_shift(db, g)
        s.enroll_paused = body.paused
        out = _toggle_done(db, user, act, g, s, not body.paused,
                           f"{'暂停' if body.paused else '恢复'}岗位「{s.name}」的报名")
    return {"ok": True, "paused": s.enroll_paused, **out}


@router.post("/shifts/{shift_id}/lock")
def lock_shift(shift_id: str, body: LockBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """手动锁定 / 解锁一个场次的名单。

    锁定后名单视为基本敲定：不收新报名、新候补，候补不自动顶上（管理者手动
    转正），此时退出的志愿者记一次「锁定后退出」。解锁时按候补顺序补齐空位；
    报名没截止的话，同时重新收新报名和新候补。不进编辑态、不发通知。
    """
    act, g, _ = _toggle_target(db, user, shift_id=shift_id)
    db.commit()
    with shift_lock(db, g.id):
        logic.reload_shift(db, g)
        logic.set_shift_locked(g, body.locked)
        out = _toggle_done(db, user, act, g, None, not body.locked,
                           f"{'锁定' if body.locked else '解锁'}场次「{g.name}」的名单")
    return {"ok": True, "locked": logic.shift_locked(g), **out}


@router.post("/positions/{position_id}/lock")
def lock_position(position_id: str, body: LockBody,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """手动锁定 / 解锁一个岗位的名单，口径同 lock_shift。岗位没有自动锁定。"""
    act, g, s = _toggle_target(db, user, position_id=position_id)
    db.commit()
    with shift_lock(db, g.id):
        logic.reload_shift(db, g)
        s.list_locked = body.locked
        out = _toggle_done(db, user, act, g, s, not body.locked,
                           f"{'锁定' if body.locked else '解锁'}岗位「{s.name}」的名单")
    return {"ok": True, "locked": s.list_locked, **out}


def _holder_occupants(db: OrmSession, holder: Position) -> str:
    """「待分配」上还挂着的人，拼成一句提示；没人返回空串。

    口径同 _apply_shift_mode 判 live：ACTIVE。候补、待确认、
    待筛选在 assign 场次里都排在占位岗位上，一键分配只挪正式报名的人，
    他们得先由负责人处理掉，所以单独点出来。
    """
    on_holder = db.query(Enrollment).filter(Enrollment.position_id == holder.id)
    counts = dict(on_holder.filter(Enrollment.status.in_(logic.ACTIVE))
                  .with_entities(Enrollment.status, func.count())
                  .group_by(Enrollment.status).all())
    parts = []
    if counts.get("enrolled"):
        parts.append(f"{counts['enrolled']} 人还没分到岗位，先一键分配或手动调岗")
    waiting = counts.get("waitlisted", 0) + counts.get("pending_confirm", 0)
    if waiting:
        parts.append(f"{waiting} 人在候补（含待确认转正），先手动转正、调岗或等候补处理完")
    if counts.get("applied"):
        parts.append(f"{counts['applied']} 人待筛选，先录取或不录取")
    return "；".join(parts)


@router.post("/shifts/{shift_id}/open-pick")
def open_pick(shift_id: str, db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """「截止后统一分配」的场次开放选岗：转成「报名即选岗位」，之后按各岗位名额自选。

    条件：已建了真实岗位，且「待分配」里一个人都不挂（正式、候补、待确认、
    待筛选都算）。这时这一场的每个人都已经站在某个真实岗位上，
    转成 pick 之后他们各自占着自己岗位的名额，账一笔不乱；剩下的空位
    就是各岗位的「名额 − 已占」，新来的人自己挑。

    和暂停报名一样不用进编辑态：它不改任何已报名的人的安排，只改新来的人
    从哪个口子进。判定和改动都在场次锁里做，锁外读到的状态只用来早拒——
    不在锁里再核一遍的话，判完「待分配没人」到删掉占位之间，可能正好有人
    报进了「待分配」。
    """
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _act(db, g.activity_id)
    _pause_target_check(act)
    if g.is_attached():
        raise HTTPException(409, "培训/彩排跟着服务场次走，没有岗位模式")
    db.commit()          # 还连接再排锁，理由同 enroll
    with shift_lock(db, g.id):
        db.refresh(g)
        db.expire(g, ["positions"])
        if not g.needs_assign():
            raise HTTPException(409, f"「{g.name}」已经是「报名即选岗位」，不用再开放选岗")
        real = g.real_positions()
        if not real:
            raise HTTPException(409, f"「{g.name}」还没建岗位：先在这一场下建好岗位，"
                                     f"再开放选岗")
        holder = g.placeholder()
        if holder is not None:
            busy = _holder_occupants(db, holder)
            if busy:
                raise HTTPException(409, f"「待分配」里还有人：{busy}")
            if db.query(CheckinTask).filter(
                    CheckinTask.position_id == holder.id).count():
                raise HTTPException(409, "「待分配」上开过分岗位签到码，"
                                         "先在签到里删掉它再开放选岗")
            _prune_questions(db, act.id, holder, [p.id for p in real])
            _prune_trainings(db, act, {holder.id})
            # 报过「待分配」又退掉（或落选）的历史记录还指着它，外键挡着删不掉。
            # 这些人本来就没分到岗位，岗位一栏该是空的：把 position_id 置空，
            # 记录本身（谁、何时报、何时退、怎么退）原样留着。
            # 只动已经结束的那些：问卷申请那条路不拿场次锁，上面判完「待分配没人」
            # 到这里之间可能正好有人报进来，不加状态条件就会把一条待筛选的报名
            # 静默清掉。清完再核一遍，多出来的人宁可让他重试
            (db.query(Enrollment)
             .filter(Enrollment.position_id == holder.id,
                     Enrollment.status.notin_(logic.ACTIVE))
             .update({"position_id": None}, synchronize_session=False))
            if db.query(Enrollment).filter(
                    Enrollment.position_id == holder.id).count():
                raise HTTPException(409, "「待分配」里刚有人报了名，请稍后重试")
            g.positions.remove(holder)
            db.delete(holder)
        g.mode = "pick"
        audit_log(db, actor=user, action="update", target_type="group",
                  target_id=g.id, target_label=f"{act.title}·{g.name}",
                  activity_id=act.id,
                  summary=f"场次「{g.name}」开放选岗：改为「报名即选岗位」，"
                          f"按 {len(real)} 个岗位的名额自选",
                  changes=[{"field": "mode", "label": "岗位模式",
                            "old": Shift.MODE_LABELS["assign"],
                            "new": Shift.MODE_LABELS["pick"]}],
                  visible=True)
        db.commit()
    # 名额闸门按（场次, 岗位）计数：assign 时闸门挂在占位岗位上，转成 pick
    # 后每个真实岗位各开一个。全部作废，下一个报名按数据库重新对齐
    if holder is not None:
        invalidate_gates(g.id, holder.id)
    for p in real:
        invalidate_gates(g.id, p.id)
    return {"ok": True, "mode": g.mode}


@router.delete("/positions/{position_id}")
def delete_position(position_id: str,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    s = db.get(Position, position_id)
    if s is None:
        raise HTTPException(404, "岗位不存在")
    g = _shift(db, s.shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _ensure_editing(db, g.activity_id)
    if s.is_placeholder:
        raise HTTPException(409, "「待分配」是这一场报名的入口，不能删；"
                                 "名额改在场次的「人数上限」里")
    # 报名和签到记录都挂在这个岗位上（enrollments.position_id、
    # checkin_tasks.position_id 都是外键）：删了它们没处安放。
    # 有人已报名先让他把人调走；退选、未录取这类历史记录也照样挡着——
    # 岗位就只能保留，不再用的话暂停报名即可
    n_enrolled = db.query(Enrollment).filter(
        Enrollment.position_id == s.id,
        Enrollment.status == "enrolled").count()
    if n_enrolled:
        raise HTTPException(409, f"该岗位仍有 {n_enrolled} 人已报名，"
                                 "请先在分配面板调整")
    n_ref = db.query(Enrollment).filter(Enrollment.position_id == s.id).count()
    n_task = db.query(CheckinTask).filter(CheckinTask.position_id == s.id).count()
    if n_ref or n_task:
        parts = []
        if n_ref:
            parts.append(f"{n_ref} 条报名历史（含已退选/未录取的）")
        if n_task:
            parts.append(f"{n_task} 个签到码")
        raise HTTPException(409, f"该岗位还挂着{'、'.join(parts)}，"
                                 "删除会把这些记录一起弄丢，不能删；"
                                 "不再用的话暂停报名即可")
    _prune_questions(db, act.id, s)
    _prune_trainings(db, act, {s.id})
    label = f"{act.title}·{g.name}·{s.name}"
    db.delete(s)
    audit_log(db, actor=user, action="delete", target_type="position",
              target_id=s.id, target_label=label, activity_id=act.id,
              summary=f"删除岗位「{s.name}」",
              visible=True)
    db.commit()
    return {"ok": True}


# ======================= 活动管理者 =======================

@router.get("/activities/{activity_id}/managers")
def list_managers(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(ActivityManager)
            .filter(ActivityManager.activity_id == activity_id).all())
    duties = {r.user_id: r.duties or [] for r in rows}
    users = [db.get(User, i) for i in activity_manager_ids(db, activity_id)]
    return {"duties": ActivityManager.DUTIES, "items": [{
        "id": u.id, "name": u.name, "student_id": u.student_id,
        "college": u.college, "roles": u.roles or [],
        "role_label": u.active_role_label(),
        # 名单上的人（可能含当前用户本人）按真实身份标
        "post_label": u.post_label() if u.post(real=True) else "",
        # 部门成员在名单里只能看：界面上要标出来，别让人以为多了个能干活的
        "is_trainee": u.is_trainee(real=True),
        "is_creator": u.id == act.created_by,
        # 管理员天然是管理者，名单里没有他那一行，也就没有分工可言
        "duties": duties.get(u.id, []),
    } for u in users if u]}


@router.get("/activities/{activity_id}/manager-candidates")
def manager_candidates(activity_id: str, db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """能被加进共管名单的人，按层级分组。

    共管名单十有八九加的是同一批人：会长团、各部门部长、活动归口部门的骨干、
    自己手下的骨干与成员。
    每次都去问学工号再手输一遍太笨。名单只列**已经有 活动发起方/青协 身份**
    的人——列出来点了才被后端拒，等于白列。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    already = set(activity_manager_ids(db, activity_id))
    groups: list[dict] = []

    def add(label: str, rows: list[User]) -> None:
        picked = [u for u in rows
                  if u.id not in already and u.is_manager_identity(real=True)]
        if picked:
            groups.append({"label": label, "users": [{
                "id": u.id, "name": u.name, "student_id": u.student_id,
                "college": u.college,
                "post_label": u.post_label() if u.post(real=True) else "",
            } for u in sorted(picked, key=lambda x: x.student_id)]})

    # 候选人按真实身份；「我是部长还是骨干」（下面的 user.post()）跟当前身份
    qingxie = [u for u in db.query(User).all() if u.has_role("qingxie", real=True)]
    add("会长团", [u for u in qingxie if u.qingxie_post == "council"])
    add("各部门部长", [u for u in qingxie if u.qingxie_post == "head"])
    # 活动归口部门的骨干：谁来管这个活动都看得到。归口没定（草稿还没提交
    # 审批）时按操作人自己的部门；两样都没有就不出这一组
    own_dept = user.department_id if user.post() else None
    dept_id = act.approval_dept_id or own_dept
    act_dept = db.get(Department, dept_id) if dept_id else None
    if act_dept is not None:
        add(f"{act_dept.name}骨干",
            [u for u in qingxie if u.department_id == act_dept.id
             and u.qingxie_post == "core"])
    # 下面几组按操作人自己的部门
    if user.post() in ("head", "core") and own_dept:
        same = [u for u in qingxie if u.department_id == own_dept]
        if user.post() == "head":
            # 归口就是本部门时，这批骨干上面已经列过
            if dept_id != own_dept:
                add("本部门骨干", [u for u in same if u.qingxie_post == "core"])
            add("本部门成员", [u for u in same if u.qingxie_post == "member"])
        else:
            add("我管理的成员", [u for u in same
                                if u.qingxie_post == "member"
                                and u.supervisor_id == user.id])
    return {"groups": groups}


class ManagerBody(BaseModel):
    student_id: str


class DutiesBody(BaseModel):
    duties: list[str]


@router.post("/activities/{activity_id}/managers")
def add_activity_manager(activity_id: str, body: ManagerBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    target = db.query(User).filter(
        User.student_id == body.student_id.strip()).first()
    if target is None:
        raise HTTPException(404, f"学号 {body.student_id} 不存在")
    row = add_manager(db, activity_id, target, user)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"添加活动管理者：{target.name}（{target.student_id}）")
    db.commit()
    return {"ok": True, "id": row.id}


@router.patch("/activities/{activity_id}/managers/{user_id}/duties")
def set_manager_duties(activity_id: str, user_id: str, body: DutiesBody,
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """给某位管理者分工。只影响通知发给谁，不限制他能做什么。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    row = (db.query(ActivityManager)
           .filter(ActivityManager.activity_id == activity_id,
                   ActivityManager.user_id == user_id).first())
    if row is None:
        raise HTTPException(404, "该用户不在本活动的管理者名单中")
    unknown = [d for d in body.duties if d not in ActivityManager.DUTIES]
    if unknown:
        raise HTTPException(400, f"未知的分工：{'、'.join(unknown)}")
    row.duties = list(dict.fromkeys(body.duties))
    target = db.get(User, user_id)
    labels = "、".join(ActivityManager.DUTIES[d] for d in row.duties)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"{target.name if target else user_id} 的分工改为"
                      f"{labels or '全部通知'}")
    db.commit()
    return {"ok": True, "duties": row.duties}


@router.delete("/activities/{activity_id}/managers/{user_id}")
def remove_activity_manager(activity_id: str, user_id: str,
                            db: OrmSession = Depends(db_session),
                            user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if user_id == user.id:
        raise HTTPException(400, "不能移除自己的管理权限；"
                                 "如需退出管理，请由其他管理者操作")
    target = db.get(User, user_id)
    remove_manager(db, activity_id, user_id, act.created_by)
    audit_log(db, actor=user, action="update", target_type="activity",
              target_id=activity_id, target_label=act.title,
              activity_id=activity_id,
              summary=f"移除活动管理者：{target.name if target else user_id}")
    db.commit()
    return {"ok": True}


# ======================= 志愿者通知 =======================

class NotifyBody(BaseModel):
    scope: str = "all"      # all 全部志愿者 / group 指定场次 / not_checked_in 场次未到岗
    shift_id: str = ""
    title: str = Field(max_length=128)
    content: str
    need_ack: bool = False  # 要求志愿者点「我已知悉」
    file_ids: list[str] = Field(default_factory=list, max_length=5)  # 先经 notify-files 上传


def _notify_files(db: OrmSession, act: Activity, ids: list[str]) -> list[dict]:
    """发通知带的附件：只认本活动经 notify-files 传上来的文件。

    传上来时 owner_id 是活动 id，随通知发出后改成这次发送的编号；定时任务
    按这个区分哪些传了没发（见 scheduled.purge_unsent_notify_files）。
    """
    send_id = uid("snd")
    out = []
    for i in dict.fromkeys(ids):
        a = db.get(Attachment, i)
        if a is None or a.owner_type != "notify" or a.activity_id != act.id:
            raise HTTPException(400, "附件不存在，请重新上传")
        a.owner_id = send_id
        out.append({"id": a.id, "title": a.title, "size": a.size})
    return out


@router.post("/activities/{activity_id}/notify")
def notify_volunteers(activity_id: str, body: NotifyBody,
                      db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """向该活动 status=enrolled 的报名者发送自定义通知。

    scope=group：再按 shift_id 过滤；scope=not_checked_in：按 shift_id 过滤
    且排除已在该场次有签到记录的人。Mock 模式下消息落 notifications 表。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    title, content = body.title.strip(), body.content.strip()
    if not title:
        raise HTTPException(400, "请填写通知标题")
    if not content:
        raise HTTPException(400, "请填写通知内容")
    if body.scope not in ("all", "group", "not_checked_in"):
        raise HTTPException(400, "通知范围不合法")
    shift_id = ""
    if body.scope != "all":
        if not body.shift_id:
            raise HTTPException(400, "请选择场次")
        g = _shift(db, body.shift_id)
        if g.activity_id != act.id:
            raise HTTPException(400, "所选场次不属于该活动")
        shift_id = g.id
    # 两步查询：先 DISTINCT user_id（JSON 列在 PG 上无相等运算符，不能整行去重）
    ids = (db.query(Enrollment.user_id)
           .filter(Enrollment.activity_id == act.id,
                   Enrollment.status == "enrolled"))
    if shift_id:
        ids = ids.filter(Enrollment.shift_id == shift_id)
    ids = [r[0] for r in ids.distinct().all()]
    users = [db.get(User, i) for i in ids]
    users = [u for u in users if u]
    if body.scope == "not_checked_in":
        checked = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == shift_id).all()}
        users = [u for u in users if u.id not in checked]
    files = _notify_files(db, act, body.file_ids)
    if files:
        # 企业微信推送带不了文件，正文里说一声去平台下载
        content += (f"\n\n附件：{'、'.join(f['title'] for f in files)}"
                    "（请在平台「消息通知」里下载）")
    batch_id = uid("bat") if body.need_ack else None
    for u in users:
        msgcenter.send_user(db, u, "custom", title=title, content=content,
                        data={"activity_id": act.id, "files": files},
                        need_ack=body.need_ack, batch_id=batch_id)
    n = len(users)
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=act.id, target_label=act.title,
              activity_id=act.id,
              summary=f"向 {n} 位志愿者发送通知：{title}"
                      + (f"（，附件 {len(files)} 个）" if files else "")
                      + ("（，要求签收）" if body.need_ack else ""))
    db.commit()
    return {"ok": True, "count": n, "batch_id": batch_id}


@router.get("/activities/{activity_id}/notify-batches")
def notify_batches(activity_id: str, db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """需要签收的群发批次及其签收情况。

    催签收的通知（ack_reminder）不进人数口径，只累计催办次数——
    催一次就多一条待签收的话，5 个人的批次催一轮会显示成 10 条。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    # 该活动报名者收到的、要求签收的通知，按批次聚合
    member_ids = [r[0] for r in db.query(Enrollment.user_id)
                  .filter(Enrollment.activity_id == act.id).distinct().all()]
    if not member_ids:
        return []
    rows = (db.query(Notification)
            .filter(Notification.batch_id.isnot(None),
                    (Notification.need_ack.is_(True))
                    | (Notification.template_key == "ack_reminder"),
                    Notification.user_id.in_(member_ids))
            .order_by(Notification.created_at.desc()).all())
    batches: dict[str, dict] = {}
    for n in rows:
        # 志愿者往往同时报了好几个活动，只认属于本活动的那些批次，
        # 否则这里会把别的活动的未签收名单（姓名+学号）摊给本活动管理者
        if str((n.data or {}).get("activity_id") or "") != act.id:
            continue
        b = batches.setdefault(n.batch_id, {
            "batch_id": n.batch_id, "title": n.title,
            "created_at": iso(n.created_at), "total": 0, "acked": 0,
            "reminded": 0, "last_remind_at": None, "pending": [],
            "_rounds": set()})
        if n.template_key == "ack_reminder":
            # 一轮催办给几个人就有几条，按轮次去重才是「催了几次」
            b["_rounds"].add(str((n.data or {}).get("remind_round") or n.id))
            b["reminded"] = len(b["_rounds"])
            b["last_remind_at"] = max(b["last_remind_at"] or "",
                                      iso(n.created_at))
            continue
        b["title"] = n.title          # 以原通知的标题为准
        b["created_at"] = iso(n.created_at)
        b["total"] += 1
        if n.acked_at:
            b["acked"] += 1
        else:
            u = db.get(User, n.user_id)
            if u is not None:
                b["pending"].append({"name": u.name, "student_id": u.student_id})
    for b in batches.values():
        b.pop("_rounds", None)
    return list(batches.values())


@router.post("/notify-batches/{batch_id}/remind")
def remind_batch(batch_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """一键提醒未签收的志愿者：催他们回去把原通知签掉。

    催办本身**不是**待签收通知——原来那条才是。否则催一次就多一条待办，
    5 个人的批次催一轮变成 10 条，谁也说不清还剩几个人没回。
    """
    pending = (db.query(Notification)
               .filter(Notification.batch_id == batch_id,
                       Notification.need_ack.is_(True),
                       Notification.acked_at.is_(None)).all())
    if not pending:
        raise HTTPException(400, "该批通知已全部签收，无需提醒")
    first = pending[0]
    activity_id = str((first.data or {}).get("activity_id") or "")
    if activity_id:
        ensure_activity_manager(db, user, activity_id)
    elif not user.is_admin():
        raise HTTPException(403, "无法确认该批通知所属活动，请由管理员操作")
    round_id = uid("rnd")
    for n in pending:
        u = db.get(User, n.user_id)
        if u is None:
            continue
        msgcenter.send_user(db, u, "ack_reminder", title=n.title,
                        data={"activity_id": activity_id,
                              "remind_round": round_id,
                              "ack_of": n.id},
                        batch_id=batch_id)
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=batch_id, target_label=first.title,
              activity_id=activity_id or None,
              summary=f"提醒 {len(pending)} 位未签收志愿者：{first.title}")
    db.commit()
    return {"ok": True, "count": len(pending)}


# ======================= 报名名单 =======================

@router.get("/shifts/{shift_id}/roster")
def roster(shift_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    positions = sorted(g.positions, key=lambda s: s.sort)
    checked_ids = {r[0] for r in db.query(CheckinRecord.user_id)
                   .filter(CheckinRecord.shift_id == g.id).all()}
    # 即选即得的报名问卷答案挂在报名记录上；筛选活动的在「报名申请」那张表里看
    questions = [] if act.is_screened() else (
        db.query(FormQuestion).filter(FormQuestion.activity_id == act.id)
        .order_by(FormQuestion.sort).all())

    # 三组名单先都取出来，再一次性取用户与评分。一行一条 SQL 的话，29 个人的
    # 名单就是 88 条（实测），而这里只需要个位数——名单端点是这一页最重的请求
    groups = {
        "enrolled": (db.query(Enrollment)
                     .filter(Enrollment.shift_id == g.id,
                             Enrollment.status == "enrolled")
                     .order_by(Enrollment.joined_at).all()),
        "cancelled": (db.query(Enrollment)
                      .filter(Enrollment.shift_id == g.id,
                              Enrollment.status == "cancelled")
                      .order_by(Enrollment.joined_at.desc()).limit(20).all()),
        "waitlist": logic.waitlist_rows(db, g.id),
    }
    everyone = [e for rows_ in groups.values() for e in rows_]
    users = {u.id: u for u in db.query(User).filter(
        User.id.in_({e.user_id for e in everyone})).all()}
    pos_by_id = {p.id: p for p in positions}
    scores = scoring.briefs(db, [e.user_id for e in everyone])

    def user_row(e: Enrollment) -> dict:
        u = users[e.user_id]
        st = pos_by_id.get(e.position_id) if e.position_id else None
        return {
            "enrollment_id": e.id,
            "answers": answers_out(db, questions, e.answers) if questions else [],
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "gender": u.gender, "phone": u.phone,
                     "wechat": u.wechat, "grade": u.grade, **scores.get(u.id, {})},
            "position_id": e.position_id,
            "position_name": st.name if st else "",
            "position_group": st.group_name if st else "",
            "is_leader": e.is_leader,
            "leader_willing": e.leader_willing,
            "assign_forced": e.assign_forced,
            "allow_reassign": e.allow_reassign,
            "source": e.source,
            "joined_at": iso(e.joined_at),
            "checked_in": u.id in checked_ids,
            "confirm_deadline": iso(e.confirm_deadline),
            "status": e.status,
            "cancel_type": e.cancel_type,
        }

    enrolled = [user_row(e) for e in groups["enrolled"]]
    waitlist = []
    for e in groups["waitlist"]:
        row = user_row(e)
        # 队列按岗位分，名次也要按岗位算（见 logic.waitlist_position）
        row["queue_position"] = logic.waitlist_position(db, e)
        waitlist.append(row)
    cancelled = [user_row(e) for e in groups["cancelled"]]
    # 人员变动流水：活动前一天少了几个人，得看得见是谁、什么时候退的
    changes = (db.query(AuditLog)
               .filter(AuditLog.activity_id == act.id,
                       AuditLog.action.in_(tuple(logic.ROSTER_ACTIONS)))
               .order_by(AuditLog.created_at.desc()).limit(30).all())
    return {
        "questions": [q.out() for q in questions],
        "roster_changes": [{
            "action": c.action,
            "action_label": logic.ROSTER_ACTIONS.get(c.action, c.action),
            "who": c.actor_name, "summary": c.summary,
            "created_at": iso(c.created_at),
        } for c in changes],
        # 候补为什么此刻不会自动顶上；空串 = 照常递补
        "auto_promote_stop": _promote_stop_reason(g),
        "shift": {"id": g.id, "name": g.name, "time_text": g.span_text(),
                  "capacity": g.total_capacity(), "needs_assign": g.needs_assign(),
                  "mode": g.mode, "mode_label": g.mode_label(),
                  "occupied": logic.shift_occupied_count(db, g.id),
                  "activity_id": act.id, "activity_title": act.title,
                  "start_time": iso(g.start_time)},
        "positions": [{"id": p.id, "name": p.name, "quota": p.quota,
                       "location": p.location,
                       "needs_leader": p.needs_leader,
                       "requirement_desc": p.requirement_desc() or "不限",
                       "enrolled": logic.position_enrolled_count(db, p.id)}
                      for p in positions],
        "enrolled": enrolled,
        "waitlist": waitlist,
        "cancelled": cancelled,
        "checked_in_count": len(checked_ids),
    }


def _promote_stop_reason(g: Shift) -> str:
    """名单页上告诉管理者：这一场的候补此刻为什么不会自动顶上。空串 = 照常递补。"""
    if now() >= g.start_time:
        return "场次已开始，候补不再自动顶上，需要补人请手动转正"
    if logic.shift_locked(g):
        return "因名单已锁定，停止候补自动转正，如需补人请在志愿者管理页面手动转正"
    if g.enroll_paused:
        return "报名暂停中，候补暂不顶上，恢复报名时按顺序补齐空位"
    locked = [p.name for p in g.positions if p.list_locked]
    paused = [p.name for p in g.positions if p.enroll_paused and not p.list_locked]
    parts = []
    if locked:
        parts.append(f"岗位{'、'.join(locked)}名单已锁定，停止候补自动转正，需要补人请手动转正")
    if paused:
        parts.append(f"岗位{'、'.join(paused)}暂停中，恢复时按顺序补齐")
    return "；".join(parts)


def _status_label(enr: Enrollment) -> str:
    return {"enrolled": "已报名", "waitlisted": "候补中",
            "pending_confirm": "待确认转正", "cancelled": "已取消",
            "applied": "申请中"}.get(enr.status, enr.status)


@router.post("/enrollments/{enrollment_id}/promote")
def promote_manually(enrollment_id: str, db: OrmSession = Depends(db_session),
                     user: User = Depends(require_manager)):
    """管理者手动为候补开确认窗。

    名单锁定、暂停报名时候补不会自动顶上（见 enroll_logic.promotion_blocked），
    要不要补人由负责人判断，但来不来仍由本人定：和自动递补一样开确认窗
    （2 小时、夜间不计时，不晚于场次开始，见 enroll_logic.promote_confirm_deadline）。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None:
        raise HTTPException(404, "报名记录不存在")
    ensure_activity_manager(db, user, enr.activity_id)
    ensure_not_finished(_act(db, enr.activity_id))
    if enr.status != "waitlisted":
        raise HTTPException(409, "该记录不在候补队列中")
    g = _shift(db, enr.shift_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    db.commit()          # 还连接再排锁，理由同 enroll
    with shift_lock(db, g.id):
        # 锁外看到的「候补中」可能已经旧了：另一位管理者刚转正过，或者本人
        # 刚退出了队列。不重读的话会给一个已经不在队里的人再开一次确认窗
        db.refresh(enr)
        if enr.status != "waitlisted":
            raise HTTPException(409, f"该记录已被处理（当前状态：{_status_label(enr)}），请刷新")
        if not logic.has_room(db, g, position):
            where = f"岗位「{position.name}」" if position else f"场次「{g.name}」"
            raise HTTPException(409, f"{where}没有空余名额，"
                                     f"如确需增加请先调整名额")
        target = db.get(User, enr.user_id)
        logic.start_confirm(db, enr, g)
        audit_log(db, actor=user, action="update", target_type="enrollment",
                  target_id=enr.id, target_label=target.name,
                  activity_id=enr.activity_id,
                  summary=f"手动为 {target.name} 开候补转正确认窗"
                          f"（{position.name if position else g.name}）")
        db.commit()
    return {"ok": True, "status": enr.status,
            "confirm_deadline": iso(enr.confirm_deadline),
            "confirm_hours": config.PROMOTE_CONFIRM_HOURS,
            "confirm_quiet": [config.PROMOTE_QUIET_START, config.PROMOTE_QUIET_END]}


class LeaderBody(BaseModel):
    is_leader: bool = True


@router.post("/enrollments/{enrollment_id}/leader")
def set_leader(enrollment_id: str, body: LeaderBody,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """指定或取消组长。组长只由管理者指定，系统不自动选人。

    一个组只能有一个组长——指定新人时先把原来的摘掉，否则导出表和大屏会
    出现两个组长。
    """
    enr = db.get(Enrollment, enrollment_id)
    if enr is None:
        raise HTTPException(404, "报名记录不存在")
    ensure_activity_manager(db, user, enr.activity_id)
    ensure_not_finished(_act(db, enr.activity_id))
    if enr.status != "enrolled":
        raise HTTPException(409, "只有正式报名的志愿者才能担任组长")
    if not enr.position_id:
        raise HTTPException(409, "该志愿者还没有岗位，请先分配岗位")
    position = db.get(Position, enr.position_id)
    siblings = logic.group_positions(db, position)
    if not logic.group_needs_leader(siblings):
        raise HTTPException(409, f"岗位「{position.name}」所在的组未设置组长，"
                                 f"请先在岗位设置中勾选「需要组长」")
    target = db.get(User, enr.user_id)

    if body.is_leader:
        sibling_ids = [x.id for x in logic.group_positions(db, position)]
        previous = (db.query(Enrollment)
                    .filter(Enrollment.position_id.in_(sibling_ids),
                            Enrollment.is_leader.is_(True),
                            Enrollment.id != enr.id).all())
        for row in previous:
            row.is_leader = False
        enr.is_leader = True
        was = "、".join(db.get(User, r.user_id).name for r in previous)
        scope = logic.leader_scope_label(siblings, position)
        summary = (f"指定 {target.name} 为「{scope}」组长"
                   + (f"（；原组长 {was} 卸任）" if was else ""))
        msgcenter.send_user(db, target, "leader_appointed",
                        activity=db.get(Activity, enr.activity_id).title,
                        position=scope,
                        position_time=position.time_text(),
                        position_loc=position.place(),
                        leader_task=(logic.group_leader_task(siblings)
                                     or "请与负责人确认"))
    else:
        enr.is_leader = False
        summary = f"取消 {target.name} 的岗位「{position.name}」组长身份"

    audit_log(db, actor=user, action="update", target_type="enrollment",
              target_id=enr.id, target_label=target.name,
              activity_id=enr.activity_id, summary=summary)
    db.commit()
    # 组长只由管理者指定：空缺了提醒他再指一位
    if not body.is_leader:
        logic.on_leader_vacated(db, position.id)
        db.commit()
    return {"ok": True, "is_leader": enr.is_leader}


# ======================= 一键分配 =======================

class PreviewBody(BaseModel):
    # 跨场次连续性：本活动其它场次已排过同名岗位的人，优先还待在那个岗位
    continuity: bool = True


@router.post("/shifts/{shift_id}/assign/preview")
def assign_preview(shift_id: str, body: PreviewBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    try:
        return assign.preview(db, g, continuity=body.continuity)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


IMPORT_MAX_BYTES = 5 * 1024 * 1024


@router.post("/shifts/{shift_id}/assign/import")
async def assign_import(shift_id: str, file: UploadFile = File(...),
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """导入排班：把改好的排班表读成一份方案，不落库（见 assign.plan_from_workbook）。"""
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    ensure_not_finished(_act(db, g.activity_id))
    data = await file.read(IMPORT_MAX_BYTES + 1)
    if len(data) > IMPORT_MAX_BYTES:
        raise HTTPException(413, "文件太大了，排班表一般不会超过 5 MB")
    try:
        return assign.plan_from_workbook(db, g, data)
    except ValueError as exc:
        raise HTTPException(400, str(exc))


class ApplyPlanBody(BaseModel):
    plan: list[dict]
    source: str = "assign"


@router.post("/shifts/{shift_id}/assign/apply")
def assign_apply(shift_id: str, body: ApplyPlanBody, bg: BackgroundTasks,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    ensure_not_finished(_act(db, g.activity_id))
    if not body.plan:
        raise HTTPException(400, "空方案")
    notify_items: list[dict] = []
    db.commit()          # 还连接再排锁，理由同 enroll
    # 名额校验、落库、递补在同一把场次锁、同一个事务里：调岗把人从甲岗调走，
    # 甲岗的候补队首要马上顶上；分开提交的话，中间那一瞬放出来的位子会被
    # 新报名的人抢走，排着队的人反倒轮不上
    with shift_lock(db, g.id):
        logic.reload_shift(db, g)
        try:
            result = assign.apply_plan(db, g, user, body.plan,
                                       source=body.source,
                                       notify_out=notify_items)
        except ValueError as exc:
            db.rollback()
            raise HTTPException(409, str(exc))
        result["promoted"] = logic.fill_vacancies(db, g)
        db.commit()
    # 各岗位的空位在闸门之外变了：作废计数器，下一个报名按数据库重新对齐
    for p in g.positions:
        invalidate_gates(g.id, p.id)
    if notify_items:
        bg.add_task(_notify_assignment_bg, notify_items)
    return result


@router.post("/shifts/{shift_id}/add-walkin")
def add_walkin(shift_id: str, body: dict,
               db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    """管理员直接添加志愿者（现场补招）。

    名额超出拦一道，勾选强制才放行——补招是常规操作，不该顺手把场次搞超员；
    真要超也得是明确按下去的，且留痕。时段冲突先提示（code=time_conflict），
    管理者确认后带 confirm_conflict 重发，同样留痕。

    attached_ids：一并报上的培训/彩排（本场次下、对这个岗位生效的）。
    补招**不自动**连带：现场临时加进来的人，赶不赶得上培训、要不要去彩排，
    得负责人自己判断，所以只报勾上的那几场。它们的时段冲突一并提示。
    """
    g = _shift(db, shift_id)
    ensure_activity_manager(db, user, g.activity_id)
    act = _act(db, g.activity_id)
    # 还没上广场的活动一个人都报不进来，补招就等于绕过审批把名单先定下来：
    # 草稿、待审批、审批通过待发布的都不给补
    if act.is_unpublished():
        raise HTTPException(409, "活动还没有发布，不能现场补招；请先发布活动")
    # 培训/彩排跟着服务场次的报名走，自己不招人；要加人请加到服务场次、
    # 在那一场补招时勾上它（见 body 的 attached_ids）
    if g.is_attached():
        raise HTTPException(409, f"「{g.name}」是本活动的{g.kind_label()}，"
                                 f"不单独招人；请到服务场次补招，并勾选这一场")
    # 补招卡在「时长封账」而不是「活动结束」：现场漏登记的人事后才补进来，
    # 补进来才好给他记时长
    ensure_hours_editable(db, act)
    sid = (body.get("student_id") or "").strip()
    u = db.query(User).filter(User.student_id == sid).first()
    if u is None:
        raise HTTPException(404, f"学号 {sid} 不存在")
    if u.user_type == "teacher":
        raise HTTPException(400, "老师类型用户不参与志愿报名，无法补招")

    force = bool(body.get("force"))
    confirm = bool(body.get("confirm_conflict"))
    reasons: list[str] = []         # 名额超出：要勾强制
    clashes: list[dict] = []        # 时段冲突：要确认
    clash_notes: list[str] = []
    db.commit()          # 还连接再抢锁，理由同 enroll（routers/activities.py）
    # 连同这个人的用户锁：下面要判时段冲突，本人可能正同时在别的场次报名
    # （见 locks 的「用户锁」；先场次后用户，不会死锁）
    with shift_lock(db, g.id, user_id=u.id):
        live = (db.query(Enrollment)
                .filter(Enrollment.shift_id == g.id, Enrollment.user_id == u.id)
                .filter(Enrollment.status.in_(logic.ACTIVE)).first())
        if live is not None:
            raise HTTPException(409, f"{u.name} 在该场次已有记录（{_status_label(live)}）")

        # 名额要按落点算：pick 模式下名额在岗位上，不传岗位一律判「满」，
        # 补招任何按岗位报名的场次都会被要求勾强制。
        # 截止后统一分配的场次没指定岗位就进「待分配」，名额按整场数
        holder = g.placeholder()
        position = holder
        if body.get("position_id"):
            position = db.get(Position, body["position_id"])
            if position is None or position.shift_id != g.id:
                raise HTTPException(400, "岗位不属于该场次")

        hit = logic.time_conflicts(db, u, g)
        if hit:
            clashes += hit
            clash_notes.append(f"时段与{logic.conflict_message(hit, g.activity_id)}冲突")

        wanted = list(dict.fromkeys(body.get("attached_ids") or []))
        allowed = {s.id: s for s in logic.attached_shifts(
            db, g.id, position.id if position else None)}
        extra: list[Shift] = []
        for aid in wanted:
            s = allowed.get(aid)
            if s is None:
                raise HTTPException(400, "所选培训/彩排不属于该场次，或对这个岗位不生效")
            if (db.query(Enrollment)
                    .filter(Enrollment.shift_id == s.id, Enrollment.user_id == u.id)
                    .first()) is not None:
                continue            # 已经在那场里了（比如之前连带报过），不重复加
            hit = logic.time_conflicts(db, u, s)
            if hit:
                clashes += hit
                clash_notes.append(f"{s.kind_label()}「{s.name}」时段与"
                                   f"{logic.conflict_message(hit, g.activity_id)}冲突")
            extra.append(s)
        if clashes and not confirm:
            raise logic.conflict_error(f"{u.name}：{'；'.join(clash_notes)}",
                                       logic.dedupe_conflicts(clashes))

        if not logic.has_room(db, g, position):
            reasons.append(f"「{position.name}」名额已满（上限{position.quota}人）"
                           if position is not None and position is not holder else
                           f"场次名额已满（上限{g.total_capacity()}人）")
        elif holder is not None and position is not holder \
                and not logic.has_room(db, g, holder):
            reasons.append(f"场次名额已满（上限{holder.quota}人）")
        if reasons and not force:
            raise HTTPException(409, f"{u.name}：{'；'.join(reasons)}。"
                                     f"确认添加请勾选强制")
        # 留痕与名单上的「强制」标记两样都算：都是管理者越过了一道提示
        reasons += clash_notes

        # 党校同学只能这样加进来：时长带「党校」标签，不进导出表、不计入本人累计
        source = hours_flow.PARTY_SCHOOL if body.get("party_school") else "admin"
        enr = Enrollment(id=uid("enr"), activity_id=g.activity_id, shift_id=g.id,
                         user_id=u.id, source=source, joined_at=now(),
                         status="enrolled",
                         allow_reassign=bool(body.get("allow_reassign", True)))
        if position is not None:
            enr.position_id = position.id
            enr.assigned_at = now()
            enr.assign_forced = bool(reasons)
        db.add(enr)
        for s in extra:
            # 与 sync_attached 连带报上的行同形：附属场次不设岗位
            db.add(Enrollment(id=uid("enr"), activity_id=g.activity_id,
                              shift_id=s.id, user_id=u.id, source=source,
                              status="enrolled", joined_at=now()))
        also = "、".join(f"{s.kind_label()}「{s.name}」" for s in extra)
        audit_log(db, actor=user, action="force_assign" if reasons else "create",
                  target_type="enrollment", target_id=enr.id,
                  target_label=f"{u.name}", activity_id=g.activity_id,
                  summary=f"补招 {u.name}（{sid}）至场次「{g.name}」"
                          + ("（党校同学）" if source == hours_flow.PARTY_SCHOOL else "")
                          + (f"（；连带报上{also}）" if extra else "")
                          + (f"（；强制：{'；'.join(reasons)}）" if reasons else ""))
        # 补招是管理者单方面加进来的：不通知的话，本人那边一点动静都没有——
        # 不知道自己报了名，更不知道哪天要去哪儿。跟候补转正一样得告诉他。
        # 场次已经结束的是事后补登记，只告诉他补记上了，不叫他来
        data = {"activity_id": g.activity_id}
        if g.end_time <= now():
            msgcenter.send_user(db, u, "walkin_recorded", data, activity=act.title,
                                shift=g.name, time=g.span_text())
        else:
            msgcenter.send_user(
                db, u, "walkin_added", data, activity=act.title, shift=g.name,
                time=g.span_text(), loc=g.location or act.location or "待定",
                position=(position.name if position is not holder
                          else "待分配，岗位确定后另行通知"),
                attached="".join(f"{s.kind_label()}：{s.span_text()}"
                                 f"（{s.location or act.location or '地点待定'}）\n"
                                 for s in extra))
        db.commit()
    return {"ok": True, "enrollment_id": enr.id, "forced_reasons": reasons,
            "attached": [s.id for s in extra]}


# ======================= 联络群二维码 =======================

# 二维码是用户上传的文件，和材料走同一条路：落在 config.MATERIAL_DIR（挂载卷）、
# 写一条 Attachment、读的时候判权限。别为了 <img src> 好写另开一条匿名直链——
# 2026-10-13 那次丢文件就出在这条自成一体的路上（目录自己拼，还错了一层）。
QR_OWNER = "activity_qr"
QR_URL_PREFIX = "/api/staff/contact-qr/"
MAX_IMAGE_BYTES = 2 * 1024 * 1024


def qr_url(attachment_id: str) -> str:
    """二维码的访问地址。活动的 contact_group_qr 里存的就是它。"""
    return f"{QR_URL_PREFIX}{attachment_id}"


def qr_activity(db: OrmSession, attachment_id: str) -> Activity | None:
    """这张二维码挂在哪个活动上。

    上传发生在活动存在之前（表单里先传图、后保存），所以归属不看
    Attachment.owner_id，认的是活动里存着的那个地址——上传后没保存的，
    这里就是 None。
    """
    return (db.query(Activity)
            .filter(Activity.contact_group_qr == qr_url(attachment_id)).first())


@router.post("/upload-image")
async def upload_image(file: UploadFile = File(...),
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """上传志愿者联络群二维码（png/jpg/jpeg/webp，≤2MB），返回访问地址。"""
    files = await uploads.read_files([file], label="二维码",
                                     exts=uploads.IMAGE_TYPES, max_count=1,
                                     max_bytes=MAX_IMAGE_BYTES)
    row = uploads.save_files(db, files, owner_type=QR_OWNER, owner_id="",
                             uploaded_by=user.id)[0]
    db.commit()
    return {"url": qr_url(row.id)}


@router.get("/contact-qr/{attachment_id}")
def contact_qr(attachment_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(current_user_download)):
    """读二维码：活动管理者、已报上名的志愿者本人，以及刚传完还没保存的那位
    （表单里的预览）。别人一律 404，不透露这张图存不存在。

    <img src> 带不了 Authorization 头，所以前端是带凭据把图取回来再显示
    （见 web/src/composables/authedImage.js），不能只靠文件名难猜。
    """
    row = db.get(Attachment, attachment_id)
    if row is None or row.owner_type != QR_OWNER:
        raise HTTPException(404, "文件不存在")
    act = qr_activity(db, row.id)
    allowed = act is None and row.uploaded_by == user.id
    if act is not None:
        allowed = (is_activity_manager(db, user, act.id)
                   or db.query(Enrollment)
                   .filter(Enrollment.activity_id == act.id,
                           Enrollment.user_id == user.id,
                           Enrollment.status == "enrolled").first() is not None)
    if not allowed:
        raise HTTPException(404, "文件不存在")
    path = config.MATERIAL_DIR / row.stored_name
    if not path.is_file():
        raise HTTPException(404, "文件已丢失，请重新上传")
    ext = row.stored_name.rsplit(".", 1)[-1]
    return downloads.file_response(
        path, row.title, kind="inline",
        media_type=uploads.IMAGE_TYPES.get(ext, "application/octet-stream"))
