"""教师/青协端：签到任务、导出中心、时长核准。"""
from __future__ import annotations

import json
import math
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy import and_, func
from sqlalchemy.orm import Session as OrmSession, selectinload

from ..audit import log as audit_log
from ..codes import insert_with_unique_code
from .. import config, downloads, hours as hours_flow, liaison
from ..stage import ensure_hours_editable, ensure_not_finished
from ..database import db_session
from ..deps import require_manager, require_manager_download
from ..exporter import (build_activity_workbook, build_answers_workbook,
                        build_application_workbook, build_hours_workbook)
from ..enroll_logic import Occupancy, shift_locked
from ..perm import ensure_activity_manager
from ..qrcodes import trusted_root
from ..integrations import msgcenter
from ..locks import current_checkin_code
from ..notify import notify, notify_checkin
from ..models import (Activity, CheckinGeo, CheckinRecord, CheckinTask,
                      Department, Enrollment, HoursAppeal, HoursRecord,
                      HoursRequest, HoursSegment, Shift, Position, User,
                      ViolationRecord, day_of, iso, new_checkin_token, now,
                      parse_local_dt, part_is_pay, segment_out, span_text, uid)
from ..serializers import applies_text, iso_of, recruit_scope_phrase
from .checkin import active_period, checkout_open, find_record
from .geo import tiles_configured

router = APIRouter(prefix="/api/staff", tags=["staff-ops"])


def _act(db: OrmSession, activity_id: str, *,
         for_update: bool = False) -> Activity:
    act = ((db.query(Activity).filter(Activity.id == activity_id)
            .populate_existing().with_for_update().one_or_none())
           if for_update else db.get(Activity, activity_id))
    if act is None:
        raise HTTPException(404, "活动不存在")
    return act


# ======================= 签到任务 =======================

class TaskBody(BaseModel):
    mode: str = "shift"                 # shift 统一签到 / position 分岗位
    # 哪一段的码：多时段场次必填；只有一个时段的场次可以不给
    period_id: str | None = None
    position_id: str | None = None      # 单岗位（旧的调用方式）
    position_ids: list[str] = []        # 分岗位时可选多个，一个岗位一张码
    valid_from: str | None = None
    valid_to: str | None = None


def live_task_conflict(db: OrmSession, shift_id: str, period_id: str,
                       position_id: str | None,
                       *, exclude_id: str | None = None) -> CheckinTask | None:
    """这一段的码里有没有已经开着的跟它冲突。

    **统一码算「这一段的所有岗位」**：统一码开着的时候，再给某个岗位开一张，
    学生扫的是哪张、记录落到哪张任务下都说不清（看板还会各算各的）。反过来也
    一样——某个岗位开着码时再开统一的，同样会让两拨人扫到不同的任务。
    不同的时段互不冲突：上午一张、下午一张才是常态。

    自己跟自己不算冲突（改时段、重新开启时要把自己排除掉）。
    """
    q = (db.query(CheckinTask)
         .filter(CheckinTask.shift_id == shift_id,
                 CheckinTask.period_id == period_id,
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
    periods = g.sorted_periods()
    if body.period_id:
        period = g.period_of(body.period_id)
        if period is None:
            raise HTTPException(400, "所选时段不属于该场次")
    elif len(periods) == 1:
        period = periods[0]
    else:
        raise HTTPException(400, "这场有多个时段，请指定是哪个时段的签到码")
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
        live = live_task_conflict(db, g.id, period.id, st.id if st else None)
        if live is not None:
            raise HTTPException(409, _conflict_message(db, live, g))
    pad = timedelta(minutes=config.CHECKIN_WINDOW_PAD_MINUTES)
    made: list[CheckinTask] = []
    for st in (positions or [None]):
        task = insert_with_unique_code(
            db, CheckinTask.token, new_checkin_token,
            lambda code, st=st: CheckinTask(
                id=uid("tsk"), activity_id=act.id, shift_id=g.id,
                period_id=period.id,
                position_id=st.id if st else None,
                mode=body.mode, token=code,
                valid_from=valid_from or period.start_time - pad,
                valid_to=valid_to or period.end_time + pad,
                created_by=user.id))
        label = st.name if st else f"{g.name}·{period.text()}·统一签到"
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
        period = g.period_of(t.period_id)
        st = db.get(Position, t.position_id) if t.position_id else None
        n = (db.query(func.count(CheckinRecord.id))
             .filter(CheckinRecord.task_id == t.id).scalar() or 0)
        x = geos.get(t.id)
        out.append({"id": t.id, "token": t.token, "mode": t.mode,
                    # shift_id 前端要用：一个活动多场次时，码表要按场次分辨，
                    # 手动补签也只能打到当前这块大屏对应的那一场上
                    "shift_id": t.shift_id, "shift_name": g.name,
                    # 这张码属于哪一段：多时段的场次一个时段一张码
                    "period_id": t.period_id,
                    "period_text": period.text() if period else "",
                    "period_count": len(g.periods),
                    # 截止早于这一段结束、或这一段没完就关码，前端要先提醒：
                    # 那之后没签退的人都按这一段结束兜底、进待核
                    "shift_end": iso(period.end_time if period else g.end_time),
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

    不给任何 tk：瓦片由本站转发（见 routers/geo.py），画图的令牌由 GeoMap 自己去
    /api/geo/tile-token 领。tiles 只说「配没配」，没配前端就只显示坐标。
    """
    return {"provider": "tianditu", "tiles": tiles_configured(),
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
        # 现场最常见的意外是场次提前或拖堂。关掉重建会让已经扫过的那张码
        # 作废（记录还挂在老任务下），所以时段要能直接改
        for key in ("valid_from", "valid_to"):
            if key in body:
                raw = (body[key] or "").strip()
                # 起止都不许留空：截止同时是签退截止，过了它没签退的人按场次
                # 结束兜底——「不限」就永远判不出来。开始也一并要求，和生成时
                # 总会填上起止保持一致，不留一种「一端不限」的特例
                if not raw:
                    raise HTTPException(
                        400, "开放和停止签到的时间都要填" if key == "valid_from"
                        else "停止签到时间必填：过了它还没签退的人按场次结束时间记时长")
                try:
                    setattr(task, key, parse_local_dt(raw))
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
            live = live_task_conflict(db, task.shift_id, task.period_id,
                                      task.position_id, exclude_id=task.id)
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
    # 多时段的场次催的是哪一段；不给就取此刻正在进行的、或下一段
    period_id: str | None = None
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

    period = (g.period_of(body.period_id) if body.period_id
              else active_period(g, now()))
    if period is None:
        raise HTTPException(400, "该场次没有服务时段")
    if len(g.periods) > 1:
        scope = f"{scope}·{period.text()}"
    batch_id = uid("bat") if body.need_ack else None
    n = notify_checkin(db, g, period=period, position_ids=ids,
                       need_ack=body.need_ack, batch_id=batch_id)
    if not n:
        raise HTTPException(400, f"{scope}没有需要提醒的志愿者")
    period.reminder_sent_at = now()
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
    # 这一段的码：大屏说的是这一段到了多少人，不把上午的记录算进下午
    recs = (db.query(CheckinRecord)
            .filter(CheckinRecord.shift_id == g.id,
                    CheckinRecord.period_id == task.period_id)
            .order_by(CheckinRecord.checkin_at).all())
    rec_by_user, rounds = _board_records(g, recs, task)

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
            "checkin_count": rounds.get(u.id, 0),
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                  "checked": by_position.get(s.id, 0),
                  "expected": expected_by_position.get(s.id, 0)}
                 for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "flexible_time": g.flexible_time,
        "task": {"id": task.id, "token": task.token, "mode": task.mode,
                 "active": task.active},
        "activity_title": act.title, "shift_name": g.name,
        "period_id": task.period_id,
        "period_text": (g.period_of(task.period_id).text()
                        if task.period_id else ""),
        "expected": len(roster), "checked": checked,
        "checked_out": checked_out,
        "positions": positions, "records": items[:500],
    }


def _board_records(g: Shift, recs: list[CheckinRecord],
                   task: CheckinTask | None
                   ) -> tuple[dict[str, CheckinRecord], dict[str, int]]:
    """大屏上每人显示哪条签到记录，以及各人一共签过几轮。

    recs 按签到时间排好。一般场次一人一条。时间灵活的场次一张码一轮：给了
    task（大屏对应的那张码、或这一场现在开着的码）就只看这张码上的那一轮——
    现场关心的是这一轮谁到了、谁还没签退；没有码开着时显示各人最近的一轮。
    """
    by_user: dict[str, CheckinRecord] = {}
    rounds: dict[str, int] = {}
    for r in recs:
        rounds[r.user_id] = rounds.get(r.user_id, 0) + 1
        if g.flexible_time and task is not None and r.task_id != task.id:
            continue
        by_user[r.user_id] = r
    return by_user, rounds


def _live_task(db: OrmSession, g: Shift, period) -> CheckinTask | None:
    """这一段现在开着的那张码（同一时间只开一张，见 live_task_conflict；
    分岗位的码可能有几张，取最新的，跟签到面板补签用的那张一致）。"""
    return (db.query(CheckinTask)
            .filter(CheckinTask.shift_id == g.id,
                    CheckinTask.period_id == period.id,
                    CheckinTask.active.is_(True))
            .order_by(CheckinTask.created_at.desc()).first())


@router.get("/shifts/{shift_id}/board")
def shift_board(shift_id: str, period_id: str = "",
                db: OrmSession = Depends(db_session),
                user: User = Depends(require_manager)):
    """按场次聚合的签到实时大屏（不依赖激活的签到任务，
    任务关闭/不存在时仍可查看到场名单）。前端 5s 轮询。

    以已报名报名（Enrollment.status=enrolled）join User 为主表，
    每人配上这一段的签到记录：一般场次一人一段一条；时间灵活的场次一人可能
    签过好几轮，配哪一轮见 _board_records。
    给了 period_id 就只看这一段；不给时把各段合在一起看。
    """
    g = db.get(Shift, shift_id)
    if g is None:
        raise HTTPException(404, "场次不存在")
    ensure_activity_manager(db, user, g.activity_id)
    act = db.get(Activity, g.activity_id)
    period = g.period_of(period_id) if period_id else None

    roster = (db.query(Enrollment, User)
              .join(User, Enrollment.user_id == User.id)
              .filter(Enrollment.shift_id == g.id,
                      Enrollment.status == "enrolled")
              .order_by(User.college, User.name).all())
    q = db.query(CheckinRecord).filter(CheckinRecord.shift_id == g.id)
    if period is not None:
        q = q.filter(CheckinRecord.period_id == period.id)
    recs = q.order_by(CheckinRecord.checkin_at).all()
    show_period = period or active_period(g, now())
    task = (_live_task(db, g, show_period) if g.flexible_time else None)
    rec_by_user, rounds = _board_records(g, recs, task)
    rows = [(enr, u, rec_by_user.get(u.id)) for enr, u in roster]

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
            # 现场联系不上人时才找得到人：手机号和微信号跟着名单一起给
            "phone": u.phone or "", "wechat": u.wechat or "",
            "position": st.name if st else "待分配",
            "is_leader": enr.is_leader,
            "checked_in": bool(rec),
            "method": rec.method if rec else "",
            "time": (rec.checkin_at.strftime("%H:%M:%S") if rec else ""),
            "checkout_at": (rec.checkout_at.strftime("%H:%M:%S")
                            if rec and rec.checkout_at else ""),
            "checkout_method": (rec.checkout_method or "") if rec else "",
            "excused": enr.excused, "excuse_reason": enr.excuse_reason,
            "checkin_count": rounds.get(u.id, 0),
        })
    positions = [{"id": s.id, "name": s.name, "quota": s.quota,
                  "checked": by_position.get(s.id, 0),
                  "expected": expected_by_position.get(s.id, 0)}
                 for s in sorted(g.positions, key=lambda x: x.sort)]
    return {
        "activity_id": act.id, "activity_title": act.title,
        "shift_id": g.id, "shift_name": g.name,
        "kind": g.kind, "kind_label": g.kind_label(),
        # 多时段的场次：大屏可以按段切换；不给段时是各段合在一起看的数
        "periods": [{**p.out(),
                     "active": show_period is not None and p.id == show_period.id}
                    for p in g.sorted_periods()],
        "period_id": period.id if period else "",
        # 时间灵活的场次：到岗、签退说的是当前开着的那张码这一轮
        "flexible_time": g.flexible_time,
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

    时间灵活的场次一张码一轮，补签、签退、取消都作用在这张码的那一轮上，
    而且这张码要开着：补签补的是「现在这一轮」，没开码就没有这一轮。要补
    以前某一轮，先把那张码重新开启。
    """
    task = db.get(CheckinTask, task_id)
    if task is None:
        raise HTTPException(404, "任务不存在")
    ensure_activity_manager(db, user, task.activity_id)
    # 补签卡在「时长封账」而不是「活动结束」：签到记录是算时长的依据，
    # 账没结完就还得能补
    ensure_hours_editable(db, _act(db, task.activity_id))
    g = db.get(Shift, task.shift_id)
    if g.flexible_time and not task.active:
        raise HTTPException(409, "时间灵活的场次按签到码分轮次，补签要落在开着的签到码上；"
                                 "请先开启签到码")
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
        rec = find_record(db, u.id, g, task)

        if action == "checkin":
            if rec:
                skipped.append(f"{u.name}（已签到）")
                continue
            db.add(CheckinRecord(id=uid("ckr"), task_id=task.id,
                                 activity_id=task.activity_id,
                                 shift_id=task.shift_id,
                                 period_id=task.period_id,
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

# 问卷结果与报名问卷结果都可能带问卷里上传的文件。表格和附件分开下：
# 「导出」只给 .xlsx；附件包另有接口，前端先问一句附件数（<a> 直链下载
# 拿不到响应头，所以数目走一个单独的 JSON 接口），有附件才询问要不要一起下。

def _applications_export(db: OrmSession, user: User, activity_id: str):
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if not act.is_screened():
        raise HTTPException(409, "只有「报名后筛选」的活动有报名问卷")
    return act, build_application_workbook(db, act), f"{act.title}_报名问卷"


def _ids(raw: str) -> set[str]:
    return {x for x in (raw or "").split(",") if x.strip()}


def _answers_export(db: OrmSession, user: User, activity_id: str,
                    shifts: str, positions: str):
    """问卷结果；shifts / positions 是逗号分隔的 id，空表示不限。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    ex = build_answers_workbook(db, act, _ids(shifts), _ids(positions))
    return act, ex, f"{act.title}_问卷结果"


def _attachments_download(db: OrmSession, user: User, act: Activity, ex,
                          name: str, what: str) -> StreamingResponse:
    buf = ex.attachments_zip()
    if buf is None:
        raise HTTPException(404, "本次导出没有附件")
    stamp = now().strftime("%Y%m%d_%H%M")
    filename = f"{name}_附件_{stamp}.zip"
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出{what}附件《{filename}》，{len(ex.files)} 个文件")
    db.commit()
    return _download(buf, filename)


@router.get("/activities/{activity_id}/export.xlsx")
def export(activity_id: str, db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager_download)):
    """排班表：活动概览、志愿者信息、每个场次一张名单。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    filename = f"{act.title}_排班表_{now():%Y%m%d_%H%M}.xlsx"
    # 下载留痕：后台记录每一次下载操作
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出排班表《{filename}》")
    db.commit()
    return _download(build_activity_workbook(db, act), filename)


@router.get("/activities/{activity_id}/answers.xlsx")
def export_answers(activity_id: str, shifts: str = "", positions: str = "",
                   db: OrmSession = Depends(db_session),
                   user: User = Depends(require_manager_download)):
    """问卷结果：在册志愿者按场次、岗位挑出来，一题一列。"""
    act, ex, name = _answers_export(db, user, activity_id, shifts, positions)
    filename = f"{name}_{now():%Y%m%d_%H%M}.xlsx"
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出问卷结果《{filename}》")
    db.commit()
    return _download(ex.xlsx(), filename)


@router.get("/activities/{activity_id}/answers/attachments")
def export_answer_attachment_count(activity_id: str, shifts: str = "",
                                   positions: str = "",
                                   db: OrmSession = Depends(db_session),
                                   user: User = Depends(require_manager)):
    """问卷结果的附件数：前端据此决定要不要问「是否同时打包下载」。"""
    _, ex, _ = _answers_export(db, user, activity_id, shifts, positions)
    return {"count": len(ex.files)}


@router.get("/activities/{activity_id}/answers/attachments.zip")
def export_answer_attachments(activity_id: str, shifts: str = "",
                              positions: str = "",
                              db: OrmSession = Depends(db_session),
                              user: User = Depends(require_manager_download)):
    """问卷结果里文件题的附件包，按「场次/岗位」分文件夹；没有附件时 404。"""
    act, ex, name = _answers_export(db, user, activity_id, shifts, positions)
    return _attachments_download(db, user, act, ex, name, "问卷结果")


def _download(buf, filename: str) -> StreamingResponse:
    """带中文文件名的下载响应：.xlsx 表格，或 .zip 附件包。"""
    media = downloads.ZIP if filename.endswith(".zip") else downloads.XLSX
    return downloads.stream_response(buf, filename, media)


@router.get("/activities/{activity_id}/hours.xlsx")
def export_hours(activity_id: str, db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager_download)):
    """时长记录导出：姓名、学号、身份证号、时长、备注（明细拼一行）。
    选了劳务的不在其中——平台不给他们记时长。
    另附「劳务」页：本活动有劳务的人一人一行，列出劳务时长与所在时段岗位，
    给发劳务用；党校同学不能选劳务，不会出现在这页。"""
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
    act, ex, name = _applications_export(db, user, activity_id)
    stamp = now().strftime("%Y%m%d_%H%M")
    filename = f"{name}_{stamp}.xlsx"
    audit_log(db, actor=user, action="export", target_type="export",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"导出报名问卷结果《{filename}》")
    db.commit()
    return _download(ex.xlsx(), filename)


@router.get("/activities/{activity_id}/applications/attachments")
def export_application_attachment_count(activity_id: str,
                                        db: OrmSession = Depends(db_session),
                                        user: User = Depends(require_manager)):
    """报名问卷附件数，用法同问卷结果那个。"""
    _, ex, _ = _applications_export(db, user, activity_id)
    return {"count": len(ex.files)}


@router.get("/activities/{activity_id}/applications/attachments.zip")
def export_application_attachments(activity_id: str,
                                   db: OrmSession = Depends(db_session),
                                   user: User = Depends(require_manager_download)):
    """报名问卷里上传的文件打成的包；没有附件时 404。"""
    act, ex, name = _applications_export(db, user, activity_id)
    return _attachments_download(db, user, act, ex, name, "报名问卷")


_WEEKDAYS = "一二三四五六日"


def _cn_day(d: datetime) -> str:
    return f"{d.month}月{d.day}日（周{_WEEKDAYS[d.weekday()]}）"


def _cn_span(start: datetime, end: datetime) -> str:
    """招募文案里的时间：10月18日（周六）07:30—12:00，跨天的两头都写日期。

    只用在招募文案上，系统里别处的时间仍走 models.span_text：同一段服务在
    各个页面上各写各的格式，对账时就得靠眼睛去认。
    """
    if start.date() == end.date():
        return f"{_cn_day(start)}{start:%H:%M}—{end:%H:%M}"
    return f"{_cn_day(start)}{start:%H:%M}—{_cn_day(end)}{end:%H:%M}"


def _one_line(text: str) -> str:
    """多行文字并成一行（分号隔开），接在「岗位名 N人：」后面。"""
    return "；".join(s.strip() for s in (text or "").splitlines() if s.strip())


@router.get("/activities/{activity_id}/poster")
def poster(activity_id: str, request: Request, base: str = "",
           db: OrmSession = Depends(db_session),
           user: User = Depends(require_manager)):
    """招募文案（纯文本，复制到群里/推送），格式由校青协定。

    只写对外能公开的：时间地点、岗位与名额、要求与保障。不含任何志愿者名单——
    名单要用「导出排班表」，那条路有下载留痕。选填的段（培训与彩排、志愿者
    要求）有内容才出现，放在「服务内容」与「志愿者保障」之间。活动时间写一整段
    起止，和网页上的「活动时间」一致；服务内容里场次一行「◆ 」、岗位一行
    「  · 」，岗位的补充信息再缩进一层。[玫瑰] 原样写：
    发到微信里显示成玫瑰表情。报名链接的域名同活动二维码：base 由前端传当前
    站点地址，只认本平台的域名。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    link = f"{trusted_root(request, base)}/activity/{act.id}"
    mains = sorted(act.main_shifts(), key=lambda x: x.start_time)
    # 简介后面要接「，现面向……」，句末的标点先去掉
    intro = (act.intro or "").strip().rstrip("。！!；;，,、.． ")
    L: list[str] = [
        f"志愿招募|{act.title}", "各位同学大家好！",
        "【活动内容简介】",
        f"{intro}{'，' if intro else ''}现面向{recruit_scope_phrase(act)}招募志愿者[玫瑰]",
        "【活动主办单位】", act.organizer_text() or "—",
        "【活动时间】", act.time_text(),
        "【活动地点】", act.location or "待定",
        "【服务内容及招募人数】"]
    for g in mains:
        assign = ""
        if g.needs_assign():
            hold = g.placeholder()
            assign = (f"招募 {hold.quota if hold else g.total_capacity()} 人，"
                      f"岗位于报名截止后统一分配")
        if len(mains) > 1:
            L.append(f"◆ {g.name}　{g.span_text()}"
                     + (f"　{assign}" if assign else ""))
        elif assign:
            L.append(assign)
        for p in sorted(g.real_positions(), key=lambda x: x.sort):
            task = _one_line(p.service_task)
            L.append("  · " + p.name + ("" if g.needs_assign() else f" {p.quota}人")
                     + (f"：{task}" if task else ""))
            extra = [x for x in (
                f"岗位地点：{p.location}" if p.location and p.location != act.location else "",
                f"志愿服装：{p.attire_text()}" if p.attire_text() else "",
                f"报酬方式：{p.pay_text()}" if p.pay_text() else "") if x]
            if extra:
                L.append("    " + "；".join(extra))
    attached = [
        f"{s.span_text()} {s.kind_label()}"
        + (f"，{s.location}" if s.location else "")
        + f"（{applies_text(act, s)}需参加，"
          f"{'不计时长' if s.hours_mode == 'none' else '计入时长'}）"
        + (f"；{_one_line(s.note)}" if s.note else "")
        for s in act.trainings()]
    if attached:
        L += ["【培训与彩排】", *attached]
    if (act.volunteer_requirement or "").strip():
        L += ["【志愿者要求】", act.volunteer_requirement.strip()]
    if (act.precautions or "").strip():
        L += ["【注意事项】", act.precautions.strip()]
    L += ["【志愿者保障】",
          (act.volunteer_benefit or "").strip() or config.DEFAULT_VOLUNTEER_BENEFIT,
          "【报名方式】",
          # 链接两边留空格：紧挨着别的字，微信识别网址时可能把旁边的字一起吞进去
          f"有意愿参加的同学请【点击链接 {link} 或 扫描下方二维码报名】(二选一)，"
          f"报名成功情况将在志愿服务管理平台进行通知。十分感谢大家对志愿活动的支持！"]
    return {"text": "\n".join(L)}


# ======================= 时长核准与锁定 =======================

def _vb_brief(row) -> dict | None:
    """最近一次外联确认，时长管理页顶上的一句话用。"""
    if row is None:
        return None
    return {"id": row.id, "status": row.status, "status_label": row.status_label(),
            "names": row.names, "reply": row.reply}


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


def council_members(db: OrmSession, dept_id: str | None = None) -> list[User]:
    """会长团。按真实身份：问的是这些人是谁，不是当前操作者此刻的视角。
    传入 dept_id 时，按每个人的 remind_dept_ids 过滤（空列表 = 接收全部部门）。
    范围里已经不存在的部门先去掉，去掉后为空同样按全部部门算：要是不去掉，
    一个只勾了已删部门的人就哪个部门的催办都收不到了。"""
    rows = [u for u in db.query(User).filter(User.qingxie_post == "council").all()
            if u.is_council(real=True)]
    if not dept_id:
        return rows
    valid = {d.id for d in db.query(Department.id).all()}

    def wants(u: User) -> bool:
        scope = [d for d in (u.remind_dept_ids or []) if d in valid]
        return not scope or dept_id in scope
    return [u for u in rows if wants(u)]


def _hours_notify_targets(db: OrmSession, act: Activity) -> list[User]:
    """时长审批（锁定与解锁）逐条通知谁：只发归口部门的对接人——有值班人发
    值班人，否则发部长。会长团与管理员照样能在审批页拍板，但不逐条收通知：
    时长审批量大，逐条抄送的结果是他们把通知关掉；放太久没人处理，另有
    scheduled.remind_stale_hours_requests 催对接人并抄送会长团。

    对接人找不到（没有归口部门、部门没设部长）就兜底发会长团：这时候没有
    别人会收到这条审批，不发就只能等超时催办才有人知道。
    """
    contacts = liaison.approval_contacts(db, act.approval_dept_id)
    if contacts:
        return contacts
    return council_members(db, act.approval_dept_id)


def effective_checkout(rec: CheckinRecord, g: Shift, still_open: bool,
                       fallback: datetime):
    """签退时间判定：
       · 已签退 → 实际签退时间；
       · 未签退、已经没有能签退的码了（码过了截止，或被关掉/删掉）
         → 按 fallback 兜底（auto，待核）；
       · 未签退、码还开着没过截止 → None（时长待定，pending）。

    still_open 由调用方用 checkin.checkout_open 算好传进来（一段算一次；时间
    灵活的场次一张码算一次）。fallback 一般是这一段的结束时间；时间灵活的
    场次一张码一轮，是这条记录那张码的截止时间（见 record_deadline）。
    签退没有另外的宽限：自己能签退的时段就是码的可签时段，两边必须说同一件事。
    关掉的码再开、或把截止往后改，只要人还没被补签，这里就回到 pending——
    判定是每次现算的，不落库。"""
    if rec.checkout_at:
        return rec.checkout_at, "actual"
    if not still_open:
        return fallback, "auto"
    return None, "pending"


def record_deadline(g: Shift, period, task: CheckinTask | None) -> datetime:
    """一条签到记录没签退时按哪个时刻兜底：时间灵活的场次是那张码的截止，
    别的场次是这一段的结束。"""
    if g.flexible_time and task is not None:
        return task.window(period)[1]
    return period.end_time


def shift_hours(rec: CheckinRecord, g: Shift, period, t: datetime,
                still_open: bool, fallback: datetime):
    """一条签到记录在这一段记多少小时、要不要人工过目，**以及按哪段时间算的**。

    时长认定方式是建场次时定下的一次决定，不是事后逐条判断：
      fixed  到场即按这一段时长计——两小时的彩排人就在那儿，不值得卡分钟，
             顺带消灭了「忘记签退」这类麻烦的一大半；
      none   只记出勤，不产生时长（线上培训）；
      checkin 签退 − 签到，码关了/过了截止还没签退的按 fallback（这一段结束；
             时间灵活的场次是那张码的截止）兜底并标记待核。

    返回 (小时数, 待核标记, 计时起点, 计时终点)。时段跟着一起给：备注里写的
    就是它，只回小时数的话，明细和数字会各说各的。
    """
    if g.hours_mode == "none":
        return 0.0, "", None, None
    if g.hours_mode == "fixed":
        # 到场即按这一段时长：写这一段的起止，不写他实际几点到的
        return ((period.end_time - period.start_time).total_seconds() / 3600, "",
                period.start_time, period.end_time)
    checkout, kind = effective_checkout(rec, g, still_open, fallback)
    if checkout is None:
        end = min(t, fallback)
        return ((end - rec.checkin_at).total_seconds() / 3600, "pending",
                rec.checkin_at, end)
    return ((checkout - rec.checkin_at).total_seconds() / 3600,
            "auto" if kind == "auto" else "", rec.checkin_at, checkout)


def hours_record_of(db: OrmSession, activity_id: str, user_id: str
                    ) -> HoursRecord | None:
    """一个人在一场活动里的那一笔时长。

    **一场活动一个人只有一笔**：签到算出来的和手动补录的合在一起（手动的那些
    段是 kind="manual" 的段）。分开记的话，合计是两处、通知是两条、本人还得
    分别确认两回——同一个活动的同一笔账，没有理由拆开。
    """
    return (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == activity_id,
                    HoursRecord.user_id == user_id)
            .order_by(HoursRecord.created_at).first())


def manual_parts(rec: HoursRecord | None) -> list[HoursSegment]:
    """这一笔里不是签到算出来的那几段：手动补录/扣减的，和补记录活动导入名单的。

    两种都是人交进来的账，删除只认手动那种（见 hours_manual_delete），导入的在
    「名单导入」里改。"""
    return [s for s in ((rec.live_segments() if rec else None) or [])
            if s.kind != "shift"]


def checkin_segments(db: OrmSession, act: Activity) -> dict[str, dict]:
    """这场活动按签到签退算出来的账：{用户 id: {"segments", "total", "flags"}}。

    段的字段名就是列名，管理端的预览、「确认」写进去的段、导出备注都由这一份
    数据来——分两处各算一遍，看到的数和入账的数迟早不一样。

    计时方式按建场次时定下的 `hours_mode` 走（见 shift_hours）：未签退、签到码
    还开着没过截止 → 标记 pending（暂按当前时间估算）；码已过截止或被关掉仍未
    签退 → 按场次结束时间兜底，并标记 auto_checkout **待核**：到场扫个码就走的
    人和全程服务的人算出来一样多，这类记录必须由管理者过目——「确认全部」会
    跳过它们，见 hours_confirm。
    """
    t = now()
    records = (db.query(CheckinRecord)
               .filter(CheckinRecord.activity_id == act.id)
               .order_by(CheckinRecord.checkin_at).all())
    # 本人已经给哪几段选了劳务：按（场次, 岗位）认段，重算时原样带上——
    # 不然管理者每点一次「确认全部」，本人的选择就被抹回志愿时长
    # 明细一次带出来：逐条记录各取一次，查询条数就跟着人数涨了
    # 党校同学不带：他们只记志愿时长（见 hours.pay_positions）
    party = hours_flow.party_school_users(db, act.id)
    chose_pay = {r.user_id: {(s.shift_id, s.period_id, s.position_id)
                             for s in r.live_segments() if s.is_pay()}
                 for r in db.query(HoursRecord)
                 .options(selectinload(HoursRecord.segments))
                 .filter(HoursRecord.activity_id == act.id,
                         HoursRecord.user_id.notin_(party or [""])).all()}
    out: dict[str, dict] = {}
    # 还能不能自己签退、没签退按哪个时刻兜底：一段查一次；时间灵活的场次
    # 一张码一轮，按（场次, 时段, 码）各查一次
    open_by: dict[tuple, tuple[bool, datetime]] = {}
    for rec in records:
        g = db.get(Shift, rec.shift_id)
        if g is None:
            continue
        period = g.period_of(rec.period_id)
        if period is None:
            continue
        key = (g.id, period.id, rec.task_id if g.flexible_time else "")
        if key not in open_by:
            task = db.get(CheckinTask, rec.task_id) if g.flexible_time else None
            open_by[key] = (checkout_open(db, g, t, period, task),
                            record_deadline(g, period, task))
        still_open, fallback = open_by[key]
        hours, flag, start, end = shift_hours(rec, g, period, t, still_open,
                                              fallback)
        mine = out.setdefault(rec.user_id, {"segments": [], "total": 0.0,
                                            "flags": set()})
        if flag:
            mine["flags"].add(flag)
        hours = max(0.0, hours)
        mine["total"] += hours
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
        # 岗位后来关了劳务的，这一段回到志愿时长
        reward = ("pay" if pos is not None and pos.pay_optional
                  and (g.id, period.id, pos.id) in chose_pay.get(rec.user_id, ())
                  else "")
        mine["segments"].append({
            "kind": "shift",
            # 服务日期是这一段开始的那一天：跨天的时段算开头那天
            "date": day_of(period.start_time),
            "hours": round(hours, 2), "reward": reward,
            "label": g.name if not g.is_attached() else f"{g.name}（{g.kind_label()}）",
            "position_name": pos.name if pos and not pos.is_placeholder else "",
            "time_text": span_text(start, end) if start and end else period.text(),
            "shift_id": g.id, "period_id": period.id,
            "position_id": pos.id if pos else None})
    return out


def compute_hours(db: OrmSession, act: Activity,
                  checkin: dict[str, dict] | None = None) -> list[dict]:
    """管理端那张时长总表：签到算出来的 + 手动补录/导入的，一人一行。

    手动补录的时长也算进合计、并按其原样列进明细：管理者看到的这张表就是这场
    活动的总账，漏了手动那部分，这个数就是错的。

    checkin 传现算出来的签到账（checkin_segments）：调用方还要拿它写段时传进来，
    免得同一份账算两遍——两遍之间签到记录变了，看到的和写进去的就不是一份。
    """
    if act.is_record():
        return _record_hours(db, act)
    if checkin is None:
        checkin = checkin_segments(db, act)
    mine = {r.user_id: r for r in db.query(HoursRecord)
            .options(selectinload(HoursRecord.segments))
            .filter(HoursRecord.activity_id == act.id).all()}
    extra = {uid_: manual_parts(r) for uid_, r in mine.items()}
    per_user: dict[str, float] = {uid_: info["total"]
                                  for uid_, info in checkin.items()}
    # 只有手动补录、一场都没签到过的人也要出现在表里：不然这笔账管理者看不见，
    # 也就无从核准
    for uid_ in extra:
        per_user.setdefault(uid_, 0.0)

    party = hours_flow.party_school_users(db, act.id)
    out = []
    for uid_, raw in sorted(per_user.items(), key=lambda kv: -kv[1]):
        mine_info = checkin.get(uid_, {})
        segs = mine_info.get("segments", [])
        manual = extra.get(uid_, [])
        if raw <= 0 and not manual:
            # 只去了「不计时长」的场合（如线上培训）：出勤在签到那边看。
            # 有手动段的留着——扣到 0 的人也得在表里，不然那几段加减没处看、没处删
            continue
        u = db.get(User, uid_)
        existing = mine.get(uid_)
        # 这一行现在算几小时：按结算口径（两半各自向上取整到 0.5）合起来，
        # 跟导出去、锁定时用的是同一套数
        counted, pay = HoursRecord.settle(
            [(float(s["hours"]), part_is_pay(s["kind"], s["reward"]))
             for s in segs] + [(s.hours, s.is_pay()) for s in manual])
        fl = mine_info.get("flags", set())
        out.append({
            "user": {"id": u.id, "name": u.name,
                     "student_id": u.student_id, "college": u.college},
            "hours": counted + pay,
            "pending": "pending" in fl,      # 未签退（码还开着）
            "auto_checkout": "auto" in fl,   # 码关了/过期仍未签退，按场次结束（时间灵活的场次按那张码的截止）
            "confirmed": existing is not None,
            # 手动加的那几段要能删，得让前端拿到这一笔的 id（还没确认过的人
            # 没有记录，也就没有可删的段）
            "record_id": existing.id if existing else "",
            # 签到算出来的在前、手动补录的追加在后：后者是对这笔账的补充说明
            "parts": [segment_out(s) for s in segs] + [s.out() for s in manual],
            "party_school": uid_ in party,
            **_volunteer_side(existing),
        })
    return out


def _record_hours(db: OrmSession, act: Activity) -> list[dict]:
    """补记录活动的总账：就是导进来的那些记录，数值以记录为准（申诉采纳改过
    的也在里面），没有签到可算、也没有待核的标记。"""
    rows = (db.query(HoursRecord, User)
            .options(selectinload(HoursRecord.segments))
            .join(User, HoursRecord.user_id == User.id)
            .filter(HoursRecord.activity_id == act.id)
            .order_by(HoursRecord.created_at).all())
    return [{"user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                      "college": u.college},
             "hours": rec.settled_hours(), "pending": False,
             "auto_checkout": False, "confirmed": True, "record_id": rec.id,
             "parts": [s.out() for s in rec.live_segments()],
             **_volunteer_side(rec)}
            for rec, u in rows]


def _volunteer_side(rec: HoursRecord | None) -> dict:
    """志愿者本人那一边的表态：报酬方式（按段选，这里给合计）与确认时间。"""
    if rec is None:
        return {"pay_hours": 0.0, "reward_label": "志愿时长",
                "volunteer_confirmed_at": None}
    return {"pay_hours": rec.pay_hours(), "reward_label": rec.reward_label(),
            "volunteer_confirmed_at": iso_of(rec.confirmed_at)}


# 管理者核定与申诉改判都不动签到算出来的那几段：差额单独记成一段（kind 是
# override / appeal），明细里看得出这个数是人改的，按 kind 认出上一次的调整段
# （见 HoursRecord.settle_to）。这两句是明细里显示的说明
OVERRIDE_LABEL = "管理者核定时调整"
APPEAL_LABEL = "申诉改判"
# 人判出来的调整段：不能在明细里单独删，要改就重新核定、重新判
JUDGED_KINDS = {"override": "管理者核定时的调整", "appeal": "申诉改判"}


def _segment_day(rec: HoursRecord, act: Activity) -> datetime:
    """一段调整记在哪一天：取已有段里最晚的服务日期。

    调整说的不是某一场服务，挂在最后一天上，按服务日期统计时才不会把它算到
    活动之外的某一天。一段都没有（只被手动补录过）就取活动时间那天。
    """
    days = [s.date for s in rec.live_segments() if s.date]
    if days:
        return max(days)
    span = act.time_span()
    return day_of(span[0] if span else now())


def _align_shift_segments(rec: HoursRecord, computed: list[dict]) -> None:
    """把这一笔里签到算出来的那几段对齐到现算的结果。

    同一（场次, 时段, 岗位）的就地改写：seq 不动，明细的顺序就还是写入的顺序——
    管理者加了手动段之后再点一次「确认全部」，签到段不会跑到手动段后面去。
    签到记录改了、场次没了的那几段删掉，新出现的补上。
    """
    left = list(rec.live_segments())
    for fields in computed:
        key = (fields["shift_id"], fields["period_id"], fields["position_id"])
        seg = next((s for s in left
                    if s.kind == "shift"
                    and (s.shift_id, s.period_id, s.position_id) == key),
                   None)
        if seg is None:
            rec.add_segment(**fields)
            continue
        left.remove(seg)
        seg.refill(hours=fields["hours"], date=fields["date"],
                   label=fields["label"], position_name=fields["position_name"],
                   time_text=fields["time_text"], reward=fields["reward"])
    for seg in left:
        if seg.kind == "shift":
            rec.drop_segment(seg)


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
            # 对外活动还缺哪几份盖章材料：报批按钮前先提示，别点了才被拦
            "stamped_missing": liaison.missing_stamped(db, act),
            # 志愿北京活动名称：提交时长审核时一并填，提交框里预先填上已存的
            "vb_names": list(act.vb_names or []),
            "vb_confirm": _vb_brief(liaison.latest_vb_confirm(db, act.id)),
            "request": ({"id": req.id, "kind": req.kind,
                         "kind_label": req.kind_label(), "status": req.status,
                         "status_label": req.status_label(),
                         "reason": req.reason, "reply": req.reply,
                         "created_at": iso(req.created_at)}
                        if req else None)}


class ManualHoursBody(BaseModel):
    """手动加/扣时长。items: [{student_id, hours, note}]，hours 为负就是扣减；
    date 是这一段算哪一天的服务，一次批量共用一个。

    备注必填：手动改的是账，事后只有审计和这条备注能说明为什么——「场外支援
    2 小时」和「扣掉中途离场 1 小时」在台账上得看得出来。
    """
    items: list[dict]
    date: str = Field(default="", max_length=32)


def _manual_day(raw: str) -> datetime:
    """手动那一段的服务日期：客户端说哪天就哪天，不做范围限制（未来的日期也收，
    补录明天已经定下来的服务是常事）。

    解析走 models.parse_local_dt：带时区的串先换算成本地时间，接口里的时间
    一律是不带时区的本地时间。
    """
    text = (raw or "").strip()
    if not text:
        raise HTTPException(400, "缺少服务日期")
    try:
        moment = parse_local_dt(text)
    except ValueError:
        raise HTTPException(400, "服务日期格式不对") from None
    return day_of(moment)


def _enrolled(db: OrmSession, act: Activity, u: User) -> bool:
    """这个人在这场活动里有没有已确认的报名。

    手动加/扣给的是这场活动的时长，界面上的下拉也是从各场次的正式报名里取的：
    接受别的学号，加出来的就是一笔没有服务依据的账。
    """
    return (db.query(Enrollment.id)
            .filter(Enrollment.activity_id == act.id,
                    Enrollment.user_id == u.id,
                    Enrollment.status == "enrolled").first()) is not None


@router.post("/activities/{activity_id}/hours/manual")
def hours_manual(activity_id: str, body: ManualHoursBody,
                 db: OrmSession = Depends(db_session),
                 user: User = Depends(require_manager)):
    """管理者为志愿者手动添加或扣减时长（不依赖签到），可批量。

    并进这个人在这场活动里的那一笔（见 hours_record_of），不另起一条：合计要
    是一个数，通知才只发一条，本人也只需确认一次。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.is_record():
        raise HTTPException(409, "补记录活动的时长请在「名单导入」里改")
    ensure_hours_editable(db, act)
    if not body.items:
        raise HTTPException(400, "空列表")
    day = _manual_day(body.date)
    # 这一批要往这一笔里加段：先把这场活动的记录行按 id 顺序都锁住，再读改写。
    # 锁在读之前，中间插进来的「确认全部」「本人选报酬方式」就不会和这次写入
    # 交错成谁都没写过的组合
    hours_flow.lock_records(db, act.id)
    added = []
    changes = []
    revoked = False
    for it in body.items:
        u = db.query(User).filter(
            User.student_id == (it.get("student_id") or "").strip()).first()
        if u is None:
            raise HTTPException(404, f"学号 {it.get('student_id')} 不存在")
        if u.user_type == "teacher":
            # 与名单导入一个口径：老师不参与志愿，也不记时长
            raise HTTPException(400, "老师类型用户不参与志愿，不记时长")
        if not _enrolled(db, act, u):
            raise HTTPException(
                400, f"{u.name}（{u.student_id}）没有报名这场活动，不能直接加时长；"
                     f"这类时长请在补记录活动里记")
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
                        if not any(s.kind == "shift"
                                   for s in rec.live_segments()) else "")
                raise HTTPException(
                    400, f"{u.name} 当前只有 {rec.hours:g} 小时，"
                         f"扣不了 {-hours:g} 小时{hint}")
        before = rec.settled_hours() if rec is not None else None
        if rec is None:
            rec = HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=u.id,
                              source="manual", note=note, confirmed_by=user.id)
            db.add(rec)
        else:
            rec.confirmed_by = user.id
        # 数值变了：本人对着旧数的确认与劳务选择作废，核准过的撤销核准。先清
        # 劳务再加段——扣减记在志愿时长上，劳务留着就可能把志愿那一半扣成负数
        revoked = hours_flow.reopen_record(db, act, rec) or revoked
        # 段的 id 是这一段的编号，删除时按它找（见 hours_manual_delete）
        rec.add_segment(kind="manual", date=day, hours=hours, label=note,
                        time_text=f"{day:%m-%d}")
        # 加完就 flush：同一批里这个人再出现一次时，hours_record_of 查不到刚建的
        # 这条、扣减也看不到刚加上去的那一段（合计是 flush 时按段重算的）
        db.flush()
        verb = f"{'手动扣减' if hours < 0 else '手动添加'} {abs(hours):g}h"
        added.append(f"{u.name} {verb}")
        changes.append(hours_flow.hours_change(u, before, rec.settled_hours(),
                                               f"{verb}：{note}"))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"手动调整时长：{'、'.join(added)}"
                      + ("；原时长核准已撤销" if revoked else ""),
              changes=changes)
    db.commit()
    return {"ok": True, "count": len(added), "reapproval_required": revoked}


@router.delete("/activities/{activity_id}/hours/manual/{record_id}/{part_id}")
def hours_manual_delete(activity_id: str, record_id: str, part_id: str,
                        db: OrmSession = Depends(db_session),
                        user: User = Depends(require_manager)):
    """删掉手动加的那**一段**（不是整笔账），按补录时发的那一段的编号找。

    不能按「它在明细里排第几」找：管理端看到的明细是现算出来的（签到段与手动段
    拼在一起），按位置取会删掉另一段，合计和审计也跟着错。

    只认 kind="manual" 的段：签到算出来的那些段是账的依据，删不得——要改就改
    签到，或者用「手动加/扣」。
    """
    ensure_activity_manager(db, user, activity_id)
    # 删一段要连着改合计、可能还要撤销核准：先锁活动行，再锁这一笔（顺序同
    # lock_records），再读改写
    act = _act(db, activity_id, for_update=True)
    rec = db.get(HoursRecord, record_id, with_for_update=True)
    if rec is None or rec.activity_id != act.id:
        raise HTTPException(404, "时长记录不存在")
    ensure_hours_editable(db, act)
    segs = rec.live_segments()
    seg = next((s for s in segs if s.id == part_id), None)
    if seg is not None and seg.kind in JUDGED_KINDS:
        raise HTTPException(409, f"{JUDGED_KINDS[seg.kind]}不能删除；"
                                 f"要改请重新核定或重新处理申诉")
    if seg is None or seg.kind != "manual":
        raise HTTPException(404, "这一段不是手动添加的时长")
    rest = [s for s in segs if s is not seg]
    before = rec.settled_hours()
    hours = round(float(seg.hours or 0), 2)
    left = round(sum(s.hours for s in rest), 2)
    if left < 0:
        # 不能截到 0 了事：截掉的那一截会在删扣减段时被「加回来」，账凭空变多
        # （加 2、扣 5 → 合计 0，删掉加的那段 → 截成 0，再删扣的那段 → 多出 3 小时）
        raise HTTPException(
            409, f"删掉这段后合计会变成 {left:g} 小时：它已经被后面的扣减用掉了，"
                 f"请先删掉扣减的那段")
    rec.drop_segment(seg)
    u = db.get(User, rec.user_id)
    # 这笔账本来就是手动加出来的，删空了就该消失，别留一条 0 小时的记录在表里
    if not rest:
        db.delete(rec)
    # 数值变了：本人得重新表个态，核准过的撤销核准
    revoked = hours_flow.reopen_record(db, act, rec if rest else None)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=f"删除手动时长：{u.name if u else '?'} {hours:g}h"
                      f"（{seg.label}）" + ("；原时长核准已撤销" if revoked else ""),
              changes=[hours_flow.hours_change(
                  u, before, rec.settled_hours() if rest else None,
                  f"删除手动段 {hours:g}h：{seg.label}")])
    db.commit()
    return {"ok": True, "reapproval_required": revoked}


class HoursConfirmBody(BaseModel):
    items: list[dict] | None = None   # [{student_id, hours}] 指定部分志愿者并覆盖时长


@router.post("/activities/{activity_id}/hours/confirm")
def hours_confirm(activity_id: str, body: HoursConfirmBody | None = None,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """确认时长（依据签到记录），幂等可重复执行。

    · 不带 items → 全部志愿者按签到算出来的数确认；
    · 带 items   → 只确认所选志愿者，时长以请求值为准（覆盖签到算出来的数）。

    确认只是把账算出来放在这儿，志愿者还看不到——要「核准」了才通知本人。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    if act.is_record():
        # 没有签到可依据：导进来的就是确认过的账，改数在「名单导入」里改
        raise HTTPException(409, "补记录活动的时长以导入的名单为准，无需确认；"
                                 "如需修改请到「名单导入」里改")
    ensure_hours_editable(db, act)

    # 重算要读现有的段与本人选的报酬方式，写回要按场次对齐签到段：先按 id 顺序
    # 把这场活动的记录行都锁住，让「读 → 重算 → 写回」整段排他。锁在读之前，
    # 不然这中间插进来的手动加减、本人确认会和这次重算交错
    hours_flow.lock_records(db, act.id)

    kept: list[str] = []
    changes: list[dict] = []
    revoked = False
    # 这一份签到账既用来出表格，也用来写段：算两遍的话，两遍之间签到变了，
    # 看到的和写进去的就不是一份
    checkin = checkin_segments(db, act)
    items = compute_hours(db, act, checkin)
    computed_segments = {user_id: info["segments"] for user_id, info in checkin.items()}

    def upsert(user_id: str, hours: float | None, *, force: bool = False,
               reason: str) -> None:
        """这一笔的签到段对齐到现算的结果；管理者点名给了数就按那个数定合计。

        手动补录的那几段（kind="manual"）不动：它们不是签到算出来的，重算时
        原样留着。本人给各段选的报酬方式在 checkin_segments 里按（场次, 岗位）
        带上了，对齐时一起写回去，所以「确认全部」不会把本人的选择抹掉。
        结算合计因此变了（含新记一笔）的，本人重新表态，核准过的撤销核准。
        """
        nonlocal revoked
        existing = hours_record_of(db, act.id, user_id)
        if existing is not None:
            # 采纳过申诉的记录不被「确认全部」推回计算值：那次改判是人判的
            settled = db.query(HoursAppeal).filter(
                HoursAppeal.record_id == existing.id,
                HoursAppeal.status == "accepted").first()
            if settled is not None and not force:
                u = db.get(User, user_id)
                kept.append(u.name if u else user_id)
                return
            rec = existing
        else:
            rec = HoursRecord(id=uid("hrs"), activity_id=act.id, user_id=user_id,
                              source="checkin", confirmed_by=user.id)
            db.add(rec)
        before = existing.settled_hours() if existing is not None else None
        _align_shift_segments(rec, computed_segments.get(user_id, []))
        if hours is not None and rec.settled_hours() != HoursRecord.ceil_half(hours):
            # 管理者给的数就是这一笔的结算合计：差额记成一段核定调整（见
            # HoursRecord.settle_to），改回签到算出的数时那段调整随之去掉。
            # 跟表上一样就什么也不动——「勾选几个人确认」发回来的就是表上那个数。
            # 先清劳务：差额落在志愿时长上，取整后的合计才恰好是给的数
            rec.clear_pay()
            rec.settle_to(hours, kind="override", label=OVERRIDE_LABEL,
                          date=_segment_day(rec, act))
        after = rec.settled_hours()
        if after != before:
            revoked = hours_flow.reopen_record(db, act, rec) or revoked
        if after != before or force:
            # 逐个点名确认的人都记，数没变的也记：点名本身就是一次人判；
            # 「确认全部」只记数变了的人，名单上几百个原样不动的人不必各占一行
            changes.append(hours_flow.hours_change(
                db.get(User, user_id), before, after, reason))

    chosen = (body.items if body else None) or []
    if chosen:
        # 同一个学号在一批里出现两次时只认最后一条：一笔账不能在一个请求里
        # 分成两笔写（库上「一人一场一笔」是唯一约束）
        targets: dict[str, float] = {}
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
            targets[u.id] = round(hours, 2)
        for uid_, h in targets.items():
            # 逐个点名改，就是人判的，照改
            upsert(uid_, h, force=True, reason="部分确认（以管理端填写为准）")
        count = len(targets)
        summary = f"部分确认 {count} 人志愿时长（以管理端填写为准）"
    else:
        if not items:
            raise HTTPException(400, "暂无签到数据，无法确认时长")
        # 未签退（码还开着）与码关了/过期后自动兜底的记录不在此列：时长存疑，
        # 需要管理者在表格里逐条核对数值后单独确认
        clear = [it for it in items
                 if not it["pending"] and not it["auto_checkout"]]
        review = [it for it in items if it not in clear]
        if not clear:
            raise HTTPException(
                400, f"{len(review)} 条记录未正常签退，时长待核，"
                     f"请在表格中逐条核对后确认")
        for it in clear:
            upsert(it["user"]["id"], None, reason="按签到签退记录确认")
        count = len(clear) - len(kept)
        summary = (f"确认 {count} 人志愿时长（依据签到签退记录）"
                   + (f"；{len(review)} 人未正常签退、时长待核未确认"
                      if review else "")
                   + (f"；{'、'.join(kept)} 的时长由申诉改判过，保留不覆盖"
                      if kept else "")
                   + (f"；{len(changes)} 人时长有变动" if changes else ""))
    if revoked:
        summary += "；时长有变动，原核准已撤销，请重新核准"
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary, changes=changes)
    db.commit()
    return {"ok": True, "count": count, "message": summary,
            "reapproval_required": revoked}


# ======================= 时长申诉 =======================

# ======================= 活动概况 =======================

@router.get("/activities/{activity_id}/overview")
def activity_overview(activity_id: str, db: OrmSession = Depends(db_session),
                      user: User = Depends(require_manager)):
    """活动管理页顶上那张概况卡：本活动的报名、签到、时长、违规一眼看完。

    口径：
    · 只算服务场次。培训/彩排不招人、不设名额（跟着服务场次的报名连带进来），
      算进来同一个人会被数两三遍。
    · 报名按人次（一条正式报名记录算一次，一人报两场算两次），另给去重人数；
      名额同 Shift.total_capacity（截止后分配的场次按占位岗位的整场名额）；
      候补 = 排队中 + 转正待确认，同 waitlist_rows。
    · 签到率 = 已签到的正式报名 / 已开始场次的正式报名（人次）。还没开始的
      场次不进分母，否则活动进行到一半，签到率看着永远偏低。
    · 未签退：「扫码计时」场次里签了到还没签退的人次。固定计时/不计时长的
      场次本来就不用签退，算进来全是假警报。
    · 时长：已入账（HoursRecord）里计入志愿时长的合计（选了劳务的段不算，
      见 HoursRecord.split）；党校同学的照算
      （同数据总览，那是平台自己的数），另单独给出党校部分。approved 说明
      这笔账是否已经核准。
    · 锁定后退出：本活动服务场次里有过锁定后退出（cancel_type=locked）的人数。
    补记录活动没有场次与报名，只给名单人数、时长和违规。

    全部是按活动聚合的几条查询，条数不随人数、岗位数涨。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id)
    t = now()

    party_ids = hours_flow.party_school_users(db, act.id)
    # 志愿时长那一半：按笔把两半各自求和交给 SQL（段上的 record_id 有索引），
    # 结算只走一处（HoursRecord.settle_sums），不在这儿另写一个取整——两处各写
    # 一个，界面上看到的和这张卡上的迟早差 0.5
    is_pay = and_(HoursSegment.kind == "shift", HoursSegment.reward == "pay")
    ledger = (db.query(HoursRecord.user_id,
                       func.coalesce(func.sum(HoursSegment.hours).filter(~is_pay),
                                     0.0),
                       func.coalesce(func.sum(HoursSegment.hours).filter(is_pay),
                                     0.0))
              .outerjoin(HoursSegment, HoursSegment.record_id == HoursRecord.id)
              .filter(HoursRecord.activity_id == act.id)
              .group_by(HoursRecord.id, HoursRecord.user_id).all())
    roster = len(ledger)
    counted = [(u, HoursRecord.settle_sums(volunteer, paid)[0])
               for u, volunteer, paid in ledger]
    total = sum(c for _u, c in counted)
    party_total = sum(c for u, c in counted if u in party_ids)
    violations = (db.query(func.count(ViolationRecord.id))
                  .filter(ViolationRecord.activity_id == act.id).scalar())
    out = {"record": act.is_record(),
           "hours": {"total": round(total, 2), "party_school": round(party_total, 2),
                     "records": roster,
                     "approved": act.hours_approved_at is not None,
                     "locked": act.hours_locked_at is not None},
           "violations": violations}
    if act.is_record():
        out["roster"] = roster
        return out

    shifts = (db.query(Shift).options(selectinload(Shift.positions))
              .filter(Shift.activity_id == act.id, Shift.kind == "service")
              .order_by(Shift.sort, Shift.start_time).all())
    occ = Occupancy(db, act.id)
    service = and_(Enrollment.shift_id == Shift.id, Shift.kind == "service")
    enrolled = Enrollment.status == "enrolled"
    people, applied, locked_quit = (
        db.query(func.count(func.distinct(Enrollment.user_id)).filter(enrolled),
                 func.count(Enrollment.id).filter(Enrollment.status == "applied"),
                 func.count(func.distinct(Enrollment.user_id))
                 .filter(Enrollment.cancel_type == "locked"))
        .join(Shift, service).filter(Enrollment.activity_id == act.id).one())
    # 应到：已开始的服务场次里的正式报名；签到记录按（人, 场次）对上
    signed = and_(CheckinRecord.shift_id == Enrollment.shift_id,
                  CheckinRecord.user_id == Enrollment.user_id)
    due, arrived = (
        db.query(func.count(func.distinct(Enrollment.id)),
                 func.count(func.distinct(Enrollment.id))
                 .filter(CheckinRecord.id.isnot(None)))
        .join(Shift, service).outerjoin(CheckinRecord, signed)
        .filter(Enrollment.activity_id == act.id, enrolled,
                Shift.start_time <= t).one())
    open_out = (db.query(func.count(CheckinRecord.id))
                .join(Shift, CheckinRecord.shift_id == Shift.id)
                .filter(CheckinRecord.activity_id == act.id,
                        Shift.kind == "service", Shift.hours_mode == "checkin",
                        CheckinRecord.checkout_at.is_(None)).scalar())

    rows = []
    for g in shifts:
        cap = g.total_capacity()
        holder = g.placeholder()
        positions = []
        for p in g.real_positions():
            n = occ.position_occupied(p.id)
            positions.append({"id": p.id, "name": p.name, "group": p.group_name,
                              "enrolled": occ.position_enrolled(p.id),
                              "occupied": n, "quota": p.quota,
                              "waitlist": occ.position_wait(p.id),
                              "paused": p.enroll_paused,
                              "locked": p.list_locked,
                              "full": n >= p.quota})
        rows.append({"id": g.id, "name": g.name, "time": g.span_text(),
                     "periods": [p.out() for p in g.sorted_periods()],
                     "mode": g.mode, "started": g.start_time <= t,
                     "enrolled": occ.shift_enrolled(g.id),
                     "occupied": occ.shift_occupied(g.id),
                     "capacity": cap, "waitlist": occ.shift_wait(g.id),
                     "paused": g.enroll_paused,
                     "locked": shift_locked(g),
                     "full": cap > 0 and occ.shift_occupied(g.id) >= cap,
                     # 截止后分配的场次：还在占位岗位里、没分到岗位的人
                     "unassigned": occ.position_enrolled(holder.id) if holder else 0,
                     "positions": positions})
    out.update({
        "enroll": {"count": sum(r["enrolled"] for r in rows), "people": people,
                   "capacity": sum(r["capacity"] for r in rows),
                   "occupied": sum(r["occupied"] for r in rows),
                   "waitlist": sum(r["waitlist"] for r in rows),
                   "applied": applied},
        "checkin": {"due": due, "arrived": arrived,
                    "rate": round(arrived / due, 4) if due else None,
                    "not_checked_out": open_out},
        "locked_quit": locked_quit,
        "shifts": rows})
    return out


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
                    "current_hours": rec.settled_hours() if rec else None,
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
        # 采纳要改这一笔的段：先把这一行锁住再读改写，免得和本人确认、名单导入
        # 互相盖掉刚写的段
        db.refresh(rec, with_for_update=True)
        hours = a.claimed_hours if body.hours is None else round(body.hours, 2)
        if hours <= 0:
            raise HTTPException(400, "时长必须大于 0")
        before = rec.settled_hours()
        rec.confirmed_by = user.id
        # 改判后本人对着新数重新确认、重新选报酬方式（核准不撤销：申诉本来就
        # 发生在核准之后，改判是这段时间里该做的事）。先清劳务：差额落在志愿
        # 时长上，取整后的合计才恰好是改判的数，也不会把志愿那一半扣成负数
        rec.confirmed_at = None
        rec.clear_pay()
        if act.is_record():
            # 补记录的一笔只有导入那一段：段上的数跟着改，名单里看到的才是新值
            seg = next((s for s in rec.live_segments() if s.kind == "import"), None)
            if seg is None:
                raise HTTPException(409, "这条记录没有导入明细，"
                                         "请到「名单导入」里重新导入")
            seg.refill(hours=hours)
        else:
            # 改判说的是「这一笔就是这么多」：差额记成一段申诉改判，签到段上的
            # 原始值不动——动了它，明细就解释不了这个数是从哪来的
            rec.settle_to(hours, kind="appeal", label=APPEAL_LABEL,
                          date=_segment_day(rec, act))
        a.final_hours = hours
        a.status = "accepted"
        summary = (f"采纳 {who.name if who else ''} 的时长申诉："
                   f"{before:g}h → {hours:g}h（申报 {a.claimed_hours:g}h）")
        changes = [hours_flow.hours_change(
            who, before, rec.settled_hours(),
            f"采纳时长申诉（申报 {a.claimed_hours:g}h）")]
    else:
        changes = []
        a.status = "rejected"
        summary = (f"驳回 {who.name if who else ''} 的时长申诉"
                   f"（申报 {a.claimed_hours:g}h，维持 {rec.settled_hours():g}h）")
    a.reply = body.reply.strip()
    a.handled_by = user.id
    a.handled_at = now()

    if who is not None:
        # 采纳后数变了：请本人重新确认；这一笔里有可选劳务的岗位才提报酬方式
        again = ""
        if body.accept:
            again = "\n请重新确认时长" + (
                "并重新选择报酬方式（志愿时长或劳务）"
                if hours_flow.pay_positions(db, rec) else "")
            again += (f"，{act.appeal_deadline:%Y-%m-%d %H:%M} 前未确认默认记为志愿时长。"
                      if act.appeal_deadline else "。")
        msgcenter.send_user(
            db, who, "custom", data={"route": "hours"},
            title=f"时长申诉{'已采纳' if body.accept else '未采纳'}｜{act.title}",
            content=(f"您对「{act.title}」时长的申诉已处理。\n"
                     f"结果：{'采纳' if body.accept else '未采纳'}，"
                     f"当前时长 {rec.settled_hours():g} 小时。"
                     + again
                     + (f"\n说明：{a.reply}" if a.reply else "")))
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=summary + (f"；说明：{a.reply}" if a.reply else ""),
              changes=changes)
    db.commit()
    return {"ok": True, "status": a.status, "hours": rec.settled_hours()}


@router.post("/activities/{activity_id}/hours/approve")
def hours_approve(activity_id: str, body: ApproveHoursBody,
                  db: OrmSession = Depends(db_session),
                  user: User = Depends(require_manager)):
    """确认核准时长：把账定下来，逐个通知本人，并给一段确认/申诉时间。

    截止之前志愿者要么确认无误（顺带选志愿时长还是劳务），要么提申诉；
    到点没表态的按「没问题 + 志愿时长」处理，定时任务自动提交校青协审批，
    通过即锁定，活动落「待外联确认」。
    有人申诉就通知管理者，改完再核准一次：这一次只惊动数值
    变过、或者申诉过的人，别人的确认照旧有效。

    「通知过」看每条记录的 approved_hours（通知时记下的数值）：补记录活动
    改名单会撤销活动级核准，但留着没改到的人的 approved_hours（见
    hours.revoke_approval），重新核准时同样只通知变了的人。
    """
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, for_update=True)
    ensure_hours_editable(db, act)
    deadline = _parse_deadline(body.appeal_deadline)
    rows = (db.query(HoursRecord)
            .filter(HoursRecord.activity_id == act.id).all())
    if not rows:
        # 一条时长都没有的活动没什么可申诉的，直接结账；志愿北京那边也没有
        # 可录的，不必推外联确认，活动直接完成
        hours_flow.lock_hours(db, act, user, "没有任何时长记录，核准时直接锁定",
                              to_liaison=False)
        db.commit()
        return {"ok": True, "locked": True, "count": 0,
                "message": "本活动没有时长记录，已直接锁定，活动已完成"}

    since = act.hours_approved_at
    appealed = set()
    if since is not None:
        appealed = {a.user_id for a in db.query(HoursAppeal)
                    .filter(HoursAppeal.activity_id == act.id,
                            HoursAppeal.created_at >= since).all()}
    act.hours_approved_at = now()
    act.appeal_deadline = deadline
    notified = 0
    again = since is not None or any(r.approved_hours is not None for r in rows)
    for r in rows:
        fresh = (r.approved_hours is None
                 or r.approved_hours != r.settled_hours())
        if not fresh and r.user_id not in appealed:
            continue          # 通知过这个数值、没变、也没申诉：不打扰
        r.approved_hours = r.settled_hours()
        r.confirmed_at = None
        u = db.get(User, r.user_id)
        if u is None:
            continue
        notified += 1
        # 有哪一段的岗位可选劳务才需要选报酬方式，别人不必看到这一句
        pay_line = ("\n请选择报酬方式志愿时长或劳务，超时未确认默认选择志愿时长。"
                    if hours_flow.pay_positions(db, r) else "")
        # 点开去「我的志愿时长」而不是活动详情：要确认/要申诉的那笔账在那儿，
        # 活动详情页上看不到本人在这个活动里有多少时长
        msgcenter.send_user(
            db, u, "hours_approved", data={"route": "hours"},
            activity=act.title, hours=f"{r.settled_hours():g}",
            deadline=f"{deadline:%Y-%m-%d %H:%M}", pay_line=pay_line)
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"{'再次' if again else ''}核准志愿时长，通知 {notified} 人，"
                       f"确认/申诉截止 {deadline:%Y-%m-%d %H:%M}"))
    db.commit()
    return {"ok": True, "locked": False, "count": notified,
            "message": f"已核准并通知 {notified} 人，"
                       f"{deadline:%m-%d %H:%M} 前无人申诉即自动锁定"}


class LockHoursBody(BaseModel):
    note: str = ""
    # 志愿北京上的活动名称，至少一个；不传就用之前存过的（审核页签里可以先填）
    vb_names: list[str] | None = None


def save_vb_names(db: OrmSession, act: Activity, user: User, raw) -> list[str]:
    """校验并存下志愿北京活动名称，有变化才记审计。不提交事务。"""
    try:
        names = liaison.clean_vb_names(raw)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    if names != list(act.vb_names or []):
        before = liaison.names_text(act.vb_names) or "（未填）"
        act.vb_names = names
        audit_log(db, actor=user, action="vb_confirm", target_type="activity",
                  target_id=act.id, target_label=act.title, activity_id=act.id,
                  summary=f"志愿北京活动名称：{before} → {liaison.names_text(names)}")
    return names


def vb_names_block(db: OrmSession, act: Activity) -> str:
    """志愿北京名称现在能不能改（提交时长审核之前随时改）；空串 = 能改。
    锁定之后被外联退回，走「重提」，不走这里。"""
    if act.hours_locked():
        return "时长已锁定，志愿北京活动名称只能在外联退回后重新提交"
    if hours_flow.pending_lock(db, act.id) is not None:
        return "时长正在等待审核，审核期间不能修改志愿北京活动名称"
    return ""


@router.post("/activities/{activity_id}/hours/lock-request")
def hours_lock_request(activity_id: str, body: LockHoursBody | None = None,
                       db: OrmSession = Depends(db_session),
                       user: User = Depends(require_manager)):
    """提前提交锁定审批。不等申诉期满、有申诉没处理，都由管理者判断。"""
    ensure_activity_manager(db, user, activity_id)
    act = _act(db, activity_id, for_update=True)
    ensure_hours_editable(db, act)
    if act.hours_approved_at is None:
        raise HTTPException(409, "请先核准时长")
    # 对外活动的时长要凭盖章立项书和盖章结项书报批，缺一份都不往上送
    missing = liaison.missing_stamped(db, act)
    if missing:
        raise HTTPException(400, f"对外活动须先上传{'和'.join(missing)}，才能提交时长审核")
    # 志愿北京活动名称随时长一起报：审的人看得到，锁定后推给对外联络部
    save_vb_names(db, act, user,
                   body.vb_names if body and body.vb_names is not None
                   else act.vb_names)
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
               f"说明：{reason}\n志愿北京活动名称：{liaison.names_text(act.vb_names)}\n"
               f"通过后时长锁定，名称推给对外联络部确认；"
               f"驳回则退回管理者修改。\n请到「活动审批」处理。",
               data={"activity_id": act.id, "route": "approvals"})


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
               data={"activity_id": act.id, "route": "approvals"})
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

    锁定通过 = 这场活动的时长经校青协审核，就此封账、活动落「待外联确认」，
    志愿北京活动名称推给对外联络部（外联确认后才算已完成）；驳回则解冻，
    管理者改完重新核准，再提交一次。
    解锁通过 = 回到「已结束」：不管此前停在待外联确认还是已完成，账重新
    打开就回到结账之前。志愿北京确认是时长之后的附加步骤，解锁不动它：已经
    确认的继续有效，重新锁定、名称没变的直接回到「已完成」。
    通过可以附意见（选填），驳回必须写理由；意见随通知发给申请人。
    """
    req = db.get(HoursRequest, request_id)
    if req is None:
        raise HTTPException(404, "申请不存在")
    if req.status != "pending":
        raise HTTPException(409, f"该申请{req.status_label()}")
    act = _act(db, req.activity_id)
    if user.id not in {u.id for u in _hours_approvers(db, act)}:
        raise HTTPException(403, f"只有归口部门的部长可以处理时长{req.kind_label()}申请")
    reply = body.reply.strip()[:512]
    if not body.approve and len(reply) < 2:
        raise HTTPException(400, "驳回必须写明理由")
    req.status = "approved" if body.approve else "rejected"
    req.reply, req.handled_by, req.handled_at = reply, user.id, now()
    if body.approve and req.kind == "lock":
        hours_flow.lock_hours(db, act, user, f"校青协审核通过（{req.reason}）")
    elif body.approve:
        act.hours_locked_at = None
        act.status = "finished"
    audit_log(db, actor=user, action="hours_confirm", target_type="hours",
              target_id=act.id, target_label=act.title, activity_id=act.id,
              summary=(f"同意{req.kind_label()}时长（{req.reason}）"
                       + (f"；意见：{reply}" if reply else "") if body.approve
                       else f"驳回时长{req.kind_label()}申请：{reply}"))
    requester = db.get(User, req.requested_by)
    if requester is not None:
        notify(db, requester,
               f"时长{req.kind_label()}{'已通过' if body.approve else '未通过'}"
               f"｜{act.title}",
               _decide_text(db, act, req, body.approve, reply),
               data={"activity_id": act.id, "route": "manage", "tab": "hours"})
    db.commit()
    return {"ok": True, "status": req.status}


def _decide_text(db: OrmSession, act: Activity, req, approve: bool,
                 reply: str) -> str:
    if not approve:
        return (f"时长{req.kind_label()}申请未通过。意见：{reply}\n"
                + ("请修改后重新核准，再提交一次审批。" if req.kind == "lock"
                   else "如仍需修改，请补充说明后重新申请。"))
    tail = f"\n审批意见：{reply}" if reply else ""
    if req.kind == "lock":
        if act.status == "completed":
            return f"「{act.title}」的志愿时长已通过校青协审核并锁定，活动已完成。{tail}"
        names = liaison.names_text(act.vb_names)
        last = liaison.latest_vb_confirm(db, act.id)
        if last is not None and last.status == "returned":
            nxt = (f"志愿北京活动名称{names}此前被对外联络部退回，请到「审核」页签"
                   f"修改后重新提交，外联确认后活动即标记为已完成。")
        else:
            nxt = (f"志愿北京活动名称{names}已推给对外联络部，"
                   f"外联在志愿北京核对确认后活动即标记为已完成。")
        return f"「{act.title}」的志愿时长已通过校青协审核并锁定。\n{nxt}{tail}"
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
    heads = liaison.dept_heads(db, dept.id) if dept else []
    head_names = "、".join(h.name for h in heads)
    duty = liaison.on_duty(db, dept)
    return {
        "department": dept.name if dept else "",
        # 等谁处理：启用值班的部门是值班人，否则是部长（会长团、管理员也能批）
        "handler": (f"{duty.name}（值班）" if duty
                    else (f"{head_names}（部长）" if head_names else "")),
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
                    # 归口外联的才有结项材料，「结项通过 / 不通过」常用语只给它们
                    "external": liaison.is_external(db, act),
                    "vb_names": (list(act.vb_names or []) if req.kind == "lock"
                                 else []),
                    # 盖章材料就在这张卡片上给：审批人多半不是这场活动的管理者，
                    # 进不了活动管理页，别处看不到要审的东西
                    "files": [{"label": liaison.SLOTS[s], "id": f.id, "title": f.title}
                              for s in liaison.STAMPED_SLOTS
                              if (f := liaison.project_file(db, act.id, s))],
                    "requested_by": who.name if who else "系统",
                    "created_at": iso(req.created_at)})
    return out
