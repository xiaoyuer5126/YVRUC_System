"""API 序列化辅助：模型 → dict。

列表接口只带概览数字（避免为显示"还剩3个名额"拉整份状态）；
详情接口才带岗位树全量。
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm.attributes import set_committed_value

from . import pubcache
from .enroll_logic import (Occupancy, active_rows, applies_to_shift, conflicts_among,
                           dedupe_conflicts,
                           enroll_deadline, group_leader_task, group_needs_leader, lock_at,
                           shift_counts, unmet_for_tags, waitlist_position)
from .models import (Activity, Application, Attachment, AuditLog, Enrollment,
                     FormQuestion, Position, Shift, ShiftPeriod, User, iso, now)


def iso_of(dt):
    return iso(dt)


def reg_started(reg_start, t) -> bool:
    """报名开始了没有。

    **全站「开没开始报名」只有这一处比较**：展示状态、各场次的 enroll_open、
    倒计时毫秒数、报名接口的拦截都走它。各算各的就会差出零点几秒：比如用截断
    成整秒的 opens_in，会比报名接口认的 reg_start 早不到一秒——手最快的那批人
    倒计时归零、按钮亮了，点下去却是「报名尚未开始」。
    """
    return reg_start is None or t >= reg_start


def display_status_of(status: str, reg_start, reg_end,
                      span, last_start=None, t=None) -> tuple[str, str]:
    """按状态与时间算展示状态。

    状态流转由定时任务每分钟推进，中间那一分钟里 status 还没跟上；报名开始
    时间更是完全不体现在 status 上——「已发布但还没到报名时间」的活动，
    只看 status 就会显示「报名中」，学生点进去才发现报不了。

        筹备中 → 未开始报名 → 报名中 → 已截止 → 进行中 → 已结束

    报名中和进行中可以同时成立：场次排在好几天里，第一场开过了、后面的
    还收人（每场在自己开场时才截止，见 enroll_logic.enroll_deadline）。
    这时显示「报名中」——标成进行中，按状态筛「报名中」的人就看不见这些
    还能报的场次了。last_start 是最晚一个服务场次的开始时间，过了它就一场
    也报不进去了。没填报名截止的活动在第一场开场时由定时任务截止，不算。

    拆成不吃 Activity 的一版：广场列表筛状态时手上只有几个字段，不能为了
    算状态把每个活动的场次都加载一遍。
    """
    t = t or now()
    # 三种「还没公开」的状态在展示层是一回事：学生看不到，管理端各有各的按钮
    if status == "draft":
        return "draft", "草稿"
    if status == "pending":
        return "draft", "待审批"
    if status == "approved":
        return "draft", "待发布"
    if status in ("finished", "vb_pending", "completed"):
        return "finished", Activity.STATUS_LABELS[status]

    if span and t > span[1]:
        return "finished", "已结束"
    if (status == "published" and reg_started(reg_start, t)
            and reg_end and t <= reg_end and last_start and t <= last_start):
        return "published", "报名中"
    if span and span[0] <= t <= span[1]:
        return "ongoing", "进行中"

    if status == "published":
        if not reg_started(reg_start, t):
            return "upcoming", "未开始报名"
        if reg_end and t > reg_end:
            return "closed", "已截止"
        return "published", "报名中"
    return "closed", "已截止"


# 管理端列表照原样显示的几种状态：各有各的按钮与下一步，分得开才点得对
ADMIN_STATUSES = ("draft", "pending", "approved", "vb_pending", "completed")


def admin_status_of(status: str, reg_start, reg_end, span, last_start=None,
                    t=None) -> tuple[str, str]:
    """管理端列表上显示的状态码与标签。

    已发布之后按时间算，与广场同一套（见 display_status_of）：没到报名时间的
    标「未开始报名」，进行中、已截止、已结束都按当前时刻定。活动管理页的状态列
    与状态筛选都读它——只看 status 的话，报名时间还没到的活动在管理页上写着
    「报名中」，跟学生在广场上看到的那一条对不上。

    还没公开的三态（草稿 / 待审批 / 待发布）与等外联确认的两态（待外联确认 /
    已完成）各有各的按钮和下一步，照原样显示，不并进按时间算的那几档：管理端
    要的是「我还能对它做什么」，学生端要的是「现在能不能报」。
    """
    if status in ADMIN_STATUSES:
        return status, Activity.STATUS_LABELS[status]
    return display_status_of(status, reg_start, reg_end, span, last_start, t)


def published_hint(reg_start, t=None) -> str:
    """「发布之后学生什么时候能报」的一句话，接在「发布后学生即可看到活动」后面。

    设了将来的报名开始时间就说清是哪一刻开始；没设、或已经到点，才是现在能报。
    一律写「即可报名」的话，报名明早才开始的活动发布出去，学生点开却报不了。
    """
    t = t or now()
    if reg_start is not None and not reg_started(reg_start, t):
        return f"报名将于 {reg_start:%m-%d %H:%M} 开始"
    return "现在可以报名"


# 占位岗位给学生看的说法。管理端仍叫「待分配」——那是给老师看的账。
PLACEHOLDER_LABEL = "暂不选岗位 · 由负责人后续统一分配"


def tags_desc(tags: dict | None) -> str:
    """把 {标签: [值…]} 写成人话：「校区：中关村校区；学段：本科生/硕士研究生」。"""
    parts = []
    for tag, value in (tags or {}).items():
        vals = value if isinstance(value, list) else [value]
        vals = [str(v) for v in vals if str(v).strip()]
        if vals:
            parts.append(f"{tag}：{'/'.join(vals)}")
    return "；".join(parts)


def recruit_scope_phrase(act) -> str:
    """招募范围放进句子里的说法，接在「现面向」后面：全校同学、中关村校区本科生、
    信息学院、法学院硕士研究生。"""
    where = "、".join(act.recruit_campuses or []) + "、".join(act.recruit_colleges or [])
    stages = "、".join(act.recruit_stages or [])
    return f"{where or '全校'}{stages or '同学'}"


def applies_text(act, s) -> str:
    """培训/彩排叫谁：「全体」，或者岗位名。同名岗位（几个场次都有「检录」）
    全圈上了只写名字，只圈了其中几个时段的带上场次名。"""
    ids = s.applies_to_positions or []
    if not ids:
        return "全体"
    by_name: dict[str, list] = defaultdict(list)
    for g in sorted(act.main_shifts(), key=lambda x: x.sort):
        for p in sorted(g.positions, key=lambda x: x.sort):
            by_name[p.name].append((p.id, g.name))
    out = []
    for name, items in by_name.items():
        hit = [gname for pid, gname in items if pid in ids]
        if hit:
            out.append(name if len(hit) == len(items) else f"{name}（{'、'.join(hit)}）")
    return "、".join(out)


def recruit_scope_text(act) -> str:
    """招募范围的一句话说明；三项都没限就是「面向全校」。"""
    text = tags_desc({"校区": act.recruit_campuses or [],
                      "学段": act.recruit_stages or [],
                      "学院": act.recruit_colleges or []})
    return text or "面向全校"


def opens_in_ms(reg_start, t) -> int:
    """距报名开始还有多少**毫秒**；已经开始或没设开始时间给 0。

    向上取整，且没开始就至少是 1：宁可倒计时晚归零一毫秒，也不能早——早了
    按钮亮了、点下去却是「尚未开始」。与 reg_started 永远说同一句话。
    """
    if reg_started(reg_start, t):
        return 0
    return max(1, math.ceil((reg_start - t).total_seconds() * 1000))


def opens_in(act: Activity, t=None) -> int:
    """距报名开始还有多少秒（向上取整）；已经开始或没设开始时间给 0。

    发**时长**而不是让前端拿 reg_start 自己减：客户端时钟偏几分钟是常事，
    自己减出来的倒计时会比服务端早到或晚到，按钮跟着错。时长只用到客户端的
    「流逝时间」，那部分是准的。前端用的是更精确的 opens_in_ms，这个整秒数
    留给老调用方；向上取整，所以它不会在还没开始时就报 0。
    """
    return math.ceil(opens_in_ms(act.reg_start, t or now()) / 1000)


# ======================= 因人而异的字段 =======================
#
# 学生端的活动详情与广场卡片，「人人都一样」的部分会在 Redis 里存 3 秒、
# 给所有人共用（见 pubcache）。所以凡是随「谁在看」而变的字段，一律不许
# 进 _detail_public / _brief_public，只能在 _for_viewer 里每次现算：
#
#   活动      my_time_conflicts   我的有效报名里与本活动场次压上时的提示（含本活动别的场次）
#             application         报名后筛选：题目 + **我的**申请
#             contact_group_qr    联络群二维码，只给管理者
#             attire_issued       只给管理者（后台有没有服装台账）
#             manager / review_preview   路由里按人填
#   场次      my_enrollment       我的报名状态、候补名次、是否组长、确认期限
#   培训/彩排 mine / my_enrollment_id / excused / excuse_reason
#   岗位      eligible / ineligible_reason     按我的档案匹配条件
#             training_visible / training_note / attachments
#                                 报了本场次的人和管理者才看得到
#             leader_name         同上
#             leader_contact      同组**正式**报名的人和管理者才看得到
#
# 与「此刻」有关的字段同样不进缓存，按原始时间每次现算（_with_times）：
#   display_status / display_label / opens_in / opens_in_ms，
#   场次的 enroll_open / started / closes_in_ms / locked（到点自动锁定名单）。
# 否则报名开始那一刻缓存里的「未开始」会再挂 3 秒，按钮刷新后又灰回去。
#
# 私密内容（培训内容、材料列表、组长）**根本不经过缓存**，而不是存进去再挑着
# 给：缓存里没有它，就不存在「忘了挑」把它发给外人的那一天。

def _ts(dt):
    return dt.isoformat() if dt else None


def _dt(text):
    return datetime.fromisoformat(text) if text else None


def _time_basis(act: Activity, *, shifts: bool) -> dict:
    """算「此刻」相关字段要用的原始时间，精确到微秒（iso_of 只到分钟）。"""
    span = act.time_span()
    out = {"status": act.status,
           "reg_start": _ts(act.reg_start), "reg_end": _ts(act.reg_end),
           "span": [_ts(span[0]), _ts(span[1])] if span else None,
           "last_start": _ts(act.last_shift_start())}
    if shifts:
        # [场次, 开始, 结束, 本场截止, 自动锁定时刻]，按 act.shifts 的顺序、含
        # 培训/彩排——时段冲突要逐场比，培训那晚撞了别的活动也得提示。
        # 自动锁定被管理者手动解锁用掉了的，锁定时刻给 None（见 enroll_logic.auto_locked）
        out["shifts"] = [[g.id, _ts(g.start_time), _ts(g.end_time),
                          _ts(enroll_deadline(act, g)),
                          None if g.auto_lock_spent else _ts(lock_at(g))]
                         for g in act.shifts]
    return out


def with_times(out: dict, t=None) -> dict:
    """把公共部分里的时间原料（_t）换成此刻的状态。每次请求都要走一遍。"""
    basis = out.pop("_t")
    t = t or now()
    reg_start = _dt(basis["reg_start"])
    span = basis["span"] and (_dt(basis["span"][0]), _dt(basis["span"][1]))
    code, label = display_status_of(basis["status"], reg_start,
                                    _dt(basis["reg_end"]), span,
                                    _dt(basis["last_start"]), t)
    ms = opens_in_ms(reg_start, t)
    out.update({"display_status": code, "display_label": label,
                "opens_in_ms": ms, "opens_in": math.ceil(ms / 1000),
                # 发布确认与发布成功提示里那句话，见 published_hint
                "publish_hint": published_hint(reg_start, t)})
    if "shifts" in out:
        times = {sid: (_dt(start), _dt(dl), _dt(auto_at))
                 for sid, start, _end, dl, auto_at in basis["shifts"]}
        started = reg_started(reg_start, t)
        for g in out["shifts"]:
            start, deadline, auto_at = times[g["id"]]
            # 名单锁定：手动锁着，或到了自动锁定时刻。口径同 enroll_logic.shift_locked
            g["locked"] = g["list_locked"] or (auto_at is not None and t >= auto_at)
            # 本场次的报名闸门，与报名接口的判断逐字相同（见 reg_started）：
            # 前端据此灰按钮，别让人点了才知道这一场已经开过了，或者报名
            # 压根还没开始——「活动已发布」不等于「现在能报」
            g["enroll_open"] = started and t <= deadline
            g["started"] = t >= start
            # 离本场截止还有多少毫秒：倒计时归零、前端本地开闸时靠它判断
            # 哪几场还收人，不拿客户端时钟去比截止时间（道理同 opens_in）
            g["closes_in_ms"] = max(0, math.floor(
                (deadline - t).total_seconds() * 1000))
    return out


def load_trees(db: OrmSession, acts: list[Activity]) -> None:
    """一批活动的场次、时段与岗位，三条 SQL 装好。

    懒加载的话是一个活动一条（场次）、一个场次一条（时段、岗位），广场一页
    十几张卡片、详情页十几个场次，条数跟着涨。已经加载过的不重查。
    """
    todo = [a for a in acts if "shifts" not in a.__dict__]
    if todo:
        by_act = defaultdict(list)
        for g in (db.query(Shift)
                  .filter(Shift.activity_id.in_([a.id for a in todo]))
                  .order_by(Shift.start_time).all()):
            by_act[g.activity_id].append(g)
        for a in todo:
            set_committed_value(a, "shifts", by_act[a.id])
    shifts = [g for a in acts for g in a.shifts]
    todo = [g for g in shifts if "periods" not in g.__dict__]
    if todo:
        by_shift = defaultdict(list)
        for p in (db.query(ShiftPeriod)
                  .filter(ShiftPeriod.shift_id.in_([g.id for g in todo]))
                  .order_by(ShiftPeriod.start_time, ShiftPeriod.sort).all()):
            by_shift[p.shift_id].append(p)
        for g in todo:
            set_committed_value(g, "periods", by_shift[g.id])
    todo = [g for g in shifts if "positions" not in g.__dict__]
    if todo:
        by_shift = defaultdict(list)
        for p in (db.query(Position)
                  .filter(Position.shift_id.in_([g.id for g in todo]))
                  .order_by(Position.sort).all()):
            by_shift[p.shift_id].append(p)
        for g in todo:
            set_committed_value(g, "positions", by_shift[g.id])


def _brief_public(act: Activity, counts: dict) -> dict:
    """广场卡片里人人都一样的部分。counts 见 enroll_logic.shift_counts。"""
    # 只数服务场次：培训/彩排不招人，算进「共几场、多少名额」会把学生看晕
    shifts = sorted(act.main_shifts(), key=lambda g: g.sort)
    total_cap = sum(g.total_capacity() for g in shifts)
    total_conf = sum(counts.get(g.id, (0, 0, 0))[0] for g in shifts)
    total_used = sum(counts.get(g.id, (0, 0, 0))[1] for g in shifts)
    span = act.time_span()
    return {
        "id": act.id, "title": act.title, "recruit_mode": act.recruit_mode,
        "organizer": act.organizer, "co_organizer": act.co_organizer or "",
        # 显示一律用这个（联合举办是「A × B」），别在各端自己拼
        "organizer_text": act.organizer_text(),
        "recruit_label": act.recruit_label(), "status": act.status,
        "status_label": act.label_status(), "location": act.location,
        "intro": act.intro[:120], "cover_hue": act.cover_hue,
        "shifts_count": len(shifts),
        "total_capacity": total_cap, "total_enrolled": total_conf,
        "remaining": max(0, total_cap - total_used),
        "time_text": act.time_text(),
        # 活动整体起止（见 Activity.time_span），没有场次时为空
        "time_start": iso_of(span[0]) if span else None,
        "time_end": iso_of(span[1]) if span else None,
        "reg_start": iso_of(act.reg_start), "reg_end": iso_of(act.reg_end),
        # 补记录活动（不公开招募）：没有场次，活动时间直接填在这两项上
        "record_only": act.is_record(),
        "held_start": iso_of(act.held_start), "held_end": iso_of(act.held_end),
        "contacts": act.contacts or [],
        "editing": act.editing_since is not None,
        "pinned": act.pinned_at is not None,
        "urgent": act.pinned_at is not None and act.status == "published",
        "_t": _time_basis(act, shifts=False),
    }


def brief_parts(db: OrmSession, ids: list[str]) -> dict[str, dict]:
    """一批活动的卡片公共部分：{id: dict}，SQL 条数与活动个数无关。

    返回的还带着时间原料，发出去之前要过 with_times。
    """
    acts = db.query(Activity).filter(Activity.id.in_(ids)).all() if ids else []
    load_trees(db, acts)
    counts = shift_counts(db, [a.id for a in acts])
    return {a.id: _brief_public(a, counts) for a in acts}


def activity_brief(db: OrmSession, act: Activity, counts=None) -> dict:
    """一张活动卡片（管理端列表直接用；广场走 brief_parts + 缓存）。

    counts：shift_counts 一次查好的一批；不给就只为这一个活动查一次。
    """
    if counts is None:
        counts = shift_counts(db, [act.id])
    return with_times(_brief_public(act, counts))


def _unassigned_count(db: OrmSession, g: Shift) -> int:
    """本场次还没安排到真实岗位的人数。

    「截止后统一分配」的场次里，这些人待在占位岗位上；pick 场次里也可能有
    岗位为空的（岗位被删过，或管理员手工补录）。两种都算。
    活动详情页里同一个数从 Occupancy 里取（shift_unplaced + 占位岗位人数）。
    """
    holder = g.placeholder()
    q = (db.query(Enrollment)
         .filter(Enrollment.shift_id == g.id,
                 Enrollment.status == "enrolled"))
    if holder is None:
        return q.filter(Enrollment.position_id.is_(None)).count()
    return q.filter((Enrollment.position_id.is_(None))
                    | (Enrollment.position_id == holder.id)).count()


def _group_of(p: Position, positions: list[Position]) -> list[Position]:
    """同组岗位（含自己）。口径同 enroll_logic.group_positions，只是在本场次
    已加载的岗位里找，不必一个岗位查一次。"""
    if not (p.group_name or "").strip():
        return [p]
    return [x for x in positions if x.group_name == p.group_name]


def _position_public(p: Position, siblings: list[Position], occ: Occupancy,
                     n_materials: int) -> dict:
    """岗位里人人都一样的部分。培训内容、材料列表、组长、能不能报见 _for_viewer。

    占位岗位的人数按整场数（同 enroll_logic.has_room）：它的名额就是这一场的
    招募人数，分到真实岗位的人也还占着。
    """
    whole = p.is_placeholder
    return {
        "id": p.id, "name": p.name,
        # 岗位没有独立时间：显示所属场次的时段
        "time_text": p.time_text(),
        "location": p.location, "quota": p.quota,
        "enrolled": occ.shift_enrolled(p.shift_id) if whole else occ.position_enrolled(p.id),
        "remaining": max(0, p.quota - (occ.shift_occupied(p.shift_id) if whole
                                       else occ.position_occupied(p.id))),
        "waitlist_quota": p.waitlist_quota,
        "waitlist_count": occ.position_wait(p.id),
        "attire": p.attire, "attire_label": p.attire_label(),
        "attire_note": p.attire_note, "attire_text": p.attire_text(),
        "pay_optional": p.pay_optional, "pay_note": p.pay_note,
        "pay_text": p.pay_text(),
        "requirement_tags": p.requirement_tags or {},
        "requirement_desc": p.requirement_desc() or "不限",
        "service_task": p.service_task,
        "position_requirement": p.position_requirement,
        "group_name": p.group_name,
        # 组长任务：指名组长时要告诉他做什么（任命通知里也带一份）。一个组
        # 共用一名组长、也共用一份任务，所以按组给
        "leader_task": group_leader_task(siblings),
        "is_placeholder": p.is_placeholder,
        "enroll_paused": p.enroll_paused,
        "list_locked": p.list_locked,
        # 占位岗位不是一个岗位，是「这一场怎么报」的说明，所以换个说法
        "display_name": PLACEHOLDER_LABEL if p.is_placeholder else p.name,
        "sort": p.sort,
        # 材料数量始终公开：让人知道「报名后有 3 份培训材料」是招募信息，
        # 内容本身才需要报名后才能看
        "material_count": n_materials,
        # 组长按「组」取：同组岗位共用一名组长
        "group_positions": ([x.name for x in siblings]
                            if len(siblings) > 1 else []),
        "needs_leader": group_needs_leader(siblings),
    }


def attachments_by_owner(db: OrmSession, owner_type: str,
                         owner_ids: list[str]) -> dict[str, list[dict]]:
    """一批 owner 的材料，一次查完：{owner_id: [材料…]}。

    活动详情页要列十几个岗位的材料，各自去查的话就是十几条 SQL，而结果
    往往还是空的。
    """
    ids = list(dict.fromkeys(owner_ids))
    out: dict[str, list[dict]] = {i: [] for i in ids}
    if not ids:
        return out
    rows = (db.query(Attachment)
            .filter(Attachment.owner_type == owner_type,
                    Attachment.owner_id.in_(ids))
            .order_by(Attachment.created_at).all())
    for a in rows:
        out.setdefault(a.owner_id, []).append({
            "id": a.id, "kind": a.kind, "title": a.title, "size": a.size,
            "url": (a.url if a.kind == "link" else f"/api/materials/{a.id}")})
    return out


def _material_counts(db: OrmSession, position_ids: list[str]) -> dict[str, int]:
    """各岗位有几份材料，一条分组查询（公共部分只要数量，不要列表）。"""
    if not position_ids:
        return {}
    return dict(db.query(Attachment.owner_id, func.count(Attachment.id))
                .filter(Attachment.owner_type == "position",
                        Attachment.owner_id.in_(position_ids))
                .group_by(Attachment.owner_id).all())


def _attached_public(s: Shift, occ: Occupancy) -> dict:
    """培训/彩排的一行摘要，人人都一样的部分。

    自己不招人，所以没有名额与候补；关心的是什么时候、在哪、叫谁、算不算时长。
    """
    return {
        "excused_count": occ.shift_excused(s.id),
        "id": s.id, "name": s.name, "kind": s.kind, "kind_label": s.kind_label(),
        "start_time": iso_of(s.start_time), "end_time": iso_of(s.end_time),
        "periods": [p.out() for p in s.sorted_periods()],
        "time_text": s.span_text(), "location": s.location,
        "note": s.note,
        "hours_mode": s.hours_mode, "hours_label": s.hours_label(),
        "requirement_tags": s.requirement_tags or {},
        "requirement_desc": tags_desc(s.requirement_tags) or "不限",
        "applies_to_positions": s.applies_to_positions or [],
        "applies_text": applies_text(s.activity, s),
        "enrolled": occ.shift_enrolled(s.id),
    }


def _shift_public(g: Shift, occ: Occupancy, n_materials: dict) -> dict:
    positions = sorted(g.positions, key=lambda p: p.sort)
    capacity = g.total_capacity()
    holder = g.placeholder()
    return {
        "id": g.id, "name": g.name,
        "activity_id": g.activity_id,
        "start_time": iso_of(g.start_time), "end_time": iso_of(g.end_time),
        # 时段明细：编辑表单、签到面板选时段、「这几天上午/下午」的展示都靠它
        "periods": [p.out() for p in g.sorted_periods()],
        "time_text": g.span_text(),
        "enroll_deadline": iso_of(enroll_deadline(g.activity, g)),
        # 名额一律是各岗位之和；assign 场次的名额挂在占位岗位上
        "capacity": capacity,
        "enrolled": occ.shift_enrolled(g.id),
        "remaining": max(0, capacity - occ.shift_occupied(g.id)),
        "needs_assign": g.needs_assign(),
        "enroll_paused": g.enroll_paused,
        # 名单锁定：志愿者端写「开场前 N 小时锁定名单」，锁了标「名单已锁定」
        "lock_hours": g.lock_hours,
        "lock_at": iso_of(lock_at(g)),
        # 手动锁定的标记；此刻锁没锁（含自动锁定）是 locked，见 with_times
        "list_locked": g.list_locked,
        "mode": g.mode,
        "mode_label": g.mode_label(),
        "flexible_time": bool(g.flexible_time),
        "location": g.location,
        "kind": g.kind, "kind_label": g.kind_label(),
        "hours_mode": g.hours_mode, "hours_label": g.hours_label(),
        "requirement_tags": g.requirement_tags or {},
        "requirement_desc": tags_desc(g.requirement_tags) or "不限",
        # 叫这一场的人的培训/彩排：学生看的是「报这个岗位还要去哪几场」
        "attached": [_attached_public(s, occ) for s in g.activity.trainings()
                     if applies_to_shift(s, g)],
        "note": g.note, "sort": g.sort,
        "positions": [_position_public(p, _group_of(p, positions), occ,
                                       n_materials.get(p.id, 0))
                      for p in positions],
        # 还没安排到真实岗位的人：待在占位岗位里的，加上岗位为空的
        # （岗位被删或管理员补录留下的）。管理者要当场看见这个缺口，
        # 而不是等分配面板报「未排上」。口径同 _unassigned_count。
        "unassigned_count": (occ.shift_unplaced(g.id)
                             + (occ.position_enrolled(holder.id) if holder else 0)),
        # 真实岗位的名额之和，不含占位——拿它跟已报名比才看得出还差多少位子
        "position_quota_sum": sum(p.quota for p in g.real_positions()),
        "waitlist_count": occ.shift_wait(g.id),
    }


def application_out(db: OrmSession, act: Activity, user=None) -> dict:
    """报名后筛选的活动：报名问卷题目，以及这个人自己的申请（没有则 None）。"""
    questions = (db.query(FormQuestion)
                 .filter(FormQuestion.activity_id == act.id)
                 .order_by(FormQuestion.sort).all())
    app = None
    if user is not None:
        app = (db.query(Application)
               .filter(Application.activity_id == act.id,
                       Application.user_id == user.id).first())
    wanted = []
    if app is not None:
        rows = (db.query(Enrollment, Shift)
                .join(Shift, Enrollment.shift_id == Shift.id)
                .filter(Enrollment.activity_id == act.id,
                        Enrollment.user_id == user.id,
                        Shift.kind == "service")
                .order_by(Shift.sort).all())
        # 同一场次可以勾好几个意向岗位，完整的一份记在申请上
        picked = {p.id: p for p in
                  db.query(Position).filter(
                      Position.id.in_(app.wanted_positions or [""])).all()}
        wanted = []
        for e, g in rows:
            names = [picked[i].name for i in (app.wanted_positions or [])
                     if i in picked and picked[i].shift_id == g.id]
            if not names and e.position_id:
                p = db.get(Position, e.position_id)
                names = [p.name] if p else []
            wanted.append({"shift_id": g.id, "shift_name": g.name,
                           "position_name": "、".join(names),
                           "status": e.status, "cancel_type": e.cancel_type})
    return {
        "questions": [q.out() for q in questions],
        "mine": ({"id": app.id, "status": app.status,
                  "status_label": app.status_label(),
                  "answers": app.answers or {}, "wanted": wanted,
                  "wanted_positions": app.wanted_positions or [],
                  "leader_willing": app.leader_willing,
                  "created_at": iso_of(app.created_at)}
                 if app else None),
    }


def _detail_public(db: OrmSession, act: Activity) -> dict:
    """活动详情里人人都一样的部分（可缓存）。SQL 条数与场次、岗位个数无关。"""
    load_trees(db, [act])
    # 各场次/岗位的人数一次查完：这一页要列十几个岗位的「已报/占用/占位/候补」，
    # 各自去 count 的话光这一页就是几十条 SQL
    occ = Occupancy(db, act.id)
    n_materials = _material_counts(
        db, [p.id for g in act.shifts for p in g.positions])
    out = _brief_public(act, {g.id: (occ.shift_enrolled(g.id),
                                     occ.shift_occupied(g.id), 0)
                              for g in act.shifts})
    out["_t"] = _time_basis(act, shifts=True)
    out.update({
        "intro": act.intro,
        "location_point": act.location_point,
        "volunteer_requirement": act.volunteer_requirement,
        "recruit_campuses": act.recruit_campuses or [],
        "recruit_stages": act.recruit_stages or [],
        "recruit_colleges": act.recruit_colleges or [],
        "recruit_scope_text": recruit_scope_text(act),
        "approval_dept_id": act.approval_dept_id or "",
        "auto_publish": act.auto_publish,
        "volunteer_benefit": act.volunteer_benefit,
        # 全部培训/彩排（挂在整个活动上）：管理端列一处，「按岗位看」按岗位挑；
        # 每个服务场次的 attached 里另有叫这一场的那几个
        "trainings": [_attached_public(s, occ) for s in act.trainings()],
        "shifts": sorted([_shift_public(g, occ, n_materials)
                          for g in act.main_shifts()],
                         key=lambda x: x["start_time"]),
    })
    if not act.is_screened():
        # 即选即得的报名问卷：题目直接给，报名弹窗里答；答案记在报名记录上。
        # 报名后筛选的题目跟着「我的申请」一起在 _for_viewer 里给
        out["questions"] = [q.out() for q in db.query(FormQuestion)
                            .filter(FormQuestion.activity_id == act.id)
                            .order_by(FormQuestion.sort).all()]
    logs = (db.query(AuditLog)
            .filter(AuditLog.activity_id == act.id, AuditLog.visible.is_(True))
            .order_by(AuditLog.created_at.desc()).limit(10).all())
    out["change_logs"] = [l.out() for l in logs]
    return out


def _leaders(db: OrmSession, activity_id: str) -> dict[str, User]:
    """本活动各岗位的组长：{岗位 id: 用户}，一条 SQL。"""
    rows = (db.query(Enrollment.position_id, User)
            .join(User, User.id == Enrollment.user_id)
            .filter(Enrollment.activity_id == activity_id,
                    Enrollment.is_leader.is_(True),
                    Enrollment.status == "enrolled")
            .order_by(Enrollment.joined_at).all())
    out: dict[str, User] = {}
    for pid, u in rows:
        out.setdefault(pid, u)
    return out


def _for_viewer(db: OrmSession, act: Activity, out: dict, user,
                manager: bool) -> dict:
    """在公共部分上补「因人而异」的字段（清单见上），再按此刻算时间字段。

    out 会被就地改掉——它要么是刚现算的，要么是刚从缓存里解出来的新对象。
    """
    basis = out["_t"]
    # 我在本活动每个场次的有效报名（含培训/彩排），一条 SQL
    mine: dict[str, Enrollment] = {}
    if user is not None:
        mine = {e.shift_id: e for e in db.query(Enrollment).filter(
            Enrollment.activity_id == act.id, Enrollment.user_id == user.id,
            Enrollment.status.in_(("enrolled", "waitlisted", "pending_confirm")))}

    # 培训内容、材料列表、组长：只对报了该场次的人和管理者可见。培训材料是
    # 发给要上岗的人的，组长姓名属于个人信息，不该挂在公开页面上供人浏览。
    # 只在真用得上时才查——来抢报的人绝大多数一样也看不到。
    notes: dict = {}
    atts: dict = {}
    leaders: dict = {}
    visible_pids = [p["id"] for g in out["shifts"]
                    if manager or g["id"] in mine for p in g["positions"]]
    if visible_pids:
        notes = dict(db.query(Position.id, Position.training_note)
                     .filter(Position.id.in_(visible_pids)).all())
        atts = attachments_by_owner(db, "position", visible_pids)
        leaders = _leaders(db, act.id)

    for g in out["shifts"]:
        my_enr = mine.get(g["id"])
        enrolled = my_enr is not None
        visible = enrolled or manager
        for p in g["positions"]:
            group = ([p["id"]] if not (p["group_name"] or "").strip() else
                     [x["id"] for x in g["positions"]
                      if x["group_name"] == p["group_name"]])
            leader = next((leaders[i] for i in group if i in leaders), None)
            # 组长联系方式只给**同组已正式报名**的人：现场要找得到组长，
            # 但候补和别的组的人拿他的手机号没有意义，只是多暴露一份个人信息。
            same_group = (my_enr is not None and my_enr.status == "enrolled"
                          and my_enr.position_id in group)
            p["training_visible"] = visible
            p["training_note"] = notes.get(p["id"], "") if visible else ""
            p["attachments"] = atts.get(p["id"], []) if visible else []
            p["leader_name"] = leader.name if (visible and leader) else ""
            p["leader_contact"] = (
                {"phone": leader.phone, "wechat": leader.wechat}
                if leader and (manager or same_group) else None)
            # 带 user 时附上「这个人能不能报」——前端据此灰掉并说明原因，
            # 比让人点了再被拒要清楚
            if user is not None:
                unmet = unmet_for_tags(user, p["requirement_tags"])
                p["eligible"] = not unmet
                p["ineligible_reason"] = "；".join(unmet)
        for s in g["attached"]:
            applies = s["applies_to_positions"]
            # 我这次报名要不要去这一场
            s["mine"] = (my_enr is not None and my_enr.status == "enrolled"
                         and (not applies or my_enr.position_id in applies))
            # 我在这一场的报名记录（培训/彩排是连带产生的），用来显示请假状态
            row = mine.get(s["id"]) if s["mine"] else None
            if row is not None and row.status != "enrolled":
                row = None
            s["my_enrollment_id"] = row.id if row else ""
            s["excused"] = bool(row and row.excused)
            s["excuse_reason"] = row.excuse_reason if row else ""
        if my_enr is not None:
            g["my_enrollment"] = {
                "enrollment_id": my_enr.id, "status": my_enr.status,
                "position_id": my_enr.position_id,
                "allow_reassign": my_enr.allow_reassign,
                "is_leader": my_enr.is_leader,
                "confirm_deadline": iso_of(my_enr.confirm_deadline),
                "waitlist_position": (waitlist_position(db, my_enr)
                                      if my_enr.status != "enrolled" else 0),
            }

    # 联络群二维码不公开：志愿者报名成功后才弹给本人，管理者在后台能看
    if manager:
        out["contact_group_qr"] = act.contact_group_qr or ""
        # 有岗位统一发放服装，后台才有「志愿服装」页；自备的没有台账可管
        out["attire_issued"] = any(p.attire == "issued"
                                   for g in act.shifts for p in g.positions)
    if out["recruit_mode"] == "screened":
        out["application"] = application_out(db, act, user)
    # 「您有时间冲突的活动」（报名前预检用）：我的有效报名查一次，逐场次在内存里比。
    # 本活动里我报了的场次也比：它跟本活动别的场次压着，再报那一场就是同一时间
    # 两头跑（比某一场时不算那一场自己，见 conflicts_among）。本活动的意向记录
    # （筛选活动的 applied）不比：意向之间本来就可以重叠，他只是说这几场都能来。
    # 列表里同一条报名只留一行：本活动有几个场次跟它压上，那也只是同一段报名。
    if user is not None:
        rows = [r for r in active_rows(db, user)
                if r[2].id != act.id or r[0].status != "applied"]
        clashes: list[dict] = []
        for g in act.shifts:
            clashes += conflicts_among(rows, g)
        out["my_time_conflicts"] = dedupe_conflicts(clashes)
    return with_times(out)


def activity_detail(db: OrmSession, act: Activity, user=None, *,
                    manager: bool = False, cached: bool = False) -> dict:
    """活动详情。

    cached=True：人人都一样的部分走 3 秒缓存（见 pubcache）；因人而异的部分
    与时间字段照样每次现算。管理者看自己的活动不要开——改完要立刻看到原样。
    """
    if cached:
        pub = pubcache.get_many("detail", [act.id],
                                lambda _ids: {act.id: _detail_public(db, act)})
        out = pub[act.id]
    else:
        out = _detail_public(db, act)
    return _for_viewer(db, act, out, user, manager)
