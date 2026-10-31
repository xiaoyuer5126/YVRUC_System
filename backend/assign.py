"""一键分配：把「截止后统一分配」场次里的志愿者分进岗位。

为什么不用通用排班求解器：它是为「志愿者填意向时间、系统排班表」设计的，
带时间冲突图、回溯增广和超时降级。平台是即选即得，岗位没有独立时间、同场次内
互斥，剩下的问题只是「把 N 个人分到 M 个有名额、且满足硬性条件的岗位」——
标准的二分图匹配，用增广路求解，结果精确，不存在超时。

贪心不够用：岗位甲不限性别（1 人）、岗位乙限女生（1 人），报名顺序是女A、男B。
贪心让女A占了甲，男B进不了乙，乙空着、B没岗位；正确解是女A→乙、男B→甲。
增广路能自动把女A让出来。

先到先得体现在两处：候选人按报名时间排序依次匹配；抢不到时也按这个顺序让位。
"""
from __future__ import annotations

from sqlalchemy.orm import Session as OrmSession

from .enroll_logic import (group_needs_leader, group_positions,
                           position_occupied_count, unmet_requirements)
from .models import Enrollment, Position, Shift, User, now


def _eligible_positions(user: User, positions: list[Position]) -> list[str]:
    return [p.id for p in positions if not unmet_requirements(user, p)]


def match(candidates: list[tuple[str, list[str]]],
          quota: dict[str, int],
          prefer: dict[str, str] | None = None) -> dict[str, str]:
    """二分图匹配（增广路）。

    candidates  [(志愿者标识, 可去的岗位 id 列表)]，**已按先到先得排好序**
    quota       岗位 id → 名额
    prefer      志愿者标识 → 优先岗位 id（跨场次连续性：尽量还在同一个岗位）

    返回 志愿者标识 → 岗位 id，没排上的不在结果里。
    """
    slots: dict[str, list[str]] = {pid: [] for pid in quota}
    placed: dict[str, str] = {}

    def try_place(who: str, seen: set[str]) -> bool:
        options = candidates_by[who]
        if prefer and prefer.get(who) in options:
            # 连续性优先：先试他上一个场次待的岗位
            options = [prefer[who]] + [o for o in options if o != prefer[who]]
        # 一个岗位在一次增广里只看一次：进来先记进 seen，有空位就坐下，
        # 没空位再请里面的人让一让。让位这一步同样受 seen 约束——不然被挤走
        # 的人会回头去挤同一批岗位，递归呈指数爆炸。名额充足时根本走不到
        # 让位这一层，所以这个坑一直没露出来；名额不够时它会直接把请求挂死。
        for pid in options:
            if pid in seen:
                continue
            seen.add(pid)
            if len(slots[pid]) < quota[pid]:
                slots[pid].append(who)
                placed[who] = pid
                return True
            for occupant in list(slots[pid]):
                slots[pid].remove(occupant)
                placed.pop(occupant, None)
                if try_place(occupant, seen):
                    slots[pid].append(who)
                    placed[who] = pid
                    return True
                slots[pid].append(occupant)     # 让不动，放回去
                placed[occupant] = pid
        return False

    candidates_by = {who: opts for who, opts in candidates}
    for who, _opts in candidates:
        try_place(who, set())
    return placed


NOT_ASSIGN_SHIFT = ("本场次是「报名即选岗位」，志愿者报名时已各自选好岗位，"
                    "不需要排班；个别调整请用「调岗」")


def _brief(u: User) -> dict:
    return {"id": u.id, "name": u.name, "student_id": u.student_id,
            "college": u.college, "gender": u.gender}


def _plan_row(r: Enrollment, u: User, target: str | None, continuity: bool) -> dict:
    return {
        "enrollment_id": r.id,
        "user": _brief(u),
        "joined_at": r.joined_at.isoformat(timespec="minutes"),
        "current_position_id": r.position_id,
        "position_id": target,
        "keep_current": target == r.position_id,
        "leader_willing": r.leader_willing,
        "allow_reassign": r.allow_reassign,
        "continuity": continuity,
    }


def _position_stats(db: OrmSession, positions: list[Position], plan: list[dict]) -> list[dict]:
    """各岗位的名额、已在岗、本次排入。本次方案里要挪走的人不算「已在岗」。"""
    moving = {x["enrollment_id"] for x in plan}
    out = []
    for p in positions:
        staying = (db.query(Enrollment)
                   .filter(Enrollment.position_id == p.id,
                           Enrollment.status == "enrolled",
                           Enrollment.id.notin_(moving or {""})).count())
        out.append({
            "position_id": p.id, "name": p.name, "quota": p.quota,
            "existing": staying,
            "location": p.location,
            "requirement_desc": p.requirement_desc() or "不限",
            "needs_leader": p.needs_leader,
            "planned": sum(1 for x in plan if x["position_id"] == p.id),
        })
    return out


def _waiting(db: OrmSession, shift: Shift) -> list[Enrollment]:
    """「待分配」里的人（以及岗位被删后没有岗位的人），按报名先后。"""
    holder = shift.placeholder()
    q = (db.query(Enrollment)
         .filter(Enrollment.shift_id == shift.id, Enrollment.status == "enrolled"))
    q = q.filter((Enrollment.position_id.is_(None))
                 | (Enrollment.position_id == (holder.id if holder else "")))
    return q.order_by(Enrollment.joined_at).all()


def preview(db: OrmSession, shift: Shift, *, continuity: bool = True) -> dict:
    """试算一次，不落库。只排「待分配」里的人，已经在真实岗位上的人一个不动——
    他们的去向是报名时自己选的、或者管理者已经定下的，试算没有理由推翻；
    他们占着的名额照算，排的只是剩下的空位。

    continuity=True 时，如果这个人在本活动的其它场次已经排过同名岗位，
    优先让他还待在那个岗位——多天活动里换来换去，交接成本全落在现场。
    """
    if not shift.needs_assign():
        raise ValueError(NOT_ASSIGN_SHIFT)
    # 占位岗位不参与分配：它是「还没安排的人」待的地方，人只能从里面出去。
    positions = sorted(shift.real_positions(), key=lambda p: p.sort)
    if not positions:
        raise ValueError("请先在「岗位设置」里为本场次建好岗位")
    rows = _waiting(db, shift)
    if not rows:
        raise ValueError("本场次没有待分配的同学，不需要一键分配")
    users = {r.id: db.get(User, r.user_id) for r in rows}
    candidates = [(r.id, _eligible_positions(users[r.id], positions))
                  for r in rows]
    # 空位 = 名额 − 已占（在岗 + 待确认）
    quota = {p.id: max(0, p.quota - position_occupied_count(db, p.id)) for p in positions}
    prefer: dict[str, str] = {}
    if continuity:
        by_name = {p.name: p.id for p in positions}
        for r in rows:
            previous = (db.query(Position.name)
                        .join(Enrollment, Enrollment.position_id == Position.id)
                        .filter(Enrollment.user_id == r.user_id,
                                Enrollment.activity_id == shift.activity_id,
                                Enrollment.shift_id != shift.id,
                                Enrollment.status == "enrolled").first())
            if previous and previous[0] in by_name:
                prefer[r.id] = by_name[previous[0]]
    placed = match(candidates, quota, prefer)
    plan, unassigned = [], []
    for r in rows:
        u = users[r.id]
        target = placed.get(r.id)
        if target is None:
            # 排不下不等于出局：他仍然待在「待分配」，管理者可以加岗位、扩名额
            # 再跑一次，或者手动指派
            reason = ("没有满足条件的岗位"
                      if not _eligible_positions(u, positions)
                      else "可去的岗位名额已满")
            unassigned.append({"user": _brief(u), "reason": reason})
            continue
        plan.append(_plan_row(r, u, target, prefer.get(r.id) == target))
    return {
        "shift": {"id": shift.id, "name": shift.name,
                  "time": shift.span_text(),
                  "capacity": shift.total_capacity()},
        "continuity": continuity,
        "plan": plan,
        "unassigned": unassigned,
        "position_stats": _position_stats(db, positions, plan),
        "total_enrolled": len(rows),
        "total_assigned": len(plan),
    }


def plan_from_workbook(db: OrmSession, shift: Shift, data: bytes) -> dict:
    """导入排班：读管理者在 Excel 里改好的排班表，拼成一份和试算同样结构的方案。

    用的就是「导出排班表」那份文件：找这一场对应的工作表，按学号对人，「岗位」
    列按名称对到本场次的岗位（写「待分配」就是放回待分配），「组长」列不空就是
    设为组长。岗位留空的行不动。只调本场次已报名的人，不增不减人。
    不落库：方案进分配面板给人过目、可改，确认后走 apply_plan，校验与留痕同一键分配。
    另外返回 errors（第几行、什么问题）与 leaders（要设为组长的报名 id）。
    """
    from io import BytesIO
    from openpyxl import load_workbook
    from .exporter import roster_sheet_names

    if not shift.needs_assign():
        raise ValueError(NOT_ASSIGN_SHIFT)
    try:
        wb = load_workbook(BytesIO(data), read_only=True, data_only=True)
    except Exception:       # 坏文件、不是 xlsx：openpyxl 抛的异常五花八门
        raise ValueError("读不了这个文件，请上传「导出排班表」导出、改好的 .xlsx 文件")
    name = roster_sheet_names(shift.activity)[shift.id]
    if name in wb.sheetnames:
        ws = wb[name]
    elif len(wb.worksheets) == 1:
        ws = wb.worksheets[0]
    else:
        raise ValueError(f"文件里没有「{name}」这张工作表，请用「导出排班表」导出的文件修改")
    lines = ws.iter_rows(values_only=True)
    header = [str(c or "").strip() for c in next(lines, ())]
    if "学号" not in header or "岗位" not in header:
        raise ValueError(f"工作表「{ws.title}」里找不到「学号」「岗位」两列")
    col_sid, col_pos = header.index("学号"), header.index("岗位")
    col_leader = header.index("组长") if "组长" in header else None

    holder = shift.placeholder()
    by_name = {p.name: p for p in shift.real_positions()}
    rows = (db.query(Enrollment, User)
            .join(User, Enrollment.user_id == User.id)
            .filter(Enrollment.shift_id == shift.id, Enrollment.status == "enrolled").all())
    enrolled = {u.student_id: (e, u) for e, u in rows}

    def cell(line, col):
        v = line[col] if col is not None and col < len(line) else None
        if isinstance(v, float) and v.is_integer():
            v = int(v)          # 学号被 Excel 当成数字存了
        return str(v).strip() if v is not None else ""

    plan, errors, leaders, seen = [], [], [], set()
    for no, line in enumerate(lines, start=2):
        sid = cell(line, col_sid)
        if not sid:
            continue
        if sid not in enrolled:
            # 表底「以下人员尚未分配岗位」那几行也有学号，岗位是空的：一并跳过
            if cell(line, col_pos):
                errors.append({"row": no, "message": f"学号 {sid} 不在本场次的已报名名单里"})
            continue
        pos_name = cell(line, col_pos)
        if not pos_name:
            continue
        if sid in seen:
            errors.append({"row": no, "message": f"学号 {sid} 出现了不止一次，以第一次为准"})
            continue
        seen.add(sid)
        target = holder if pos_name == Position.PLACEHOLDER_NAME else by_name.get(pos_name)
        if target is None:
            errors.append({"row": no, "message": f"本场次没有「{pos_name}」这个岗位"})
            continue
        e, u = enrolled[sid]
        if target.id != e.position_id:
            plan.append(_plan_row(e, u, None if target is holder else target.id, False))
        if col_leader is not None and cell(line, col_leader) and target is not holder:
            if group_needs_leader(group_positions(db, target)):
                leaders.append(e.id)
            else:
                errors.append({"row": no, "message": f"岗位「{target.name}」没有设组长，"
                                                     "这一行的组长没有导入"})
    positions = sorted(shift.real_positions(), key=lambda p: p.sort)
    return {
        "shift": {"id": shift.id, "name": shift.name, "time": shift.span_text(),
                  "capacity": shift.total_capacity()},
        "continuity": False,
        "plan": plan,
        "unassigned": [],
        "position_stats": _position_stats(db, positions, plan),
        "total_enrolled": len(rows),
        "total_assigned": len(plan),
        "errors": errors,
        "leaders": leaders,
    }


def apply_plan(db: OrmSession, shift: Shift, actor: User, plan: list[dict], *,
               source: str = "assign", notify_out: list | None = None) -> dict:
    """落库分配方案（试算或导入的结果，可人工微调后提交；调岗也走这里）。

    plan 行：{"enrollment_id", "position_id"|"", "force": false}
    position_id 为空：assign 场次是放回「待分配」，pick 场次是不分岗位。
    校验：记录属于本场次、岗位属于本场次、硬性条件、名额。
    force 可以突破名额与条件，单独标记为强制分配并留痕——这正是「强制」的含义。

    只 flush 不提交：调用方要在同一把场次锁、同一个事务里接着递补候补
    （调走的人空出来的位子），再一起提交。
    """
    from .audit import log as audit_log
    from .enroll_logic import on_leader_vacated, sync_attached

    positions = {p.id: p for p in shift.positions}
    holder = shift.placeholder()
    rows = []
    for row in plan:
        enr = db.get(Enrollment, row.get("enrollment_id") or "")
        if enr is None or enr.shift_id != shift.id or enr.status != "enrolled":
            raise ValueError(f"报名记录无效或不在该场次内：{row.get('enrollment_id')}")
        pid = (row.get("position_id") or "").strip()
        if pid and pid not in positions:
            raise ValueError(f"岗位不属于该场次：{pid}")
        if not pid and holder is not None:
            pid = holder.id
        rows.append((enr, positions.get(pid), bool(row.get("force"))))

    # 名额：把本次方案与未列入方案的既有分配合并起来算。
    # 占名额的口径和 enroll_logic 一致：候补转正待确认的位子也已经占住了，
    # 不能当作空位再塞人。
    planned = {enr.id for enr, _p, _f in rows}
    counts: dict[str, int] = {}
    for enr in (db.query(Enrollment)
                .filter(Enrollment.shift_id == shift.id,
                        Enrollment.status.in_(("enrolled", "pending_confirm"))).all()):
        if enr.id not in planned and enr.position_id:
            counts[enr.position_id] = counts.get(enr.position_id, 0) + 1
    for enr, position, force in rows:
        if position is None or force:
            continue
        counts[position.id] = counts.get(position.id, 0) + 1
    for pid, n in counts.items():
        room = positions[pid].quota
        if n > room:
            raise ValueError(f"岗位「{positions[pid].name}」超出名额"
                             f"（{n}>{room}），"
                             f"如确需超额请对相应人员勾选「强制」")

    # 硬性条件：非强制行必须满足
    for enr, position, force in rows:
        if position is None or force:
            continue
        unmet = unmet_requirements(db.get(User, enr.user_id), position)
        if unmet:
            u = db.get(User, enr.user_id)
            raise ValueError(f"{u.name} 不满足「{position.name}」的条件："
                             f"{'；'.join(unmet)}，如确需分配请勾选「强制」")

    changed, forced, against_will, vacated = [], [], [], set()
    for enr, position, force in rows:
        old = db.get(Position, enr.position_id) if enr.position_id else None
        if old is position and not force:
            continue
        if old is not None and old is not position:
            vacated.add(old.id)
            if enr.is_leader:
                enr.is_leader = False
        # 从「待分配」分出去不算调剂：他本来就没有岗位
        if (not enr.allow_reassign and old is not None and not old.is_placeholder
                and old is not position):
            against_will.append(db.get(User, enr.user_id).name)
        changed.append((enr, old, position, force))
        enr.position_id = position.id if position else None
        enr.assign_forced = force
        enr.assigned_at = now()
        enr.assigned_by = actor.id
        if force:
            forced.append(enr)

    db.flush()
    for pid in vacated:
        on_leader_vacated(db, pid)
    # 只对某些岗位生效的培训/彩排要跟着人走：调去检录组之后，
    # 东门组那场彩排就不该再叫他，检录组那场得叫上他
    for enr, _old, _p, _f in changed:
        sync_attached(db, enr)

    for enr, _old, position, _f in changed:
        if position is None or position.is_placeholder:
            continue        # 放回「待分配」不算分到岗位，不发岗位通知
        if notify_out is not None:
            notify_out.append({"user_id": enr.user_id,
                               "position_id": position.id,
                               "shift_id": shift.id})

    if not changed:
        summary = "所有人员岗位未变化"
    else:
        summary = f"{len(changed)} 人完成岗位分配"
        if forced:
            summary += f"（含强制 {len(forced)} 人）"
        if against_will:
            summary += f"（未同意调剂仍被调换：{'、'.join(against_will)}）"

    changes = [{"field": "enrollment", "label": db.get(User, e.user_id).name,
                "old": old.name if old else "未分配",
                "new": (p.name if p else "未分配") + ("（强制）" if f else "")}
               for e, old, p, f in changed]
    action = "assign_manual" if source == "manual" else "assign"
    if forced:
        action = "force_assign"
    audit_log(db, actor=actor, action=action, target_type="assignment",
              target_id=shift.id, target_label=shift.name,
              activity_id=shift.activity_id, summary=summary, changes=changes)
    db.flush()

    return {
        "applied": len(changed), "forced": len(forced),
        "unwilling_moved": len(against_will), "against_will": against_will,
        "summary": summary,
        "rows": [{"enrollment_id": e.id,
                  "user_name": db.get(User, e.user_id).name,
                  "from_position_name": old.name if old else "未分配",
                  "position_id": p.id if p else "",
                  "position_name": p.name if p else "未分配",
                  "allow_reassign": e.allow_reassign,
                  "forced": f, "is_leader": e.is_leader}
                 for e, old, p, f in changed],
    }
