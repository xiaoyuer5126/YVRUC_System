"""一键分配：把「截止后统一分配」场次里的志愿者分进岗位。

为什么不用通用排班求解器：原来那套是为「志愿者填意向时间、系统排班表」设计的，
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

from .enroll_logic import (position_enrolled_count, position_occupied_count,
                           unmet_requirements)
from .models import Enrollment, Position, Shift, User, now


def _eligible_positions(user: User, positions: list[Position]) -> list[str]:
    return [p.id for p in positions if not unmet_requirements(user, p)]


def _held_seats(db: OrmSession, positions: list[Position]) -> dict[str, int]:
    """各岗位被临期退选扣留的名额数。

    扣留的位子不释放给任何人，分配同样不能用：不减掉的话，方案会把它当空位
    分出去，等于绕开了扣留规则。
    """
    return {p.id: (position_occupied_count(db, p.id)
                   - position_enrolled_count(db, p.id)) for p in positions}


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


def preview(db: OrmSession, shift: Shift, *, continuity: bool = True) -> dict:
    """试算一次，不落库。

    continuity=True 时，如果这个人在本活动的其它场次已经排过同名岗位，
    优先让他还待在那个岗位——多天活动里换来换去，交接成本全落在现场。
    """
    # 占位岗位不参与分配：它是「还没安排的人」待的地方，人只能从里面出去。
    # 把它当成候选岗位的话，匹配会心安理得地把人留在原地，等于什么也没分。
    positions = sorted(shift.real_positions(), key=lambda p: p.sort)
    if not positions:
        raise ValueError("请先在该场次下创建岗位")

    rows = (db.query(Enrollment)
            .filter(Enrollment.shift_id == shift.id,
                    Enrollment.status == "enrolled")
            .order_by(Enrollment.joined_at).all())

    users = {r.id: db.get(User, r.user_id) for r in rows}
    candidates = [(r.id, _eligible_positions(users[r.id], positions))
                  for r in rows]
    held = _held_seats(db, positions)
    quota = {p.id: max(0, p.quota - held[p.id]) for p in positions}

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
            # 排不下不等于出局：他仍然待在原来的岗位（assign 场次就是占位岗位），
            # 管理者可以加岗位、扩名额再跑一次，或者手动指派。
            reason = ("没有满足条件的岗位"
                      if not _eligible_positions(u, positions)
                      else "可去的岗位名额已满")
            unassigned.append({
                "user": {"id": u.id, "name": u.name,
                         "student_id": u.student_id, "college": u.college},
                "reason": reason})
            continue
        plan.append({
            "enrollment_id": r.id,
            "user": {"id": u.id, "name": u.name, "student_id": u.student_id,
                     "college": u.college, "gender": u.gender},
            "joined_at": r.joined_at.isoformat(timespec="minutes"),
            "current_position_id": r.position_id,
            "position_id": target,
            "keep_current": target == r.position_id,
            "leader_willing": r.leader_willing,
            "allow_reassign": r.allow_reassign,
            "continuity": prefer.get(r.id) == target,
        })

    stats = [{
        "position_id": p.id, "name": p.name, "quota": p.quota,
        "held": held[p.id],
        "location": p.location,
        "requirement_desc": p.requirement_desc() or "不限",
        "needs_leader": p.needs_leader,
        "planned": sum(1 for x in plan if x["position_id"] == p.id),
    } for p in positions]

    return {
        "shift": {"id": shift.id, "name": shift.name,
                  "time": shift.span_text(),
                  "capacity": shift.total_capacity()},
        "continuity": continuity,
        "plan": plan,
        "unassigned": unassigned,
        "position_stats": stats,
        "total_enrolled": len(rows),
        "total_assigned": len(plan),
    }


def apply_plan(db: OrmSession, shift: Shift, actor: User, plan: list[dict], *,
               source: str = "assign", notify_out: list | None = None) -> dict:
    """落库分配方案（试算结果可人工微调后提交）。

    plan 行：{"enrollment_id", "position_id"|"", "force": false}
    校验：记录属于本场次、岗位属于本场次、硬性条件、名额。
    force 可以突破名额与条件，单独标记为强制分配并留痕——这正是「强制」的含义。
    """
    from .audit import log as audit_log
    from .enroll_logic import on_leader_vacated, sync_attached

    positions = {p.id: p for p in shift.positions}
    rows = []
    for row in plan:
        enr = db.get(Enrollment, row.get("enrollment_id") or "")
        if enr is None or enr.shift_id != shift.id or enr.status != "enrolled":
            raise ValueError(f"报名记录无效或不在该场次内：{row.get('enrollment_id')}")
        pid = (row.get("position_id") or "").strip()
        if pid and pid not in positions:
            raise ValueError(f"岗位不属于该场次：{pid}")
        rows.append((enr, positions.get(pid), bool(row.get("force"))))

    # 名额：把本次方案与未列入方案的既有分配合并起来算。
    # 扣留的名额先扣掉——它们不释放给任何人，分配也不能用。
    planned = {enr.id for enr, _p, _f in rows}
    held = _held_seats(db, list(positions.values()))
    counts: dict[str, int] = {}
    for enr in (db.query(Enrollment)
                .filter(Enrollment.shift_id == shift.id,
                        Enrollment.status == "enrolled").all()):
        if enr.id not in planned and enr.position_id:
            counts[enr.position_id] = counts.get(enr.position_id, 0) + 1
    for enr, position, force in rows:
        if position is None or force:
            continue
        counts[position.id] = counts.get(position.id, 0) + 1
    for pid, n in counts.items():
        room = positions[pid].quota - held[pid]
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
        if not enr.allow_reassign and old is not None and old is not position:
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
        if position is None:
            continue
        if notify_out is not None:
            notify_out.append({"user_id": enr.user_id,
                               "position_id": position.id,
                               "shift_id": shift.id})

    if not changed:
        summary = "所有人员岗位未发生变化"
    else:
        summary = f"{len(changed)} 人完成岗位分配"
        if forced:
            summary += f"（其中强制分配 {len(forced)} 人）"
        if against_will:
            summary += f"；未接受调剂但被调换：{'、'.join(against_will)}"

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
    db.commit()

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
