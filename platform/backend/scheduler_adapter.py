"""排班适配器：平台数据 ⇄ schedule 求解器。

《设计方案》阶段三「二次分配（兜底排班）」的实现：
  · 把待分配一级岗位下的二级工位映射为排班程序的 Position；
  · 该组已确认报名的学生映射为 Volunteer（标签：性别/学院/年级）；
  · 学生 × 全部工位生成 SignupItem——模式B报名时未选工位，视为"任何工位均可"，
    由求解器按规则配置（先到先得/摊平/保底等）统一分配；
  · 工位间时间重叠 → 写入冲突图，保证同一人不会被分进两个重叠工位；
  · 求解结果回填 Enrollment.station_id；与该生**其他活动**时段冲突的行
    红色高亮警告，允许管理员强制分配（留痕）。

求解器本体零依赖（schedule/app/core.py），直接以原算法复用：
深度2主段超时自动降级深度1——与原工具行为一致。
"""
from __future__ import annotations

import sys

from sqlalchemy.orm import Session as OrmSession

from . import config, enroll_logic
from .models import Activity, Enrollment, PositionGroup, Station, User, iso, now

# —— 挂载 schedule 排班程序 ——
if str(config.SCHEDULE_DIR) not in sys.path:
    sys.path.insert(0, str(config.SCHEDULE_DIR))
from app import core as schedule_core          # noqa: E402
from app import rules as schedule_rules        # noqa: E402

SOLVE_TIMEOUT_MAIN = 20     # 秒。校内规模远用不满
SOLVE_TIMEOUT_FALLBACK = 20
RESCUE_TIMEOUT = 8

PRESETS_FOR_UI = [
    {"key": key, "label": spec["label"], "hint": spec["hint"]}
    for key, spec in schedule_rules.PRESETS.items()
]


def _station_requirements(station: Station) -> dict:
    """结构化 requirement_tags → 排班程序条件树。

    {"性别": "女"}            → {"all": [{"tag":"性别","op":"eq","value":"女"}]}
    {"学院": ["新闻学院",…]}   → {"all": [{"tag":"学院","op":"in","value":[…]}]}
    """
    tree = {"all": []}
    for tag, value in (station.requirement_tags or {}).items():
        if isinstance(value, (list, tuple, set)):
            vals = [str(v) for v in value if str(v).strip()]
            if vals:
                tree["all"].append({"tag": str(tag), "op": "in", "value": vals})
        elif str(value).strip():
            tree["all"].append({"tag": str(tag), "op": "eq", "value": str(value)})
    return tree


def build_project(group: PositionGroup, db: OrmSession,
                  rules_preset: str = "",
                  rules_cfg: dict | None = None) -> tuple[schedule_core.Project,
                                                          dict[str, Enrollment],
                                                          dict[str, User]]:
    """把一个一级岗位（含其二级工位与已确认名单）装配成排班项目。"""
    prj = schedule_core.Project()
    if rules_cfg:
        # 高级自定义：与排班程序同一套 normalize 校验，非法配置当场报错
        prj.rules = schedule_rules.normalize(rules_cfg)
    elif rules_preset and rules_preset in schedule_rules.PRESETS:
        prj.rules = schedule_rules.from_preset(rules_preset)

    stations = sorted(group.stations, key=lambda s: s.sort)
    enrollments = (db.query(Enrollment)
                   .filter(Enrollment.group_id == group.id,
                           Enrollment.status == "confirmed")
                   .order_by(Enrollment.joined_at).all())

    users: dict[str, User] = {}
    enr_of_key: dict[str, Enrollment] = {}
    for enr in enrollments:
        u = db.get(User, enr.user_id)
        key = f"u:{u.id}"
        users[key] = u
        enr_of_key[key] = enr
        prj.merge_volunteer(schedule_core.Volunteer(
            key=key, name=u.name, contact=u.phone or "",
            tags={"性别": u.gender or "", "学院": u.college or "",
                  "年级": str(u.grade or "")}))
        # 模式A已选工位的保留其意愿：只报自己那格；模式B（待分配）报全部工位
        pids = [enr.station_id] if enr.station_id else [s.id for s in stations]
        for pid in pids:
            prj.add_signup(key, pid, time_raw=f"{enr.joined_at:%Y-%m-%d %H:%M}")

    for st in stations:
        prj.positions[st.id] = schedule_core.Position(
            pid=st.id, name=st.name,
            # 工位无独立时间：时段随场次
            time_note=(f"场次 {group.span_text()}"
                       + (f"（{st.location}）" if st.location else "")),
            quota=max(0, st.quota),
            requirements=_station_requirements(st),
            req_note=st.requirement_note or "")
    # 二级工位之间默认互斥：同一人在本场次只占一个工位（完全冲突图）
    for i, a in enumerate(stations):
        for b in stations[i + 1:]:
            prj.set_conflict(a.id, b.id, True)
    return prj, enr_of_key, users


def _run(prj: schedule_core.Project) -> tuple[list, bool]:
    report: dict = {}
    try:
        result = schedule_core.solve(prj, max_depth=2,
                                     timeout=SOLVE_TIMEOUT_MAIN,
                                     rescue_timeout=RESCUE_TIMEOUT,
                                     report=report)
        return result, False
    except schedule_core.SolveTimeout:
        result = schedule_core.solve(prj, max_depth=1,
                                     timeout=SOLVE_TIMEOUT_FALLBACK,
                                     rescue_timeout=RESCUE_TIMEOUT,
                                     report=report)
        return result, True


def preview(db: OrmSession, group: PositionGroup,
            rules_preset: str = "", rules_cfg: dict | None = None) -> dict:
    """试排一次：给出建议方案 + 冲突高亮 + 未分配名单，不落库。"""
    activity = db.get(Activity, group.activity_id)
    prj, enr_of_key, users = build_project(group, db, rules_preset, rules_cfg)
    assignments, degraded = _run(prj)

    suggest: dict[str, str] = {}       # vol_key(u:id) -> station_id
    for a in assignments:
        suggest.setdefault(a.vol_key, a.pid)

    # 细粒度冲突高亮：建议工位的时间 与 该生其他活动安排 重叠？
    rows = []
    for key, u in users.items():
        enr = enr_of_key[key]
        target = suggest.get(key)
        warning = ""
        if target:
            st = db.get(Station, target)
            warning = enroll_logic.station_conflict_warning(db, u, group, st)
        rows.append({
            "enrollment_id": enr.id,
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "gender": u.gender},
            "joined_at": iso(enr.joined_at),          # 先到先得的依据
            "current_station_id": enr.station_id,
            "suggested_station_id": target,
            "keep_current": bool(target and target == enr.station_id),
            "warning": warning,
        })

    # 未分配名单 + 原因（复用求解器自己的判据）
    assigned_keys = {a.vol_key for a in assignments}
    queue_rows = schedule_core.build_queue(prj, assignments)
    reason_of: dict[str, str] = {}
    for r in queue_rows:
        if r["vol_key"] in assigned_keys:
            continue
        reason_of.setdefault(r["vol_key"], r["reason"])
    unassigned = [{
        "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                 "college": u.college},
        "reason": reason_of.get(key, "名额不足"),
    } for key, u in users.items() if key not in assigned_keys]

    # 各工位建议人数 vs 名额
    station_stats = [{
        "station_id": st.id, "name": st.name, "quota": st.quota,
        "time": st.time_text(), "location": st.location,
        "suggested": sum(1 for v in suggest.values() if v == st.id),
    } for st in sorted(group.stations, key=lambda s: s.sort)]

    return {
        "group": {"id": group.id, "name": group.name, "time": group.span_text(),
                  "capacity": group.capacity, "activity_title": activity.title},
        "rules_preset": prj.rules.get("preset", "balanced"),
        "rules_desc": schedule_rules.describe(prj.rules),
        "rules_cfg": prj.rules,
        "degraded": degraded,
        "rows": rows,
        "unassigned": unassigned,
        "station_stats": station_stats,
        "total_confirmed": len(users),
        "total_assigned": len(assignments),
    }


def apply_plan(db: OrmSession, group: PositionGroup, actor: User,
               plan: list[dict], *, source: str = "assign",
               notify_out: list | None = None) -> dict:
    """应用分配方案（预览可经人工微调后提交）。

    plan 行: {"enrollment_id": …, "station_id": …|"", "force": false}
    校验：工位属于本组；不超名额（force 可超并标记违规强排+单独留痕）。
    notify_out 传入列表时，通知不在此处发送而是收集起来由路由层放后台——
    逐个发消息会拖慢接口，按钮就像"点不动"。
    对每个发生变化的用户发岗位分配通知。
    """
    from .audit import log as audit_log

    stations = {s.id: s for s in group.stations}
    plan_by_enr: dict[str, dict] = {}
    for row in plan:
        plan_by_enr[row.get("enrollment_id") or ""] = row
    changed: list[tuple[Enrollment, Station | None, bool]] = []

    # 第一遍校验：记录有效性 + 工位归属 + 容量（非强排行数 ≤ 名额）
    for row in plan:
        enr = db.get(Enrollment, row.get("enrollment_id") or "")
        if enr is None or enr.group_id != group.id or enr.status != "confirmed":
            raise ValueError(f"报名记录无效或不在该岗位组内: {row}")
        sid = (row.get("station_id") or "").strip()
        if sid and sid not in stations:
            raise ValueError(f"工位不属于该岗位组: {sid}")

    all_confirmed = db.query(Enrollment).filter(
        Enrollment.group_id == group.id, Enrollment.status == "confirmed").all()

    def post_station(e: Enrollment) -> str:
        """应用方案后该行落在哪个工位（不在计划内则维持现状）。"""
        row = plan_by_enr.get(e.id)
        return ((row.get("station_id") or "").strip() if row
                else (e.station_id or ""))

    nonforced_count: dict[str, int] = {}
    for e in all_confirmed:
        row = plan_by_enr.get(e.id)
        if row and row.get("force"):
            continue  # 强排不受名额约束（这正是"强制"的含义），单独留痕
        sid = post_station(e)
        if sid:
            nonforced_count[sid] = nonforced_count.get(sid, 0) + 1
    for sid, cnt in nonforced_count.items():
        if cnt > stations[sid].quota:
            raise ValueError(
                f"工位「{stations[sid].name}」超出名额（{cnt}>{stations[sid].quota}），"
                f"如确需超额请对相应人员使用强制分配")

    # 第二遍落库
    forced_rows = []
    for row in plan:
        enr = db.get(Enrollment, row["enrollment_id"])
        sid = (row.get("station_id") or "").strip()
        new_station = stations.get(sid)
        old_station = db.get(Station, enr.station_id) if enr.station_id else None
        force = bool(row.get("force"))
        if old_station == new_station and not force:
            continue
        enr.station_id = new_station.id if new_station else None
        enr.assign_forced = force
        enr.assigned_at = now()
        enr.assigned_by = actor.id
        if force:
            forced_rows.append(enr)
        changed.append((enr, new_station, force))

    for enr, st, force in changed:
        if st is None:
            continue
        if notify_out is not None:
            notify_out.append({"user_id": enr.user_id, "station_id": st.id,
                               "group_id": group.id})
            continue
        try:
            from .integrations import wecom
            wecom.send_user(db.get(User, enr.user_id), "assignment",
                            activity=db.get(Activity, group.activity_id).title,
                            group=group.name, station=st.name,
                            station_time=st.time_text(),
                            station_loc=st.location or "待定")
        except Exception:
            pass

    if not changed:
        summary = "所有人员岗位未发生变化（方案与现状一致）"
    else:
        summary = (f"{len(changed)} 人完成岗位分配"
                   f"（其中强制分配 {len(forced_rows)} 人）")
    changes = [{"field": "enrollment", "label": "报名记录",
                "old": f"{db.get(User, e.user_id).name}→{db.get(Station, e.station_id).name if e.station_id else '未分配'}",
                "new": f"{db.get(User, e.user_id).name}→{st.name if st else '未分配'}"
                       + ("（强制）" if f_ else "")}
               for e, st, f_ in changed]
    if source == "manual":
        action, label = "assign_manual", "手动调岗"
        if forced_rows:
            action, label = "force_assign", "强制分配"
        audit_log(db, actor=actor, action=action, target_type="assignment",
                  target_id=group.id, target_label=f"{group.name}",
                  activity_id=group.activity_id, summary=summary, changes=changes)
    else:
        audit_log(db, actor=actor, action="assign", target_type="assignment",
                  target_id=group.id, target_label=f"{group.name}",
                  activity_id=group.activity_id, summary=summary, changes=changes)
    db.commit()

    return {
        "applied": len(changed), "forced": len(forced_rows),
        "summary": summary,
        "rows": [{"enrollment_id": e.id, "user_name": db.get(User, e.user_id).name,
                  "station_id": st.id if st else "",
                  "station_name": st.name if st else "未分配",
                  "warning": "", "forced": f_} for e, st, f_ in changed],
    }
