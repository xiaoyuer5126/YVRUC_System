"""教师/青协端：签到任务、导出中心、时长核准。"""
from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession

from ..audit import log as audit_log
from .. import config, hours as hours_flow, liaison
from ..stage import ensure_hours_editable, ensure_not_finished
from ..database import db_session
from ..deps import require_manager, require_manager_download
from ..enroll_logic import position_enrolled_count
from ..exporter import (build_activity_workbook, build_application_workbook,
                        build_hours_workbook)
from ..perm import ensure_activity_manager
from ..integrations import msgcenter
from ..locks import current_checkin_code
from ..notify import notify, notify_checkin
from ..models import (Activity, CheckinGeo, CheckinRecord, CheckinTask,
                      Department, Enrollment, HoursAppeal, HoursRecord,
                      HoursRequest, Shift, Position, User, iso, new_checkin_token, now,
                      parse_local_dt, span_text, uid)
from ..serializers import iso_of, recruit_scope_text

router = APIRouter(prefix="/api/staff", tags=["staff-ops"])


def _act(db: OrmSession, activity_id: str) -> Activity:
    act = db.get(Activity, activity_id)
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


# ======================= 签到任务 =======================

class TaskBody(BaseModel):
    mode: str = "shift"                 # shift 统一签到 / position 分岗位
    position_id: str | None = None      # 单岗位（旧的调用方式）
    position_ids: list[str] = []        # 分岗位时可选多个，一个岗位一张码
    valid_from: str | None = None
    valid_to: str | None = None


def live_task_conflict(db: OrmSession, shift_id: str, position_id: str | None,
                       *, exclude_id: str | None = None) -> CheckinTask | None:
    """这一场里有没有已经开着的码跟它冲突。

    **整场的码算「所有岗位」**：整场开着的时候，再给某个岗位开一张，学生扫的
    是哪张、记录落到哪张任务下都说不清（看板还会各算各的）。反过来也一样——
    某个岗位开着码时再开整场的，同样会让两拨人扫到不同的任务。

    自己跟自己不算冲突（改时段、重新开启时要把自己排除掉）。
    """
    q = (db.query(CheckinTask)
         .filter(CheckinTask.shift_id == shift_id,
                 CheckinTask.active.is_(True)))
    if exclude_id:
        q = q.filter(CheckinTask.id != exclude_id)
    for t in q.all():
        if t.position_id is None or position_id is None \
                or t.position_id == position_id:
            return t
    return None


def _hm(t: datetime | None) -> str:
    """可签时段的一端；留空表示这一端不限。"""
    return f"{t:%m-%d %H:%M}" if t else "不限"


def _conflict_message(db: OrmSession, t: CheckinTask, g: Shift) -> str:
    st = db.get(Position, t.position_id) if t.position_id else None
    return (f"本场次已经有开启的签到码（"
            f"{st.name if st else '整场统一签到'}），请先把它关掉再开新的")


@router.post("/shifts/{shift_id}/checkin-tasks")
def create_task(shift_id: str, body: TaskBody,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    ensure_not_finished(act)
    positions: list[Position] = []
    if body.mode == "position":
        # 可多选：一个岗位一张码。只认本场次下的岗位
        wanted = body.position_ids or ([body.position_id] if body.position_id else [])
        # 去重：同一个岗位勾两次会开出两张同时开着的码——冲突检查只比库里已有的
        wanted = list(dict.fromkeys(wanted))
        if not wanted:
            raise HTTPException(400, "分岗位签到必须指定本场次下的岗位")
        for pid in wanted:
            st = db.get(Position, pid)
            if st is None or st.shift_id != g.id:
                raise HTTPException(400, "分岗位签到必须指定本场次下的岗位")
            positions.append(st)
    try:
        valid_from = parse_local_dt(body.valid_from) if body.valid_from else None
        valid_to = parse_local_dt(body.valid_to) if body.valid_to else None
    except ValueError:
        raise HTTPException(400, "时间格式不对") from None
    # 一次一张地查冲突：多选时只要有一个岗位撞上就整体拒绝，别做一半
    for st in (positions or [None]):
        live = live_task_conflict(db, g.id, st.id if st else None)
        if live is not None:
            raise HTTPException(409, _conflict_message(db, live, g))
    pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
    made: list[CheckinTask] = []
    for st in (positions or [None]):
        task = CheckinTask(id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                           position_id=st.id if st else None,
                           mode=body.mode, token=new_checkin_token(),
                           valid_from=valid_from or g.start_time - pad,
                           valid_to=valid_to or g.end_time + pad,
                           created_by=user.id)
        db.add(task)
        label = st.name if st else f"{g.name}·统一签到"
        audit_log(db, actor=user, action="checkin_task", target_type="checkin",
                  target_id=task.id, target_label=f"{act.title}·{label}",
                  activity_id=act.id,
                  summary=f"生成签到码 {task.token}"
                          f"（{'岗位' if st else '统一'}模式，"
                          f"有效 {task.valid_from:%H:%M}~{task.valid_to:%H:%M}）")
        made.append(task)
    db.commit()
    return {**({"id": made[0].id, "token": made[0].token} if made else {}),
            # 多选时一次生成好几张，按 tasks 拿全的
            "tasks": [{"id": t.id, "token": t.token} for t in made]}


@router.get("/activities/{activity_id}/checkin-tasks")
def list_tasks(activity_id: str, db: OrmSession = Depends(db_session),
               user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    _act(db, activity_id)
    tasks = (db.query(CheckinTask)
             .filter(CheckinTask.activity_id == activity_id)
             .order_by(CheckinTask.created_at.desc()).all())
    # 锚点一次查完按 task_id 归位，别在循环里一人一次查询
    geos = {x.task_id: x for x in db.query(CheckinGeo)
            .filter(CheckinGeo.task_id.in_([t.id for t in tasks] or [""])).all()}
    out = []
    for t in tasks:
        g = db.get(Shift, t.shift_id)
        st = db.get(Position, t.position_id) if t.position_id else None
        n = (db.query(func.count(CheckinRecord.id))
             .filter(CheckinRecord.task_id == t.id).scalar() or 0)
        x = geos.get(t.id)
        out.append({"id": t.id, "token": t.token, "mode": t.mode,
                    # shift_id 前端要用：一个活动多场次时，码表要按场次分辨，
                    # 手动补签也只能打到当前这块大屏对应的那一场上
                    "shift_id": t.shift_id, "shift_name": g.name,
                    "shift_kind": g.kind, "shift_kind_label": g.kind_label(),
                    "position_name": st.name if st else "",
                    "valid_from": iso(t.valid_from), "valid_to": iso(t.valid_to),
                    "active": t.active, "count": n,
                    # 有锚点 = 这个任务走定位签到，前端据此显示「方式」列
                    "geo": ({"lat": x.lat, "lng": x.lng, "radius_m": x.radius_m,
                             "accuracy_m": x.accuracy_m} if x else None)})
    return out


# ======================= 定位签到的坐标锚点 =======================

# 天地图的地名搜索。它的密钥分类型不能混用，这类接口要的是**服务端** tk，
# 而服务端 tk 绝不能进前端——所以由后端转一手，前端只跟我们说话。
GEO_SEARCH_URL = "https://api.tianditu.gov.cn/v2/search"
GEO_SEARCH_TIMEOUT = 8
# 搜索范围：以当前签到点为中心画一个方框（单位：度）。0.15° 约 16 公里，
# 盖得住一个校区和周边。不限定的话，「明德楼」能搜出桂林和深圳的来。
GEO_SEARCH_SPAN = 0.15


@router.get("/geo/search")
def geo_search(q: str, lat: float | None = None, lng: float | None = None,
               user: User = Depends(require_manager)):
    """按地名找坐标：管理端输「明德楼」，给一串候选点和它们的经纬度。

    给了中心点就在那附近搜（管理端多半已经选过校区或量过位置），没给就搜全国
    ——那样同名异地会很多，界面上要把地址一并列出来让人认。
    """
    kw = (q or "").strip()
    if len(kw) < 2:
        raise HTTPException(400, "请至少输入两个字")
    if not config.TIANDITU_TK_SERVER:
        # 没配服务端 tk：说清楚是没配，而不是「没搜到」，免得人反复换关键词
        return {"configured": False, "items": []}
    if lat is None or lng is None:
        bound = "-180,-90,180,90"
    else:
        bound = (f"{lng - GEO_SEARCH_SPAN},{lat - GEO_SEARCH_SPAN},"
                 f"{lng + GEO_SEARCH_SPAN},{lat + GEO_SEARCH_SPAN}")
    # queryType 必须是 1（普通搜索）：7 是「地名搜索」，但**它不认 mapBound**，
    # 实测会把桂林、深圳的同名楼一起返回
    post = {"keyWord": kw, "mapBound": bound, "level": 16, "queryType": 1,
            "start": 0, "count": 10}
    url = GEO_SEARCH_URL + "?" + urlencode({
        "postStr": json.dumps(post, ensure_ascii=False),
        "type": "query", "tk": config.TIANDITU_TK_SERVER})
    try:
        with urllib.request.urlopen(url, timeout=GEO_SEARCH_TIMEOUT) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
    except Exception:
        # 天地图挂了不该把人堵在这一步：手填坐标那条路照旧
        raise HTTPException(502, "地名搜索暂时不可用，请稍后再试或直接填坐标")
    items = []
    for p in (data.get("pois") or []):
        parts = (p.get("lonlat") or "").split(",")
        if len(parts) != 2:
            continue
        try:
            lo, la = float(parts[0]), float(parts[1])
        except ValueError:
            continue
        items.append({"name": p.get("name") or "", "address": p.get("address") or "",
                      "lat": la, "lng": lo})
    return {"configured": True, "items": items}


@router.get("/map-config")
def map_config(user: User = Depends(require_manager)):
    """管理端画地图要的参数。

    tk 是**浏览器端**的密钥，会原样进前端（瓦片地址得在浏览器里拼），所以它
    本来就公开，真正的保护是天地图后台的域名白名单。走这个接口而不是构建时
    写死，是为了换 key 不用重新构建前端。留空 = 前端不画地图，功能照旧。
    """
    return {"provider": "tianditu", "tk": config.TIANDITU_TK,
            "campuses": config.CAMPUS_PRESETS}


class GeoBody(BaseModel):
    """签到点的坐标。前端三种给法：站在现场用 navigator.geolocation 取（gps）、
    在管理端的地图上点/拖（map）、点校区快捷按钮落到校区中心（campus）。
    **只有 gps 能说明「人到过现场」**，所以 source 要传上来，写进审计——
    另两种也允许（有时候没别的办法），但得留下「这个点不是现场量的」这个事实。"""
    lat: float
    lng: float
    accuracy: float = 0
    radius_m: int = config.GEO_RADIUS_DEFAULT_M
    source: str = "gps"        # gps 现场量的 / map 图上点的 / campus 校区预设点


@router.put("/checkin-tasks/{task_id}/geo")
def set_geo(task_id: str, body: GeoBody,
            db: OrmSession = Depends(db_session),
            user: User = Depends(require_manager)):
    """把这个任务改成定位签到（已有锚点则改半径/位置）。

    坐标就是「人在哪儿算到场」的唯一依据，所以最好由管理者**在现场**用手机量。
    图上点的、校区预设的也收，但审计里写明来源（见 GeoBody）：日后有人质疑
    签到判定，得查得出这个点是不是坐办公室定的。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    ensure_not_finished(_act(db, task.activity_id))

    if not (-90 <= body.lat <= 90 and -180 <= body.lng <= 180):
        raise HTTPException(400, "坐标不合法")
    if body.lat == 0 and body.lng == 0:
        # 浏览器取不到定位时会给 (0,0)（几内亚湾），照单全收就成了一个
        # 谁都不在范围内的签到点，现场还查不出原因
        raise HTTPException(400, "没有取到有效定位，请到现场重新量一次")
    if not (config.GEO_RADIUS_MIN_M <= body.radius_m <= config.GEO_RADIUS_MAX_M):
        raise HTTPException(
            400, f"签到范围请填 {config.GEO_RADIUS_MIN_M}~"
                 f"{config.GEO_RADIUS_MAX_M} 米")

    geo = (db.query(CheckinGeo).filter(CheckinGeo.task_id == task.id).first())
    if geo is None:
        geo = CheckinGeo(id=uid("geo"), task_id=task.id, set_by=user.id)
        db.add(geo)
    geo.lat, geo.lng = round(body.lat, 6), round(body.lng, 6)
    geo.radius_m = body.radius_m
    geo.accuracy_m = round(body.accuracy, 1)
    geo.set_by, geo.set_at = user.id, now()

    g = db.get(Shift, task.shift_id)
    st = db.get(Position, task.position_id) if task.position_id else None
    audit_log(db, actor=user, action="checkin_geo", target_type="checkin",
              target_id=task.id,
              target_label=f"{_act(db, task.activity_id).title}·"
                           f"{st.name if st else g.name}",
              activity_id=task.activity_id,
              summary=f"把「{st.name if st else g.name}」改成定位签到："
                      f"({geo.lat:.5f}, {geo.lng:.5f}) 半径 {geo.radius_m} 米"
                      + (f"，量点时误差 ±{geo.accuracy_m:.0f} 米"
                         if geo.accuracy_m else "")
                      + {"map": "；**坐标是在地图上点的，不是站在现场量的**",
                         "campus": "；坐标取自校区预设点（多半不是具体岗位），"
                                   "落点未经现场核对",
                         "search": "；坐标来自地名搜索，落点未经现场核对",
                         }.get(body.source, "；坐标在现场量的"))
    db.commit()
    return {"ok": True, "geo": {"lat": geo.lat, "lng": geo.lng,
                               "radius_m": geo.radius_m,
                               "accuracy_m": geo.accuracy_m}}


@router.delete("/checkin-tasks/{task_id}/geo")
def clear_geo(task_id: str, db: OrmSession = Depends(db_session),
              user: User = Depends(require_manager)):
    """撤销定位签到，改回扫码。"""
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    ensure_not_finished(_act(db, task.activity_id))
    geo = (db.query(CheckinGeo).filter(CheckinGeo.task_id == task.id).first())
    if geo is not None:
        g = db.get(Shift, task.shift_id)
        st = db.get(Position, task.position_id) if task.position_id else None
        audit_log(db, actor=user, action="checkin_geo", target_type="checkin",
                  target_id=task.id,
                  target_label=f"{_act(db, task.activity_id).title}·"
                               f"{st.name if st else g.name}",
                  activity_id=task.activity_id,
                  summary=f"取消定位签到，改回扫码："
                          f"{st.name if st else g.name}")
        db.delete(geo)
        db.commit()
    return {"ok": True}


@router.get("/checkin-tasks/{task_id}/code")
def checkin_code(task_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """取该任务此刻的动态签到码（没有就发一张），管理端每 30 秒来取一次。

    **换码不删旧码**：新码发出后旧码还有一段寿命，留给正在排队扫码的人。
    所以前端拿到 expires_in 后照着它定时刷新即可，不必自己算时间。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    code, expires_in = current_checkin_code(
        task.id, config.CHECKIN_CODE_TTL_SECONDS,
        config.CHECKIN_CODE_REFRESH_SECONDS)
    # refresh 一并返回：前端照着它说明「多久换一张」，节奏只在 config 里写一份
    return {"code": code, "expires_in": expires_in,
            "ttl": config.CHECKIN_CODE_TTL_SECONDS,
            "refresh": config.CHECKIN_CODE_REFRESH_SECONDS}


@router.patch("/checkin-tasks/{task_id}")
def toggle_task(task_id: str, body: dict,
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """开启/关闭，改可签时段。

    开启时同样要查冲突：关掉一张、再打开另一张，很容易凑出两张同时开着的
    （同一个岗位的两张、或者一张整场的一张分岗位的），现场就说不清该扫哪张了。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    ensure_not_finished(_act(db, task.activity_id))

    if "valid_from" in body or "valid_to" in body:
        # 现场最常见的意外是场次提前或拖堂：以前只能关掉重建，而重建会让
        # 已经扫过的那张码作废（记录还挂在老任务下）。时段本来就该能改
        for key in ("valid_from", "valid_to"):
            if key in body:
                raw = (body[key] or "").strip()
                try:
                    setattr(task, key, parse_local_dt(raw) if raw else None)
                except ValueError:
                    raise HTTPException(400, "时间格式不对") from None
        if task.valid_from and task.valid_to and task.valid_to <= task.valid_from:
            raise HTTPException(400, "结束时间要晚于开始时间")
        g = db.get(Shift, task.shift_id)
        st = db.get(Position, task.position_id) if task.position_id else None
        audit_log(db, actor=user, action="checkin_task", target_type="checkin",
                  target_id=task.id,
                  target_label=f"{_act(db, task.activity_id).title}·"
                               f"{st.name if st else (g.name + '·统一签到')}",
                  activity_id=task.activity_id,
                  summary=f"改可签时段：{_hm(task.valid_from)} ~ {_hm(task.valid_to)}")

    if "active" in body:
        want = bool(body["active"])
        if want:
            live = live_task_conflict(db, task.shift_id, task.position_id,
                                      exclude_id=task.id)
            if live is not None:
                raise HTTPException(
                    409, _conflict_message(db, live, db.get(Shift, task.shift_id)))
        task.active = want
    db.commit()
    return {"ok": True, "active": task.active,
            "valid_from": iso(task.valid_from), "valid_to": iso(task.valid_to)}


class ReminderBody(BaseModel):
    """范围三选一：整场次 / 指定岗位组 / 指定岗位。"""
    position_ids: list[str] = []
    group_name: str = ""
    need_ack: bool = False   # 要求志愿者点「我已知悉」，管理端能看到谁没回


@router.post("/shifts/{shift_id}/checkin-reminder")
def send_reminder(shift_id: str, body: ReminderBody | None = None,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """手动补发签到提醒。

    自动提醒在场次开始前 30 分钟发一次；这个按钮用于临时催场——
    只发给还没签到的正式志愿者，已经到岗的不再打扰。
    可只催某几个岗位或某个岗位组：现场往往是「东门那三个人没到」。
    勾选签收后，这一批会出现在活动的「待签收批次」里，能看到谁还没回。
    """
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    ensure_not_finished(_act(db, g.activity_id))

    body = body or ReminderBody()
    ids, scope = None, "全场次"
    if body.group_name.strip():
        ids = [p.id for p in g.positions if p.group_name == body.group_name.strip()]
        if not ids:
            raise HTTPException(400, f"本场次没有岗位组「{body.group_name}」")
        scope = f"岗位组「{body.group_name}」"
    elif body.position_ids:
        known = {p.id: p for p in g.positions}
        bad = [i for i in body.position_ids if i not in known]
        if bad:
            raise HTTPException(400, "所选岗位不属于该场次")
        ids = list(body.position_ids)
        scope = "岗位" + "、".join(f"「{known[i].name}」" for i in ids)

    batch_id = uid("bat") if body.need_ack else None
    n = notify_checkin(db, g, position_ids=ids, need_ack=body.need_ack,
                       batch_id=batch_id)
    if not n:
        raise HTTPException(400, f"{scope}没有需要提醒的志愿者")
    g.reminder_sent_at = now()
    audit_log(db, actor=user, action="update", target_type="notify",
              target_id=g.id, target_label=g.name, activity_id=g.activity_id,
              summary=f"手动补发签到提醒：{scope} {n} 人（未签到）"
                      + ("，要求签收" if body.need_ack else ""))
    db.commit()
    return {"ok": True, "count": n, "scope": scope, "batch_id": batch_id}


@router.get("/checkin-tasks/{task_id}/board")
def board(task_id: str, db: OrmSession = Depends(db_session),
          user: User = Depends(require_manager)):
    """签到实时大屏。以已报名报名名单为主表（含未签到者），前端 5s 轮询。"""
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    g = db.get(Shift, task.shift_id)
    act = db.get(Activity, task.activity_id)

    roster = (db.query(Enrollment, User)
              .join(User, Enrollment.user_id == User.id)
              .filter(Enrollment.shift_id == g.id,
                      Enrollment.status == "enrolled")
              .order_by(User.college, User.name).all())
    recs = (db.query(CheckinRecord)
            .filter(CheckinRecord.shift_id == g.id).all())
    rec_by_user: dict[str, CheckinRecord] = {r.user_id: r for r in recs}

    items = []
    checked = checked_out = 0
    # 分母是「分到这个岗位的人数」，不是名额——同 shift_board 里的说明
    by_position: dict[str, int] = {}
    expected_by_position: dict[str, int] = {}
    for enr, u in roster:
        st = db.get(Position, enr.position_id) if enr.position_id else None
        rec = rec_by_user.get(u.id)
        key = enr.position_id or "_shift"
        expected_by_position[key] = expected_by_position.get(key, 0) + 1
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            by_position[key] = by_position.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "position": st.name if st else "待分配",
            "is_leader": enr.is_leader,
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                  "checked": by_position.get(s.id, 0),
                  "expected": expected_by_position.get(s.id, 0)}
                 for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "task": {"id": task.id, "token": task.token, "mode": task.mode,
                 "active": task.active},
        "activity_title": act.title, "shift_name": g.name,
        "expected": len(roster), "checked": checked,
        "checked_out": checked_out,
        "positions": positions, "records": items[:500],
    }


@router.get("/shifts/{shift_id}/board")
def shift_board(shift_id: str, db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """按场次聚合的签到实时大屏（不依赖激活的签到任务，
    任务关闭/不存在时仍可查看到场名单）。前端 5s 轮询。

    以已报名报名（Enrollment.status=enrolled）join User 为主表，
    左连该场次的 CheckinRecord（user_id+shift_id 匹配，一人至多一条）。
    """
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)

    rows = (db.query(Enrollment, User, CheckinRecord)
            .join(User, Enrollment.user_id == User.id)
            .outerjoin(CheckinRecord,
                       (CheckinRecord.user_id == Enrollment.user_id)
                       & (CheckinRecord.shift_id == Enrollment.shift_id))
            .filter(Enrollment.shift_id == g.id,
                    Enrollment.status == "enrolled")
            .order_by(User.college, User.name).all())

    items = []
    checked = checked_out = excused = 0
    # 两个都按**名单上分的岗位**归堆（不按签到记录里的岗位）：岗位卡片回答的是
    # 「分给我的这几个人来了没有」。用名额当分母会把人吓一跳——名额 5、只排了
    # 2 个人时写「0/5 到岗」，看着像缺 5 个，其实是名额没招满，那是另一回事
    by_position: dict[str, int] = {}          # 到岗
    expected_by_position: dict[str, int] = {}  # 应到岗
    for enr, u, rec in rows:
        key = enr.position_id or "_shift"
        if enr.excused and not rec:
            excused += 1
        else:
            expected_by_position[key] = expected_by_position.get(key, 0) + 1
        st = db.get(Position, enr.position_id) if enr.position_id else None
        if rec:
            checked += 1
            if rec.checkout_at:
                checked_out += 1
            by_position[key] = by_position.get(key, 0) + 1
        items.append({
            "record_id": rec.id if rec else "",
            "name": u.name, "student_id": u.student_id,
            "college": u.college or "",
            "position": st.name if st else "待分配",
            "is_leader": enr.is_leader,
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
            "excused": enr.excused, "excuse_reason": enr.excuse_reason,
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                  "checked": by_position.get(s.id, 0),
                  "expected": expected_by_position.get(s.id, 0)}
                 for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "activity_id": act.id, "activity_title": act.title,
        "shift_id": g.id, "shift_name": g.name,
        "kind": g.kind, "kind_label": g.kind_label(),
        # 请假的人不进应到岗的分母：他事先说了不来，不该拉低到岗率
        "expected": len(rows) - excused, "checked": checked,
        "excused": excused,
        "checked_out": checked_out,
        "positions": positions, "records": items[:500],
    }


@router.post("/checkin-tasks/{task_id}/batch")
def batch_op(task_id: str, body: dict,
             db: OrmSession = Depends(db_session),
             user: User = Depends(require_manager)):
    """批量签到/签退/取消签到/取消签退（多选志愿者）。

    body: {"action": "checkin|checkout|cancel_checkin|cancel_checkout",
           "student_ids": ["2023…", …]}
    全部计入留痕。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    # 补签卡在「时长封账」而不是「活动结束」：签到记录是算时长的依据，
    # 账没结完就还得能补
    ensure_hours_editable(db, _act(db, task.activity_id))
    g = db.get(Shift, task.shift_id)
    action = body.get("action")
    sids = [s.strip() for s in (body.get("student_ids") or []) if s.strip()]
    if action not in ("checkin", "checkout", "cancel_checkin", "cancel_checkout"):
        raise HTTPException(400, "未知操作")
    if not sids:
        raise HTTPException(400, "未选择志愿者")

    done, skipped = [], []
    for sid in sids:
        u = db.query(User).filter(User.student_id == sid).first()
        if u is None:
            skipped.append(f"{sid}（学号不存在）")
            continue
        enr = (db.query(Enrollment)
               .filter(Enrollment.shift_id == task.shift_id,
                       Enrollment.user_id == u.id,
                       Enrollment.status == "enrolled").first())
        if enr is None:
            # 不回姓名：这条提示等于「输个学号就能查出他叫什么」——不是本场次
            # 的人，管理者本来也不该从这儿认识他。学号是他自己填进来的，还给他
            skipped.append(f"{sid}（非本场次确认志愿者）")
            continue
        rec = (db.query(CheckinRecord)
               .filter(CheckinRecord.user_id == u.id,
                       CheckinRecord.shift_id == task.shift_id).first())

        if action == "checkin":
            if rec:
                skipped.append(f"{u.name}（已签到）")
                continue
            db.add(CheckinRecord(id=uid("ckr"), task_id=task.id,
                                 activity_id=task.activity_id,
                                 shift_id=task.shift_id,
                                 position_id=enr.position_id, user_id=u.id,
                                 method="manual", by_user=user.id))
            done.append(u.name)
        elif action == "checkout":
            if not rec:
                skipped.append(f"{u.name}（尚未签到）")
                continue
            if rec.checkout_at:
                skipped.append(f"{u.name}（已签退）")
                continue
            rec.checkout_at = now()
            rec.checkout_method = "manual"
            rec.checkout_by = user.id
            done.append(u.name)
        elif action == "cancel_checkin":
            if not rec:
                skipped.append(f"{u.name}（无签到记录）")
                continue
            db.delete(rec)
            done.append(u.name)
        else:  # cancel_checkout
            if not rec or not rec.checkout_at:
                skipped.append(f"{u.name}（无签退记录）")
                continue
            rec.checkout_at = None
            rec.checkout_method = ""
            rec.checkout_by = None
            done.append(u.name)

    ACTION_SUMMARY = {
        "checkin": "手动签到", "checkout": "手动签退",
        "cancel_checkin": "取消签到", "cancel_checkout": "取消签退",
    }
    audit_log(db, actor=user, action="manual_checkin", target_type="checkin",
              target_id=task.id, target_label=g.name,
              activity_id=task.activity_id,
              summary=f"{ACTION_SUMMARY[action]} {len(done)} 人"
                      + (f"；跳过：{'、'.join(skipped[:5])}"
                         + ("…" if len(skipped) > 5 else "") if skipped else ""))
    db.commit()
    return {"ok": True, "done": len(done), "names": done, "skipped": skipped}


# ======================= 导出 =======================

@router.get("/activities/{activity_id}/export.xlsx")
def export(activity_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager_download)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ex = build_activity_workbook(db, act)
    stamp = now().strftime("%Y%m%d_%H%M")
    base = f"{act.title}_排班表_{stamp}"
    buf, ext = ex.bundle(f"{base}.xlsx")
    # 下载留痕（《设计方案》：后台记录每一次下载操作）
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出排班表《{base}.{ext}》")
    db.commit()
    return _download(buf, f"{base}.{ext}")


def _download(buf, filename: str) -> StreamingResponse:
    """带中文文件名的下载响应；.zip 是 Excel 加报名问卷里上传的文件。"""
    from urllib.parse import quote
    media = ("application/zip" if filename.endswith(".zip") else
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    return StreamingResponse(
        buf, media_type=media,
        headers={"Content-Disposition":
                 f"attachment; filename*=UTF-8''{quote(filename)}"})


@router.get("/activities/{activity_id}/hours.xlsx")
def export_hours(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager_download)):
    """时长记录导出：姓名、身份证号、岗位（留空）、时长、备注（明细拼一行）。
    选了劳务的不在其中——平台不给他们记时长。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    buf = build_hours_workbook(db, act)
    stamp = now().strftime("%Y%m%d_%H%M")
    filename = f"{act.title}_时长记录_{stamp}.xlsx"
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出时长记录《{filename}》")
    db.commit()
    return _download(buf, filename)


@router.get("/activities/{activity_id}/applications.xlsx")
def export_applications(activity_id: str, db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager_download)):
    """报名问卷结果导出。筛选是在表格里做的事，答案摊平成一人一行。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not act.is_screened():
        raise HTTPException(409, "只有「报名后筛选」的活动有报名问卷")
    ex = build_application_workbook(db, act)
    stamp = now().strftime("%Y%m%d_%H%M")
    base = f"{act.title}_报名问卷_{stamp}"
    buf, ext = ex.bundle(f"{base}.xlsx")
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出报名问卷结果《{base}.{ext}》"
                      + ("，含上传的附件" if ext == "zip" else ""))
    db.commit()
    return _download(buf, f"{base}.{ext}")


def _indent(text: str, pad: str = "      ") -> str:
    """多行文字接在「服务任务：」这类标签后面，第二行起缩进到与正文对齐。"""
    lines = [l.rstrip() for l in text.strip().splitlines()]
    return ("\n" + pad).join(lines)


@router.get("/activities/{activity_id}/poster")
def poster(activity_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """招募版活动信息（纯文本，供复制到群里/推送）。

    只写对外能公开的：时间地点、岗位与名额、要求与保障、联系人。
    不含任何志愿者名单——名单要用「导出排班表」，那条路有下载留痕。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    L: list[str] = [f"【{act.title}】志愿者招募",
                    f"主办：{act.organizer or '—'}",
                    f"类型：{act.recruit_label()}"]
    span = act.time_span()
    if span:
        L.append(f"时间：{span[0]:%Y-%m-%d %H:%M} ~ {span[1]:%m-%d %H:%M}")
    L.append(f"地点：{act.location or '待定'}")
    L.append(f"招募范围：{recruit_scope_text(act)}")
    if act.reg_end:
        L.append(f"报名截止：{act.reg_end:%Y-%m-%d %H:%M}")
    if act.intro:
        L += ["", "【活动简介】", act.intro.strip()]
    if act.volunteer_requirement:
        L += ["", "【志愿者要求】", act.volunteer_requirement.strip()]
    if act.volunteer_benefit:
        L += ["", "【志愿者保障】", act.volunteer_benefit.strip()]

    L += ["", "【岗位安排】"]
    for g in sorted(act.main_shifts(), key=lambda x: x.start_time):
        head = f"◆ {g.name}（{g.span_text()}）"
        if g.needs_assign():
            head += f"　名额 {g.total_capacity()} 人，岗位于报名截止后统一分配"
        L.append(head)
        for p in sorted(g.positions, key=lambda x: x.sort):
            left = max(0, p.quota - position_enrolled_count(db, p.id))
            line = (f"  · {p.name}　{p.quota} 人"
                    + (f"（剩 {left}）" if left else "（已满）")
                    + (f"　{p.location}" if p.location else ""))
            if p.service_task:
                line += "\n    服务任务：" + _indent(p.service_task)
            if p.attire_text():
                line += f"\n    志愿服装：{p.attire_text()}"
            if p.pay_text():
                line += f"\n    报酬方式：{p.pay_text()}"
            L.append(line)
        for s in sorted((x for x in act.shifts if x.parent_id == g.id),
                        key=lambda x: x.start_time):
            who = "全体" if not s.applies_to_positions else "、".join(
                p.name for p in g.positions if p.id in s.applies_to_positions)
            L.append(f"  ▷ {s.kind_label()}：{s.span_text()}"
                     + (f"　{s.location}" if s.location else "")
                     + f"　（{who}需参加"
                     + ("，不计时长）" if s.hours_mode == "none" else "，计入时长）")
                     + (f"\n    备注：{s.note}" if s.note else ""))

    contacts = "；".join(f"{c.get('name', '')} {c.get('phone', '')}".strip()
                        for c in (act.contacts or []) if isinstance(c, dict))
    if contacts:
        L += ["", f"【联系方式】{contacts}"]
    L += ["", "报名方式：登录志愿服务管理平台，在「活动广场」找到本活动报名。"]
    return {"text": "\n".join(L)}


# ======================= 时长核准与锁定 =======================

class ApproveHoursBody(BaseModel):
    appeal_deadline: str = ""     # 留空 = 明天中午


def _parse_deadline(raw: str) -> datetime:
    if not raw.strip():
        return hours_flow.default_appeal_deadline()
    try:
        deadline = parse_local_dt(raw)
    except ValueError:
        raise HTTPException(400, "申诉截止时间格式不对") from None
    if deadline <= now():
        raise HTTPException(400, "申诉截止时间要晚于现在")
    return deadline


def _hours_approvers(db: OrmSession, act: Activity) -> list[User]:
    """谁能拍锁定与解锁：归口部门的部长（启用值班的部门加上值班人），
    加上会长团与平台管理员。"""
    out = []
    if act.approval_dept_id:
        out += (db.query(User)
                .filter(User.department_id == act.approval_dept_id,
                        User.qingxie_post == "head").all())
        duty = liaison.on_duty(db, db.get(Department, act.approval_dept_id))
        if duty is not None:
            out.append(duty)
    # 全库查人：当前用户本人也在里面、带着身份视角，按真实身份算
    out += [u for u in db.query(User).all() if u.has_full_access(real=True)]
    seen, uniq = set(), []
    for u in out:
        if u.id not in seen:
            seen.add(u.id)
            uniq.append(u)
    return uniq


def council_members(db: OrmSession) -> list[User]:
    """会长团。按真实身份：问的是这些人是谁，不是当前操作者此刻的视角。"""
    return [u for u in db.query(User).filter(User.qingxie_post == "council").all()
            if u.is_council(real=True)]


def _hours_notify_targets(db: OrmSession, act: Activity) -> list[User]:
    """时长审批（锁定与解锁）逐条通知谁：只发归口部门的对接人——有值班人发
    值班人，否则发部长。会长团与管理员照样能在审批页拍板，但不再逐条收通知：
    时长审批量大，逐条抄送的结果是他们把通知关掉；放太久没人处理，另有
    scheduled.remind_stale_hours_requests 催对接人并抄送会长团。

    对接人找不到（没有归口部门、部门没设部长）就兜底发会长团：这时候没有
    别人会收到这条审批，不发就只能等超时催办才有人知道。
    """
    contact = liaison.approval_contact(db, act.approval_dept_id)
    if contact is not None:
        return [contact]
    return council_members(db)


CHECKOUT_GRACE = timedelta(hours=config.CHECKOUT_GRACE_HOURS)


def _round_half_up(hours: float) -> float:
    """向上取整至 0.5 的倍数：2h13m → 2.5h。"""
    return math.ceil(max(0.0, hours) * 2) / 2


def effective_checkout(rec: CheckinRecord, g: Shift):
    """签退时间判定：
       · 已签退 → 实际签退时间；
       · 未签退但已超过「场次结束+2小时」→ 按场次结束时间兜底；
       · 宽限期内未签退 → None（时长待定）。"""
    if rec.checkout_at:
        return rec.checkout_at, "actual"
    deadline = g.end_time + CHECKOUT_GRACE
    if now() > deadline:
        return g.end_time, "auto"
    return None, "pending"


def shift_hours(rec: CheckinRecord, g: Shift, t: datetime):
    """一条签到记录在该场次记多少小时、要不要人工过目，**以及按哪段时间算的**。

    时长认定方式是建场次时定下的一次决定，不是事后逐条判断：
      fixed  到场即按场次时长计——两小时的彩排人就在那儿，不值得卡分钟，
             顺带消灭了「忘记签退」这类麻烦的一大半；
      none   只记出勤，不产生时长（线上培训）；
      checkin 签退 − 签到，未签退按宽限期规则兜底并标记待核。

    返回 (小时数, 待核标记, 计时起点, 计时终点)。时段跟着一起给：备注里写的
    就是它，只回小时数的话，明细和数字会各说各的。
    """
    if g.hours_mode == "none":
        return 0.0, "", None, None
    if g.hours_mode == "fixed":
        # 到场即按场次时长：写场次的起止，不写他实际几点到的
        return ((g.end_time - g.start_time).total_seconds() / 3600, "",
                g.start_time, g.end_time)
    checkout, kind = effective_checkout(rec, g)
    if checkout is None:
        end = min(t, g.end_time)
        return ((end - rec.checkin_at).total_seconds() / 3600, "pending",
                rec.checkin_at, end)
    return ((checkout - rec.checkin_at).total_seconds() / 3600,
            "auto" if kind == "auto" else "", rec.checkin_at, checkout)


def hours_record_of(db: OrmSession, activity_id: str, user_id: str
                    ) -> HoursRecord | None:
    """一个人在一场活动里的那一笔时长。

    **一场活动一个人只有一笔**：签到算出来的和手动补录的合在一起（手动的那些
    段落在 parts 里，kind="manual"）。分开记的话，合计是两处、通知是两条、
    本人还得分别确认两回——同一个活动的同一笔账，没有理由拆开。
    """
    return (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == activity_id,
                    HoursRecord.user_id == user_id)
            .order_by(HoursRecord.created_at).first())


def manual_parts(rec: HoursRecord | None) -> list:
    """这一笔里手动补录的那几段。"""
    return [p for p in ((rec.parts if rec else None) or [])
            if p.get("kind") == "manual"]


def compute_hours(db: OrmSession, act: Activity) -> list[dict]:
    """基于签到/签退记录计算时长：签退 - 签到，向上取整至 0.5 小时。

    未签退且在宽限期内 → 标记 pending（暂按当前时间估算展示）；
    超过宽限未签退 → 按场次结束时间兜底，并标记 auto_checkout **待核**：
    到场扫个码就走的人和全程服务的人算出来一样多，这类记录必须由管理者
    过目——「确认全部」会跳过它们，见 hours_confirm。

    手动补录的时长也算进合计、并按其原样列进明细：管理者看到的这张表就是
    这场活动的总账，漏了手动那部分，这个数就是错的。
    """
    t = now()
    records = (db.query(CheckinRecord)
               .filter(CheckinRecord.activity_id == act.id)
               .order_by(CheckinRecord.checkin_at).all())
    mine = {r.user_id: r for r in db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id).all()}
    extra = {uid_: manual_parts(r) for uid_, r in mine.items()}
    per_user: dict[str, float] = {}
    flags: dict[str, set] = {}
    parts: dict[str, list] = {}
    for rec in records:
        g = db.get(Shift, rec.shift_id)
        if g is None:
            continue
        hours, flag, start, end = shift_hours(rec, g, t)
        if flag:
            flags.setdefault(rec.user_id, set()).add(flag)
        per_user[rec.user_id] = per_user.get(rec.user_id, 0.0) + max(0.0, hours)
        # 明细一段一段记：哪一场、什么时段、什么岗位、几小时。
        # 时段用的是**这次计时实际依据的那一段**，跟上面那个小时数同源——
        # 备注要能解释这个数，各写各的就没法对账。0 小时的场次不占一行
        if hours <= 0:
            continue
        pos = db.get(Position, rec.position_id) if rec.position_id else None
        if pos is None and not g.is_attached():
            enr = (db.query(Enrollment)
                   .filter(Enrollment.shift_id == g.id,
                           Enrollment.user_id == rec.user_id,
                           Enrollment.status == "enrolled").first())
            pos = db.get(Position, enr.position_id) if enr and enr.position_id else None
        parts.setdefault(rec.user_id, []).append({
            "label": g.name if not g.is_attached() else f"{g.name}（{g.kind_label()}）",
            "time": span_text(start, end) if start and end else g.span_text(),
            "position": pos.name if pos and not pos.is_placeholder else "",
            "hours": round(max(0.0, hours), 2), "kind": "shift"})

    # 只有手动补录、一场都没签到过的人也要出现在表里：不然这笔账管理者看不见，
    # 也就无从核准
    for uid_ in extra:
        per_user.setdefault(uid_, 0.0)

    out = []
    for uid_, raw in sorted(per_user.items(), key=lambda kv: -kv[1]):
        fl = flags.get(uid_, set())
        manual = extra.get(uid_, [])
        total = raw + sum(float(p.get("hours") or 0) for p in manual)
        if total <= 0 and not manual:
            # 只去了「不计时长」的场合（如线上培训）：出勤在签到那边看。
            # 有手动段的留着——扣到 0 的人也得在表里，不然那几段加减没处看、没处删
            continue
        u = db.get(User, uid_)
        existing = mine.get(uid_)
        out.append({
            "user": {"id": u.id, "name": u.name,
                     "student_id": u.student_id, "college": u.college},
            "hours": _round_half_up(total),
            "pending": "pending" in fl,      # 未签退（宽限期内）
            "auto_checkout": "auto" in fl,   # 超时自动按场次结束时间
            "confirmed": existing is not None,
            # 手动加的那几段要能删，得让前端拿到这一笔的 id（还没确认过的人
            # 没有记录，也就没有可删的段）
            "record_id": existing.id if existing else "",
            # 签到算出来的在前、手动补录的追加在后：后者是对这笔账的补充说明
            "parts": parts.get(uid_, []) + manual,
            **_volunteer_side(existing),
        })
    return out


def _volunteer_side(rec: HoursRecord | None) -> dict:
    """志愿者本人那一边的表态：报酬方式与确认时间。"""
    if rec is None:
        return {"reward": "hours", "reward_label": "志愿时长",
                "volunteer_confirmed_at": None}
    return {"reward": rec.reward, "reward_label": rec.reward_label(),
            "volunteer_confirmed_at": iso_of(rec.confirmed_at)}


@router.get("/activities/{activity_id}/hours")
def hours_preview(activity_id: str, db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    pending_appeals = (db.query(HoursAppeal)
                       .filter(HoursAppeal.activity_id == act.id,
                               HoursAppeal.status == "pending").count())
    req = (db.query(HoursRequest)
           .filter(HoursRequest.activity_id == act.id)
           .order_by(HoursRequest.created_at.desc()).first())
    return {"items": compute_hours(db, act),
            "status_label": act.label_status(),
            # 核准状态：这一页所有按钮都按它来
            "approved_at": iso_of(act.hours_approved_at),
            "appeal_deadline": iso_of(act.appeal_deadline),
            "locked_at": iso_of(act.hours_locked_at),
            "pending_appeals": pending_appeals,
            "default_deadline": iso(hours_flow.default_appeal_deadline()),
            # 锁定审批挂着的时候整页冻结：部长看的和最终封的得是同一份账
            "lock_pending": hours_flow.pending_lock(db, act.id) is not None,
            # 对外活动还缺盖章立项书：报批按钮前先提示，别点了才被拦
            "stamped_missing": (liaison.is_external(db, act) and liaison.project_file(
                db, act.id, "stamped") is None),
            "request": ({"id": req.id, "kind": req.kind,
                         "kind_label": req.kind_label(), "status": req.status,
                         "status_label": req.status_label(),
                         "reason": req.reason, "reply": req.reply,
                         "created_at": iso(req.created_at)}
                        if req else None)}


class ManualHoursBody(BaseModel):
    """手动加/扣时长。items: [{student_id, hours, note}]，hours 为负就是扣减。

    备注必填：手动改的是账，事后只有审计和这条备注能说明为什么——「场外支援
    2 小时」和「扣掉中途离场 1 小时」在台账上得看得出来。
    """
    items: list[dict]


@router.post("/activities/{activity_id}/hours/manual")
def hours_manual(activity_id: str, body: ManualHoursBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """管理者为志愿者手动添加时长（不依赖签到），可批量。

    并进这个人在这场活动里的那一笔（见 hours_record_of），不另起一条：合计要
    是一个数，通知才只发一条，本人也只需确认一次。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_hours_editable(db, act)
    if not body.items:
        raise HTTPException(400, "空列表")
    added = []
    for it in body.items:
        u = db.query(User).filter(
            User.student_id == (it.get("student_id") or "").strip()).first()
        if u is None:
            raise HTTPException(404, f"学号 {it.get('student_id')} 不存在")
        try:
            hours = float(it.get("hours"))
        except (TypeError, ValueError):
            raise HTTPException(400, f"{u.name} 的时长不是数字")
        if not math.isfinite(hours):
            # NaN / inf 能过下面所有比较（NaN 跟谁比都是假），存进去之后
            # 每个返回这笔账的接口都序列化不出 JSON
            raise HTTPException(400, f"{u.name} 的时长不是有效数字")
        if hours == 0:
            raise HTTPException(400, f"{u.name} 的时长不能是 0")
        note = (it.get("note") or "").strip()
        if not note:
            # 手动动的就是账：事后只有这条备注能说明为什么加、为什么扣
            raise HTTPException(400, f"{u.name} 的备注必填（写明加/扣的原因）")
        rec = hours_record_of(db, act.id, u.id)
        if hours < 0:
            if rec is None:
                raise HTTPException(400, f"{u.name} 这场活动还没有时长记录，"
                                         f"没有可扣的")
            if (rec.hours or 0) + hours < 0:
                # 不偷偷扣到 0 了事：说清楚现在有多少，让管理者自己决定。
                # 还没「确认全部」时，这一笔里只有手动补录的那几段——签到算出来
                # 的时长还没进账，扣不到它头上，得先确认
                hint = ("（签到算出的时长还没核准进账，请先「确认全部」再扣）"
                        if not any(p.get("kind") == "shift"
                                   for p in (rec.parts or [])) else "")
                raise HTTPException(
                    400, f"{u.name} 当前只有 {rec.hours:g} 小时，"
                         f"扣不了 {-hours:g} 小时{hint}")
        # id 是这一段的编号，删除时按它找（见 hours_manual_delete）
        part = {"id": uid("seg"), "label": note, "time": "",
                "position": "", "hours": round(hours, 2), "kind": "manual"}
        if rec is None:
            db.add(HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id,
                               hours=part["hours"], source="manual", note=note,
                               parts=[part], confirmed_by=user.id))
            # 会话不自动 flush：同一批里这个人再出现一次时，hours_record_of
            # 查不到刚加的这条，会给他再建一笔
            db.flush()
        else:
            rec.hours = round((rec.hours or 0) + part["hours"], 2)
            rec.parts = (rec.parts or []) + [part]
            rec.confirmed_by = user.id
            # 数值变了，本人之前那句「确认无误」是对着旧数字说的，不算数了
            rec.confirmed_at = None
        added.append(f"{u.name} {'扣减' if hours < 0 else '添加'}{abs(hours):g}h")
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"手动调整时长：{'、'.join(added)}")
    db.commit()
    return {"ok": True, "count": len(added)}


@router.delete("/activities/{activity_id}/hours/manual/{record_id}/{part_id}")
def hours_manual_delete(activity_id: str, record_id: str, part_id: str,
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """删掉手动加的那**一段**（不是整笔账），按补录时发的段编号找。

    不能按「它在明细里排第几」找：管理端看到的明细是 compute_hours 现拼的
    （签到段在前、手动段在后），而还没「确认全部」过的人，库里这一笔只存了
    手动那几段——两边顺序对不上，按位置取会删掉另一段，合计和审计也跟着错。

    只认 kind="manual" 的段：签到算出来的那些段没有编号，是记录的依据，删不得
    ——要改就改合计（hours/confirm 的 items）。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rec = db.get(HoursRecord, record_id)
    if rec is None or rec.activity_id != act.id:
        raise HTTPException(404, "时长记录不存在")
    ensure_hours_editable(db, act)
    parts = list(rec.parts or [])
    hit = next((i for i, p in enumerate(parts)
                if p.get("kind") == "manual" and p.get("id") == part_id), None)
    if hit is None:
        raise HTTPException(404, "这一段不是手动添加的时长")
    gone = parts.pop(hit)
    hours = round(float(gone.get("hours") or 0), 2)
    left = round((rec.hours or 0) - hours, 2)
    if left < 0:
        # 不能截到 0 了事：截掉的那一截会在删扣减段时被「加回来」，账凭空变多
        # （加 2、扣 5、删掉加的那段 → 截成 0 → 再删扣的那段 → 多出 1 小时）
        raise HTTPException(
            409, f"删掉这段后合计会变成 {left:g} 小时：它已经被后面的扣减用掉了，"
                 f"请先删掉扣减的那段")
    rec.hours = left
    rec.parts = parts
    rec.confirmed_at = None          # 数值变了，本人得重新表个态
    u = db.get(User, rec.user_id)
    # 这笔账本来就是手动加出来的，删空了就该消失，别留一条 0 小时的记录在表里
    if not parts and rec.hours <= 0:
        db.delete(rec)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"删除手动时长：{u.name if u else '?'} {hours}h"
                      f"（{gone.get('label')}）")
    db.commit()
    return {"ok": True}


class HoursConfirmBody(BaseModel):
    items: list[dict] | None = None   # [{student_id, hours}] 指定部分志愿者并覆盖时长


@router.post("/activities/{activity_id}/hours/confirm")
def hours_confirm(activity_id: str, body: HoursConfirmBody | None = None,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """确认时长（依据签到记录），幂等可重复执行。

    · 不带 items → 全部志愿者按计算值确认（原逻辑）；
    · 带 items   → 只确认所选志愿者，时长以请求值为准（覆盖计算值）。

    确认只是把账算出来放在这儿，志愿者还看不到——要「核准」了才通知本人。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_hours_editable(db, act)

    kept: list[str] = []
    items = compute_hours(db, act)
    computed_parts = {it["user"]["id"]: it["parts"] for it in items}

    def upsert(user_id: str, hours: float, *, force: bool = False) -> None:
        # 明细按签到算出来的段落记；管理者改的是合计，段落是它的依据。
        # 手动补录的那几段不用在这儿保住：compute_hours 就是从这一笔的 parts
        # 里把它们读出来再拼回去的（所以这里再拼一次反而会重复一遍）。
        # 当初把手动时长拆成单独一张表，图的是「确认全部」别把它抹掉；
        # 现在拆开记、合起来算，同一个目的换了个不重复的写法。
        existing = hours_record_of(db, act.id, user_id)
        parts = computed_parts.get(user_id, [])
        if existing:
            # 采纳过申诉的记录不被「确认全部」推回计算值：那次改判是人判的
            settled = db.query(HoursAppeal).filter(
                HoursAppeal.record_id == existing.id,
                HoursAppeal.status == "accepted").first()
            if settled is not None and not force:
                u = db.get(User, user_id)
                kept.append(u.name if u else user_id)
                return
            existing.hours = hours
            existing.parts = parts
            return
        db.add(HoursRecord(id=uid("hrs"), activity_id=act.id,
                           user_id=user_id, hours=hours, source="checkin",
                           parts=parts, confirmed_by=user.id))

    chosen = (body.items if body else None) or []
    if chosen:
        targets: list[tuple[str, float]] = []
        for it in chosen:
            sid = str(it.get("student_id") or "").strip()
            if not sid:
                raise HTTPException(400, "缺少学号")
            try:
                hours = float(it.get("hours"))
            except (TypeError, ValueError):
                raise HTTPException(400, f"学号 {sid} 的时长不是数字")
            if hours < 0:
                raise HTTPException(400, f"学号 {sid} 的时长不能为负数")
            u = db.query(User).filter(User.student_id == sid).first()
            if u is None:
                raise HTTPException(404, f"学号 {sid} 不存在")
            targets.append((u.id, round(hours, 2)))
        for uid_, h in targets:
            upsert(uid_, h, force=True)   # 逐个点名改，就是人判的，照改
        count = len(targets)
        detail = "、".join(f"{db.get(User, u_).name}{h:g}h"
                           for u_, h in targets[:8])
        summary = (f"部分确认 {count} 人志愿时长（以管理端填写为准）：{detail}"
                   + ("…" if count > 8 else ""))
    else:
        if not items:
            raise HTTPException(400, "暂无签到数据，无法确认时长")
        # 未签退（宽限期内）与超时自动兜底的记录不在此列：时长存疑，
        # 需要管理者在表格里逐条核对数值后单独确认
        clear = [it for it in items
                 if not it["pending"] and not it["auto_checkout"]]
        review = [it for it in items if it not in clear]
        if not clear:
            raise HTTPException(
                400, f"{len(review)} 条记录未正常签退，时长待核，"
                     f"请在表格中逐条核对后确认")
        for it in clear:
            upsert(it["user"]["id"], it["hours"])
        count = len(clear) - len(kept)
        summary = (f"确认 {count} 人志愿时长（依据签到签退记录）"
                   + (f"；{len(review)} 人未正常签退、时长待核未确认"
                      if review else "")
                   + (f"；{'、'.join(kept)} 的时长由申诉改判过，保留不覆盖"
                      if kept else ""))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary)
    db.commit()
    return {"ok": True, "count": count, "message": summary}


# ======================= 时长申诉 =======================

@router.get("/activities/{activity_id}/hours-appeals")
def list_appeals(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    rows = (db.query(HoursAppeal)
            .filter(HoursAppeal.activity_id == activity_id)
            .order_by(HoursAppeal.status.desc(),
                      HoursAppeal.created_at.desc()).all())
    out = []
    for a in rows:
        u = db.get(User, a.user_id)
        rec = db.get(HoursRecord, a.record_id)
        out.append({"id": a.id, "status": a.status, "label": a.status_label(),
                    "name": u.name if u else "?",
                    "student_id": u.student_id if u else "",
                    "old_hours": a.old_hours, "claimed_hours": a.claimed_hours,
                    "current_hours": rec.hours if rec else None,
                    "locked": act.hours_locked(),
                    "reason": a.reason, "reply": a.reply,
                    "final_hours": a.final_hours,
                    "created_at": iso(a.created_at)})
    return out


class ResolveAppealBody(BaseModel):
    accept: bool
    hours: float | None = None    # 采纳时可改判成第三个数，留空则按申报值
    reply: str = ""


@router.post("/hours-appeals/{appeal_id}/resolve")
def resolve_appeal(appeal_id: str, body: ResolveAppealBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """采纳或驳回一条时长申诉。采纳即改写时长，全程留痕并回告本人。"""
    a = db.get(HoursAppeal, appeal_id)
    if a is None:
        raise HTTPException(404, "申诉不存在")
    ensure_activity_manager(db, user, a.activity_id)
    ensure_hours_editable(db, _act(db, a.activity_id))
    if a.status != "pending":
        raise HTTPException(409, f"该申诉已{a.status_label()}")
    rec = db.get(HoursRecord, a.record_id)
    if rec is None:
        raise HTTPException(404, "对应的时长记录已不存在")
    act = _act(db, a.activity_id)
    who = db.get(User, a.user_id)

    if body.accept:
        hours = a.claimed_hours if body.hours is None else round(body.hours, 2)
        if hours <= 0:
            raise HTTPException(400, "时长必须大于 0")
        before = rec.hours
        rec.hours = hours
        rec.confirmed_by = user.id
        a.final_hours = hours
        a.status = "accepted"
        summary = (f"采纳 {who.name if who else ''} 的时长申诉："
                   f"{before:g}h → {hours:g}h（申报 {a.claimed_hours:g}h）")
    else:
        a.status = "rejected"
        summary = (f"驳回 {who.name if who else ''} 的时长申诉"
                   f"（申报 {a.claimed_hours:g}h，维持 {rec.hours:g}h）")
    a.reply = body.reply.strip()
    a.handled_by = user.id
    a.handled_at = now()

    if who is not None:
        msgcenter.send_user(
            db, who, "custom", data={"route": "hours"},
            title=f"时长申诉{'已采纳' if body.accept else '未采纳'}｜{act.title}",
            content=(f"您对「{act.title}」时长的申诉已处理。\n"
                     f"结果：{'采纳' if body.accept else '未采纳'}，"
                     f"当前时长 {rec.hours:g} 小时。"
                     + (f"\n说明：{a.reply}" if a.reply else "")))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary + (f"；说明：{a.reply}" if a.reply else ""))
    db.commit()
    return {"ok": True, "status": a.status, "hours": rec.hours}


@router.post("/activities/{activity_id}/hours/approve")
def hours_approve(activity_id: str, body: ApproveHoursBody,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """确认核准时长：把账定下来，逐个通知本人，并给一段确认/申诉时间。

    截止之前志愿者要么确认无误（顺带选志愿时长还是劳务），要么提申诉；
    到点没表态的按「没问题 + 志愿时长」处理，定时任务自动提交校青协审批，
    通过即锁定，活动落「待录入志愿北京」。
    有人申诉就通知管理者，改完再核准一次：这一次只惊动数值
    变过、或者申诉过的人，别人的确认照旧有效。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_hours_editable(db, act)
    deadline = _parse_deadline(body.appeal_deadline)
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id).all())
    if not rows:
        # 一条时长都没有的活动没什么可申诉的，直接结账
        hours_flow.lock_hours(db, act, user, "没有任何时长记录，核准时直接锁定")
        db.commit()
        return {"ok": True, "locked": True, "count": 0,
                "message": "本活动没有时长记录，已直接锁定；请录入志愿北京并在"
                           "「审核」页签上传截图后确认完成"}

    since = act.hours_approved_at
    appealed = set()
    if since is not None:
        appealed = {a.user_id for a in db.query(HoursAppeal)
                    .filter(HoursAppeal.activity_id == act.id,
                            HoursAppeal.created_at >= since).all()}
    act.hours_approved_at = now()
    act.appeal_deadline = deadline
    notified = 0
    for r in rows:
        fresh = r.approved_hours is None or r.approved_hours != r.hours
        if since is not None and not fresh and r.user_id not in appealed:
            continue          # 上一轮通知过、数值没变、也没申诉：不再打扰
        r.approved_hours = r.hours
        r.confirmed_at = None
        u = db.get(User, r.user_id)
        if u is None:
            continue
        notified += 1
        # 只有岗位可选劳务的人才需要选报酬方式，别人不必看到这一句
        pay_line = ("\n请选择报酬方式志愿时长或劳务，超时未确认默认选择志愿时长。"
                    if hours_flow.pay_position(db, act.id, r.user_id) else "")
        # 点开去「我的志愿时长」而不是活动详情：要确认/要申诉的那笔账在那儿，
        # 活动详情页上看不到本人在这个活动里有多少时长
        msgcenter.send_user(
            db, u, "hours_approved", data={"route": "hours"},
            activity=act.title, hours=f"{r.hours:g}",
            deadline=f"{deadline:%Y-%m-%d %H:%M}", pay_line=pay_line)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"{'再次' if since else ''}核准志愿时长，通知 {notified} 人，"
                       f"确认/申诉截止 {deadline:%Y-%m-%d %H:%M}"))
    db.commit()
    return {"ok": True, "locked": False, "count": notified,
            "message": f"已核准并通知 {notified} 人，"
                       f"{deadline:%m-%d %H:%M} 前无人申诉即自动锁定"}


class LockHoursBody(BaseModel):
    note: str = ""


@router.post("/activities/{activity_id}/hours/lock-request")
def hours_lock_request(activity_id: str, body: LockHoursBody | None = None,
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """提前提交锁定审批。不等申诉期满、有申诉没处理，都由管理者判断。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ensure_hours_editable(db, act)
    if act.hours_approved_at is None:
        raise HTTPException(409, "请先核准时长")
    # 对外活动的时长要凭盖章立项书报批，没有就不往上送
    if liaison.is_external(db, act) \
            and liaison.project_file(db, act.id, "stamped") is None:
        raise HTTPException(400, "对外活动须先上传盖章立项书，才能提交时长审核")
    pending = (db.query(HoursAppeal)
               .filter(HoursAppeal.activity_id == act.id,
                       HoursAppeal.status == "pending").count())
    note = (body.note.strip() if body and body.note else "")
    reason = "管理者提交" + (f"（{note}）" if note else "")
    if pending:
        reason += f"；{pending} 条申诉未处理"
    req = hours_flow.submit_lock(db, act, user, reason)
    _notify_lock_approvers(db, act, user.name, reason)
    db.commit()
    return {"ok": True, "id": req.id if req else "", "pending_appeals": pending}


def _notify_lock_approvers(db: OrmSession, act: Activity, who: str,
                           reason: str) -> None:
    for approver in _hours_notify_targets(db, act):
        notify(db, approver, f"时长锁定审批｜{act.title}",
               f"{who}提交了「{act.title}」的志愿时长，等待校青协审核。\n"
               f"说明：{reason}\n通过后时长锁定，管理者录入志愿北京后活动完成；"
               f"驳回则退回管理者修改。\n请到「活动审批」处理。",
               data={"activity_id": act.id, "route": "approvals"}, need_ack=True)


class UnlockRequestBody(BaseModel):
    reason: str


@router.post("/activities/{activity_id}/hours/unlock-request")
def request_unlock(activity_id: str, body: UnlockRequestBody,
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager)):
    """锁定之后要改时长，报归口部长审批。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not act.hours_locked():
        raise HTTPException(409, "时长尚未锁定，可直接修改")
    reason = body.reason.strip()
    if len(reason) < 5:
        raise HTTPException(400, "请写明修改内容与原因（至少 5 个字）")
    if (db.query(HoursRequest)
            .filter(HoursRequest.activity_id == act.id,
                    HoursRequest.kind == "unlock",
                    HoursRequest.status == "pending").first()):
        raise HTTPException(409, "已有一条解锁申请在审批中")
    req = HoursRequest(id=uid("hrq"), activity_id=act.id, kind="unlock",
                       reason=reason, requested_by=user.id)
    db.add(req)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"申请解锁时长：{reason}")
    for approver in _hours_notify_targets(db, act):
        notify(db, approver, f"时长解锁申请｜{act.title}",
               f"{user.name} 申请解锁「{act.title}」已锁定的志愿时长。\n"
               f"理由：{reason}\n请到「活动审批」处理。",
               data={"activity_id": act.id, "route": "approvals"}, need_ack=True)
    db.commit()
    return {"ok": True, "id": req.id}


class HoursDecideBody(BaseModel):
    approve: bool
    reply: str = ""


@router.post("/hours-requests/{request_id}/decide")
def decide_hours_request(request_id: str, body: HoursDecideBody,
                         db: OrmSession = Depends(db_session),
                         user: User = Depends(require_manager)):
    """归口部长（或会长团/管理员）拍板锁不锁、解不解。

    锁定通过 = 这场活动的时长经校青协审核，就此封账、活动落「待录入志愿北京」
    （管理者上传录入截图并确认后才算已完成）；驳回则解冻，管理者改完重新核准，
    再提交一次。
    解锁通过 = 回到「已结束」：不管此前停在待录入志愿北京还是已完成，账重新
    打开就回到结账之前。之前传的志愿北京录入截图随之作废（截的是改之前的
    时长），重新锁定后再录一遍、传新的截图。
    """
    req = db.get(HoursRequest, request_id)
    if req is None:
        raise HTTPException(404, "申请不存在")
    if req.status != "pending":
        raise HTTPException(409, f"该申请{req.status_label()}")
    act = _act(db, req.activity_id)
    if user.id not in {u.id for u in _hours_approvers(db, act)}:
        raise HTTPException(403, f"只有归口部门的部长可以处理时长{req.kind_label()}申请")
    reply = body.reply.strip()
    if not body.approve and len(reply) < 2:
        raise HTTPException(400, "驳回必须写明理由")
    req.status = "approved" if body.approve else "rejected"
    req.reply, req.handled_by, req.handled_at = reply, user.id, now()
    if body.approve and req.kind == "lock":
        hours_flow.lock_hours(db, act, user, f"校青协审核通过（{req.reason}）")
    elif body.approve:
        act.hours_locked_at = None
        act.status = "finished"
        dropped = liaison.drop_project_file(db, act.id, "vb_hours")
        if dropped is not None:
            audit_log(db, actor=user, action="material", target_type="activity",
                      target_id=act.id, target_label=act.title,
                      activity_id=act.id,
                      summary=f"时长解锁，志愿北京时长录入截图《{dropped.title}》作废")
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"同意{req.kind_label()}时长（{req.reason}）" if body.approve
                       else f"驳回时长{req.kind_label()}申请：{reply}"))
    requester = db.get(User, req.requested_by)
    if requester is not None:
        notify(db, requester,
               f"时长{req.kind_label()}{'已通过' if body.approve else '未通过'}"
               f"｜{act.title}",
               _decide_text(act, req, body.approve, reply),
               data={"activity_id": act.id})
    db.commit()
    return {"ok": True, "status": req.status}


def _decide_text(act: Activity, req, approve: bool, reply: str) -> str:
    if not approve:
        return (f"时长{req.kind_label()}申请未通过。意见：{reply}\n"
                + ("请修改后重新核准，再提交一次审批。" if req.kind == "lock"
                   else "如仍需修改，请补充说明后重新申请。"))
    tail = f"\n审批意见：{reply}" if reply else ""
    if req.kind == "lock":
        return (f"「{act.title}」的志愿时长已通过校青协审核并锁定。\n"
                f"请把时长录入志愿北京，在活动管理「审核」页签上传录入截图并确认，"
                f"活动即标记为已完成。{tail}")
    return f"「{act.title}」的时长已解锁，修改后请重新核准。{tail}"


@router.get("/activities/{activity_id}/hours-requests")
def activity_hours_requests(activity_id: str, db: OrmSession = Depends(db_session),
                            user: User = Depends(require_manager)):
    """这场活动所有的时长审核（锁定与解锁），新的在前。给活动管理页「审核」
    页签看：提交在「时长管理」里点，审到哪一步、谁批的、意见是什么在这里看。
    管理者与这场活动的时长审批人都能看。"""
    act = _act(db, activity_id)
    approvers = _hours_approvers(db, act)
    if user.id not in {u.id for u in approvers}:
        ensure_activity_manager(db, user, activity_id)
    rows = (db.query(HoursRequest)
            .filter(HoursRequest.activity_id == act.id)
            .order_by(HoursRequest.created_at.desc()).all())

    def name(uid_):
        u = db.get(User, uid_) if uid_ else None
        return u.name if u else ""
    dept = db.get(Department, act.approval_dept_id) if act.approval_dept_id else None
    head = liaison.dept_head(db, dept.id) if dept else None
    duty = liaison.on_duty(db, dept)
    return {
        "department": dept.name if dept else "",
        # 等谁处理：启用值班的部门是值班人，否则是部长（会长团、管理员也能批）
        "handler": (f"{duty.name}（值班）" if duty
                    else (f"{head.name}（部长）" if head else "")),
        "locked_at": iso_of(act.hours_locked_at),
        "items": [{
            "id": r.id, "kind": r.kind, "kind_label": r.kind_label(),
            "status": r.status, "status_label": r.status_label(),
            "reason": r.reason, "reply": r.reply,
            "requested_by": name(r.requested_by) or "系统",
            "created_at": iso(r.created_at),
            "handled_by": name(r.handled_by),
            "handled_at": iso_of(r.handled_at),
        } for r in rows],
    }


@router.get("/hours-requests")
def my_hours_requests(db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """待我处理的时长审批（锁定与解锁），挂在「活动审批」页上。"""
    rows = (db.query(HoursRequest, Activity)
            .join(Activity, HoursRequest.activity_id == Activity.id)
            .filter(HoursRequest.status == "pending")
            .order_by(HoursRequest.created_at).all())
    out = []
    for req, act in rows:
        if user.id not in {u.id for u in _hours_approvers(db, act)}:
            continue
        who = db.get(User, req.requested_by)
        out.append({"id": req.id, "kind": req.kind,
                    "kind_label": req.kind_label(), "activity_id": act.id,
                    "activity_title": act.title, "reason": req.reason,
                    "requested_by": who.name if who else "系统",
                    "created_at": iso(req.created_at)})
    return out
