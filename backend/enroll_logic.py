"""报名核心逻辑：冲突检测、名额账本、候补流转。

关键机制：
  · 时段冲突：只比场次的时间区间，重叠即拦截（跨活动，含候补中的场次——
    排了队就等于预留了这段时间）；
  · 名额账本：已报名 + 候补转正待确认（确认窗内位子给他留着）+ 临期退选占位
    （seat_held，不释放给任何人）；
  · 候补按 joined_at 排队，正常退选释放名额 → 队首转正，2 小时确认窗口
    （夜间 23:00–8:00 不计时，且不晚于场次开始，见 promote_confirm_deadline）。

调用容量相关函数前必须持有 locks.shift_lock(db, shift_id)，且判定与落库在同一个
事务里（锁随事务结束而放，见 locks.py）。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import NamedTuple

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session as OrmSession

from . import config
from .integrations import msgcenter
from .models import (Activity, CheckinRecord, Enrollment, Shift, Position,
                     User, now, uid)

# 申请中的场次同样占着时段：申请就拦冲突，不等录取
ACTIVE = ("applied", "enrolled", "waitlisted", "pending_confirm")


def overlaps(a1, a2, b1, b2) -> bool:
    """两个时间区间 [start,end) 是否重叠。"""
    return a1 < b2 and b1 < a2


def active_rows(db: OrmSession, user: User) -> list[tuple]:
    """该用户全部有效报名（跨活动），连同场次与活动。

    活动详情页要拿它和本活动的每个场次各比一遍：逐场次去查就是一个场次
    一条 SQL，查一次在内存里比就够了——见 conflicts_among。
    """
    return (db.query(Enrollment, Shift, Activity)
            .join(Shift, Enrollment.shift_id == Shift.id)
            .join(Activity, Enrollment.activity_id == Activity.id)
            .filter(Enrollment.user_id == user.id,
                    Enrollment.status.in_(ACTIVE)).all())


def time_conflicts(db: OrmSession, user: User,
                   target: Shift) -> list[dict]:
    """该用户有效报名中与 target 组时间段重叠的记录（跨活动）。"""
    return conflicts_among(active_rows(db, user), target.id,
                           target.start_time, target.end_time)


def conflicts_among(rows, target_id: str, start: datetime,
                    end: datetime) -> list[dict]:
    """在 active_rows 查回来的报名里挑出与 [start, end) 重叠的（本场次自己除外）。"""
    out = []
    for enr, g, act in rows:
        if enr.shift_id == target_id:
            continue
        if overlaps(g.start_time, g.end_time, start, end):
            out.append({
                "enrollment_id": enr.id, "activity_id": act.id,
                "activity_title": act.title, "shift_name": g.name,
                "time": g.span_text(), "status": enr.status,
                "waiting": enr.status != "enrolled",
            })
    return out


def enroll_deadline(act: Activity, shift: Shift) -> datetime:
    """本场次实际什么时候停止收人：活动报名截止与本场次开场时间里更早的那个。

    活动的报名窗可以跨很多天——支教连着办八周，第三周还想让人报第五周那场，
    很正常。但没有哪一场能在**自己开场之后**还收人：报进去的人签不了到，
    也退不了选（活动已开始一律不许退），最后只会挂在名单上算一个未签到。
    所以截止时间按场次各算各的，活动那个只是总闸。
    """
    if act.reg_end and act.reg_end < shift.start_time:
        return act.reg_end
    return shift.start_time


def applies_to_shift(s: Shift, g: Shift) -> bool:
    """培训/彩排 s 叫不叫服务场次 g 的人：全体，或者圈到了 g 的某个岗位。"""
    return (not s.applies_to_positions
            or any(p.id in s.applies_to_positions for p in g.positions))


def attached_shifts(db: OrmSession, shift_id: str,
                    position_id: str | None = None) -> list[Shift]:
    """对某服务场次生效的培训/彩排；给了岗位就只留对该岗位生效的。"""
    g = db.get(Shift, shift_id)
    rows = (db.query(Shift)
            .filter(Shift.activity_id == g.activity_id, Shift.kind != "service")
            .order_by(Shift.start_time).all())
    return for_position([s for s in rows if applies_to_shift(s, g)], position_id)


def for_position(attached: list[Shift], position_id: str | None) -> list[Shift]:
    """在 attached_shifts(db, shift_id) 查回来的那批里挑出对这个岗位生效的，不再查库。"""
    if position_id is None:
        return attached
    return [s for s in attached
            if not s.applies_to_positions
            or position_id in s.applies_to_positions]


def sync_attached(db: OrmSession, enr: Enrollment,
                  attached: list[Shift] | None = None) -> None:
    """主报名有变化之后，同步这个人的培训/彩排报名。

    一个入口管所有变化——报名、候补转正、退选、改岗——省得每处各写一遍
    然后漏掉一处。改岗尤其要紧：东门组周五彩排，人被调去检录组之后，
    那场彩排就不该再叫他。
    一场培训可能管好几个场次的人，所以看的是他在本活动**全部**正式报名的
    岗位：任一个被圈到就该去，一个都不剩才退。
    已经签到过的不删：人确实来了，记录不能凭空消失。

    attached：调用方已经查过的 attached_shifts(db, enr.shift_id)（不按岗位
    过滤）。报名在锁外为比时段查过一遍，锁里直接用；这一场的变化也只可能
    影响这几场培训。一场培训都没有时什么也不查。
    """
    if attached is None:
        attached = attached_shifts(db, enr.shift_id)
    if not attached:
        return
    # 会话不自动 flush：enr 自己的最新状态单独算，库里只取他别的报名
    others = (db.query(Enrollment.position_id)
              .join(Shift, Enrollment.shift_id == Shift.id)
              .filter(Enrollment.user_id == enr.user_id,
                      Enrollment.activity_id == enr.activity_id,
                      Enrollment.id != enr.id,
                      Enrollment.status == "enrolled",
                      Shift.kind == "service").all())
    pids = {pid for (pid,) in others}
    if enr.status == "enrolled":
        pids.add(enr.position_id)
    enrolled_any = bool(pids)
    pids.discard(None)
    for s in attached:
        want = enrolled_any and (not s.applies_to_positions
                                 or bool(pids & set(s.applies_to_positions)))
        row = (db.query(Enrollment)
               .filter(Enrollment.shift_id == s.id,
                       Enrollment.user_id == enr.user_id).first())
        if want and row is None:
            db.add(Enrollment(id=uid("enr"), activity_id=enr.activity_id,
                              shift_id=s.id, user_id=enr.user_id,
                              position_id=None, status="enrolled",
                              joined_at=now()))
        elif not want and row is not None:
            been = db.query(CheckinRecord).filter(
                CheckinRecord.shift_id == s.id,
                CheckinRecord.user_id == enr.user_id).count()
            if not been:
                db.delete(row)


def conflict_message(clashes: list[dict]) -> str:
    parts = []
    for c in clashes[:3]:
        tail = "（候补中）" if c["waiting"] else ""
        parts.append(f"「{c['activity_title']}」{c['shift_name']}"
                     f"（{c['time']}）{tail}")
    return "；".join(parts)


# ======================= 名额账本 =======================

def shift_enrolled_count(db: OrmSession, shift_id: str) -> int:
    """正式报名人数（用于展示「已报名 N 人」）。"""
    return db.query(Enrollment).filter(
        Enrollment.shift_id == shift_id,
        Enrollment.status == "enrolled").count()


def shift_occupied_count(db: OrmSession, shift_id: str) -> int:
    """场次已消耗的名额 = 正式报名 + 待确认 + 临期退选占位。"""
    return db.query(Enrollment).filter(
        Enrollment.shift_id == shift_id,
        or_(Enrollment.status.in_(("enrolled", "pending_confirm")),
            Enrollment.seat_held.is_(True))).count()


def position_enrolled_count(db: OrmSession, position_id: str) -> int:
    return db.query(Enrollment).filter(
        Enrollment.position_id == position_id,
        Enrollment.status == "enrolled").count()


def position_occupied_count(db: OrmSession, position_id: str) -> int:
    """岗位已消耗的名额。

    待确认（候补转正的确认窗内）也算：位子是给他留的，这两小时里别人不能占，
    否则他确认回来时名额已经没了。超时未确认会转成 cancelled，位子自然放开。
    """
    return db.query(Enrollment).filter(
        Enrollment.position_id == position_id,
        or_(Enrollment.status.in_(("enrolled", "pending_confirm")),
            Enrollment.seat_held.is_(True))).count()


def position_held_count(db: OrmSession, position_id: str) -> int:
    """岗位上被临期退选扣留的名额数。"""
    return db.query(Enrollment).filter(
        Enrollment.position_id == position_id,
        Enrollment.seat_held.is_(True)).count()


class Occupancy:
    """一个活动里各场次/岗位的人数，**一次查完**。

    展示页要列十几个岗位的「已报 / 占用 / 占位 / 候补」，一个岗位一条 count
    就是几十条 SQL（实测：一个活动详情页 80 条，大头在这儿）。这里按
    (场次, 岗位, 状态, 是否占位) 分组取回来，之后都在内存里加。

    只给展示路径用；报名、抢名额那些地方仍走单条 count——那里要的是此刻
    这一刻的准确值，缓存下来反而危险。
    """

    def __init__(self, db: OrmSession, activity_id: str):
        n = lambda: defaultdict(int)                       # noqa: E731
        self._shift = {"enrolled": n(), "occupied": n(), "wait": n(),
                       "excused": n(), "unplaced": n()}
        self._pos = {"enrolled": n(), "occupied": n(), "held": n(), "wait": n()}
        rows = (db.query(Enrollment.shift_id, Enrollment.position_id,
                         Enrollment.status, Enrollment.seat_held,
                         Enrollment.excused)
                .filter(Enrollment.activity_id == activity_id).all())
        for sid, pid, status, held, excused in rows:
            enr = status == "enrolled"
            occ = held or status in ("enrolled", "pending_confirm")
            wait = status in ("waitlisted", "pending_confirm")
            if enr:
                self._shift["enrolled"][sid] += 1
                if pid:
                    self._pos["enrolled"][pid] += 1
                else:
                    self._shift["unplaced"][sid] += 1
                if excused:
                    self._shift["excused"][sid] += 1
            if occ:
                self._shift["occupied"][sid] += 1
                if pid:
                    self._pos["occupied"][pid] += 1
            if wait:
                self._shift["wait"][sid] += 1
                if pid:
                    self._pos["wait"][pid] += 1
            if held and pid:
                self._pos["held"][pid] += 1

    def shift_enrolled(self, shift_id: str) -> int:
        return self._shift["enrolled"][shift_id]

    def shift_occupied(self, shift_id: str) -> int:
        return self._shift["occupied"][shift_id]

    def shift_wait(self, shift_id: str) -> int:
        """候补队列长度（waitlisted + pending_confirm），同 waitlist_rows。"""
        return self._shift["wait"][shift_id]

    def shift_excused(self, shift_id: str) -> int:
        """已报名但请了假的人数（培训/彩排）。"""
        return self._shift["excused"][shift_id]

    def shift_unplaced(self, shift_id: str) -> int:
        """已报名却没有岗位的人数（岗位被删或管理员补录留下的）。"""
        return self._shift["unplaced"][shift_id]

    def position_enrolled(self, position_id: str) -> int:
        return self._pos["enrolled"][position_id]

    def position_occupied(self, position_id: str) -> int:
        return self._pos["occupied"][position_id]

    def position_held(self, position_id: str) -> int:
        return self._pos["held"][position_id]

    def position_wait(self, position_id: str) -> int:
        return self._pos["wait"][position_id]


def shift_counts(db: OrmSession,
                 activity_ids: list[str]) -> dict[str, tuple[int, int, int]]:
    """一批活动里每个场次的（正式报名, 已消耗名额, 候补）三个数，一条分组查询。

    广场一页十几个活动，逐个活动逐个场次 count 的话，SQL 条数跟着活动数涨
    （实测一页 39 条）。口径与 shift_enrolled_count / shift_occupied_count /
    waitlist_rows 相同。
    """
    if not activity_ids:
        return {}
    enrolled = Enrollment.status == "enrolled"
    occupied = or_(Enrollment.status.in_(("enrolled", "pending_confirm")),
                   Enrollment.seat_held.is_(True))
    waiting = Enrollment.status.in_(("waitlisted", "pending_confirm"))
    rows = (db.query(Enrollment.shift_id,
                     func.count(case((enrolled, 1))),
                     func.count(case((occupied, 1))),
                     func.count(case((waiting, 1))))
            .filter(Enrollment.activity_id.in_(activity_ids))
            .group_by(Enrollment.shift_id).all())
    return {sid: (e, o, w) for sid, e, o, w in rows}


def has_room(db: OrmSession, shift: Shift, position=None) -> bool:
    """还有没有正式名额。名额只记在岗位上，场次不另存一份。

    assign 模式下报名时看似「没有岗位可选」，其实落在占位岗位上——
    调用方（报名接口）负责把它解析成 shift.placeholder()。占位岗位按整场
    数人：分到真实岗位的人还在这一场里，只数占位岗位的话，人一挪出去
    就像空出了位子，会再招进来一批。
    """
    if position is None:
        return False
    if position.is_placeholder:
        return shift_occupied_count(db, shift.id) < position.quota
    return position_occupied_count(db, position.id) < position.quota


class EnrollLedger(NamedTuple):
    """报名锁里要的全部账目，见 enroll_ledger。"""
    mine: str | None     # 本人在这一场的有效记录的状态（没有为 None）
    mine_held: bool      # 本人在这一场有一条临期退选占位
    occupied: int        # 岗位已消耗的名额，口径同 position_occupied_count
    queued: int          # 岗位候补队列人数，口径同 waitlist_rows 按岗位过滤
    editing: bool        # 活动此刻是否处于编辑态
    clash: bool          # 本人已有的有效报名与 targets 里某一场时段重叠


def enroll_ledger(db: OrmSession, activity_id: str, shift_id: str,
                  position_id: str, user_id: str,
                  targets: list[Shift], *, whole_shift: bool = False) -> EnrollLedger:
    """报名锁里的「查重 + 数名额 + 数候补 + 看编辑态 + 有没有时段冲突」，**一条 SQL**。

    分开查要四五条：查重一条、has_room 一条、没位子时把整场候补行全捞回来
    在内存里按岗位筛一遍。锁里每多一个往返，排在后面的几百人就多等一个
    往返，所以合成一次：只扫「本人这一场」和「这个岗位」两小撮行，用带
    FILTER 的聚合各数各的。

    口径与下列函数逐条对齐（改其中一边要同时改另一边）：
      · 查重：status ∈ ACTIVE 或 seat_held（唯一索引 uq_enrollment_live 的口径，
        所以最多一行，max/bool_or 取到的就是那一行）；
      · 名额：position_occupied_count——正式 + 待确认 + 临期退选占位；
        whole_shift（报的是占位岗位）时按整场数，同 has_room；
      · 候补：waitlist_rows——waitlisted + pending_confirm，限本场次本岗位。
        待确认既占名额又在队列里，两边都算；
      · 冲突：time_conflicts——本人 ACTIVE 的报名，场次与 targets 里任一场
        （主场次和它的培训/彩排）区间重叠，比某一场时不算那一场自己。
        这里只要「有没有」；真有的话调用方再走 active_rows 拼出详细的提示，
        那是少见的路径，常见路径省下一个往返。
    """
    live = or_(Enrollment.status.in_(ACTIVE), Enrollment.seat_held.is_(True))
    mine = (Enrollment.shift_id == shift_id) & (Enrollment.user_id == user_id)
    at_pos = Enrollment.position_id == position_id
    seats = (Enrollment.shift_id == shift_id) if whole_shift else at_pos
    editing = (select(Activity.editing_since.isnot(None))
               .where(Activity.id == activity_id).scalar_subquery())
    clash = (select(Enrollment.id)
             .join(Shift, Enrollment.shift_id == Shift.id)
             .where(Enrollment.user_id == user_id,
                    Enrollment.status.in_(ACTIVE),
                    or_(*[(Enrollment.shift_id != t.id)
                          & (Shift.start_time < t.end_time)
                          & (Shift.end_time > t.start_time)
                          for t in targets]))
             .exists())
    row = db.execute(
        select(func.max(Enrollment.status).filter(mine & live),
               func.coalesce(func.bool_or(mine & Enrollment.seat_held), False),
               func.count().filter(seats & or_(
                   Enrollment.status.in_(("enrolled", "pending_confirm")),
                   Enrollment.seat_held.is_(True))),
               func.count().filter(
                   at_pos & (Enrollment.shift_id == shift_id)
                   & Enrollment.status.in_(("waitlisted", "pending_confirm"))),
               editing, clash)
        .where(or_(mine, seats))).one()
    return EnrollLedger(row[0], bool(row[1]), row[2], row[3], bool(row[4]),
                        bool(row[5]))


def paused_reason(shift: Shift, position: Position | None) -> str:
    """这一场 / 这个岗位是不是被管理者暂停了报名；没暂停返回空串。"""
    if shift.enroll_paused:
        return f"场次「{shift.name}」暂停报名"
    if position is not None and position.enroll_paused:
        return f"岗位「{position.name}」暂停报名"
    return ""


# ======================= 硬性条件（场次与岗位共用） =======================

def profile_fields(user: User) -> dict[str, str]:
    """条件标签 → 志愿者档案里对应的值。场次、岗位、招募范围都按这张表匹配。"""
    return {"性别": user.gender or "", "学院": user.college or "",
            "年级": str(user.grade or ""),
            "校区": user.campus or "", "学段": user.stage or ""}


def unmet_for_tags(user: User, tags: dict | None) -> list[str]:
    """志愿者不满足 {标签: 值或值列表} 里的哪几条。空 = 都满足。"""
    fields = profile_fields(user)
    unmet = []
    for tag, value in (tags or {}).items():
        mine = fields.get(tag)
        if mine is None:      # 未知标签不拦人，否则一个错别字能把所有人挡在外面
            continue
        allowed = value if isinstance(value, list) else [value]
        allowed = [str(v) for v in allowed if str(v).strip()]
        if allowed and mine not in allowed:
            unmet.append(f"{tag}需为 {'/'.join(allowed)}（您是 {mine or '未填写'}）")
    return unmet


def unmet_requirements(user: User, position: Position) -> list[str]:
    """志愿者不满足该岗位的哪些硬性条件。空 = 可以报。

    报名时直接拦截，一键分配时同样作为硬约束。
    """
    return unmet_for_tags(user, position.requirement_tags)


def unmet_shift_requirements(user: User, shift) -> list[str]:
    """场次级的硬性条件。assign 模式下报名时还没有岗位，只能靠这一层筛人。"""
    return unmet_for_tags(user, shift.requirement_tags)


def recruit_scope_tags(campuses, stages, colleges) -> dict:
    """招募范围 → 条件标签。哪一项空就是那一项不限。

    活动详情、广场列表（只取了这几列，没有 Activity 对象）都走这里，
    免得加一个维度时漏改一处——漏了就是广场上看得见、点进去被拦。
    """
    tags = {}
    if campuses:
        tags["校区"] = campuses
    if stages:
        tags["学段"] = stages
    if colleges:
        tags["学院"] = colleges
    return tags


def unmet_recruit_scope(user: User, act) -> list[str]:
    """招募范围：活动一刀切限定校区/学段/学院，空 = 不限。"""
    return unmet_for_tags(user, recruit_scope_tags(
        act.recruit_campuses, act.recruit_stages, act.recruit_colleges))


# ======================= 组长 =======================

def group_positions(db: OrmSession, position: Position) -> list[Position]:
    """与该岗位同组的全部岗位（含自己）。

    组名相同即同组；没填组名的岗位自成一组。分组是为了让相邻岗位共用一名
    组长——「东门片区」的引导、检录、医疗点现场本来就是一个人在协调。
    """
    if not (position.group_name or "").strip():
        return [position]
    return (db.query(Position)
            .filter(Position.shift_id == position.shift_id,
                    Position.group_name == position.group_name)
            .order_by(Position.sort).all())


def group_needs_leader(positions: list[Position]) -> bool:
    """组里只要有一个岗位标了需要组长，这个组就要有组长。"""
    return any(p.needs_leader for p in positions)


def notify_leader_vacancy(db: OrmSession, position: Position) -> bool:
    """本组需要组长却没有了，提醒管理者去指定。返回是否真的空缺。

    组长**只由管理者指定**——组长要协调现场，谁合适只有管理者知道。
    报名时的「我愿意担任组长」是给管理者看的参考，不决定谁当选。
    """
    from .notify import notify_managers

    positions = group_positions(db, position)
    if not group_needs_leader(positions):
        return False
    if group_leader(db, position) is not None:
        return False
    activity = db.get(Activity, position.shift.activity_id)
    scope = leader_scope_label(positions, position)
    notify_managers(db, activity.id, f"[组长空缺] {activity.title}",
                    f"「{position.shift.name}·{scope}」目前没有组长，"
                    f"请在「志愿者管理」中指定。", duty="roster", tab="roster")
    return True


def leader_scope_label(positions: list[Position], own: Position) -> str:
    """通知里怎么称呼组长的管辖范围。"""
    if len(positions) <= 1:
        return own.name
    names = "、".join(p.name for p in positions)
    return f"{positions[0].group_name}（{names}）"


def group_leader(db: OrmSession, position: Position) -> Enrollment | None:
    ids = [p.id for p in group_positions(db, position)]
    return (db.query(Enrollment)
            .filter(Enrollment.position_id.in_(ids),
                    Enrollment.is_leader.is_(True),
                    Enrollment.status == "enrolled").first())


def on_leader_vacated(db: OrmSession, position_id: str | None) -> None:
    """组长退选、被调走、被取消之后调一次：空缺了就提醒管理者，不自动补。"""
    if not position_id:
        return
    position = db.get(Position, position_id)
    if position is not None:
        notify_leader_vacancy(db, position)


# ======================= 人员变动流水 =======================

def record_change(db: OrmSession, user: User, enr: Enrollment, action: str,
                  detail: str = "") -> None:
    """记一条人员变动。

    审计要是只记管理者操作，学生自己报名、退选就不留痕：活动前一天少了三个人，
    老师可能到现场才发现。这里用同一套审计设施记学生自己的动作，
    actor 就是学生本人；不对外公开（其他志愿者不该看到谁退了）。
    """
    from .audit import log as audit_log

    shift = db.get(Shift, enr.shift_id)
    position = db.get(Position, enr.position_id) if enr.position_id else None
    where = f"{shift.name}·{position.name}" if position else shift.name
    audit_log(db, actor=user, action=action, target_type="enrollment",
              target_id=enr.id, target_label=user.name,
              activity_id=enr.activity_id,
              summary=f"{user.name}（{user.student_id}）{ROSTER_ACTIONS[action]}"
                      f"「{where}」" + (f"：{detail}" if detail else ""))


ROSTER_ACTIONS = {
    "enroll": "报名", "waitlist": "加入候补", "cancel": "退选",
    "quit_queue": "退出候补", "abandon": "放弃候补转正",
    "promote": "候补转正",
    "excuse": "请假", "unexcuse": "销假",
}


# ======================= 候补队列 =======================

def waitlist_rows(db: OrmSession, shift_id: str) -> list[Enrollment]:
    """候补队列：waitlisted + pending_confirm，按排队时间先后。"""
    return (db.query(Enrollment)
            .filter(Enrollment.shift_id == shift_id,
                    Enrollment.status.in_(("waitlisted", "pending_confirm")))
            .order_by(Enrollment.joined_at).all())


def position_waitlist_count(db: OrmSession, shift_id: str,
                            position_id: str) -> int:
    """岗位候补队列的人数：waitlist_rows 按岗位筛出来的条数，只数不取行。"""
    return db.query(Enrollment).filter(
        Enrollment.shift_id == shift_id,
        Enrollment.position_id == position_id,
        Enrollment.status.in_(("waitlisted", "pending_confirm"))).count()


def waitlist_position(db: OrmSession, enr: Enrollment) -> int:
    """排在第几位。

    pick 模式的队列是按岗位分的，只能和同岗位的人比——按整个场次排名的话，
    排在「引导岗」第一位的人会因为「检录岗」前面有两个人而被告知「第 3 位」，
    白白以为自己没希望。
    """
    rows = waitlist_rows(db, enr.shift_id)
    if enr.position_id:
        rows = [r for r in rows if r.position_id == enr.position_id]
    for i, row in enumerate(rows):
        if row.id == enr.id:
            return i + 1
    return 0


def auto_promote_deadline(db: OrmSession, shift: Shift) -> datetime:
    """自动候补转正的截止点：报名截止时间与「活动开始前 24 小时」取更晚的那个。

    过了这个点还自动转正是坑人：转正的人只剩不到一天准备，而他此时再退选
    就要按临期退选扣分。之后要不要补人由负责人看着办，手动转。
    """
    act = db.get(Activity, shift.activity_id)
    cutoff = shift.start_time - timedelta(hours=config.LATE_CANCEL_HOURS)
    if act is not None and act.reg_end and act.reg_end > cutoff:
        cutoff = act.reg_end
    # 再晚也不能晚于活动开始——报名截止时间填到活动之后时兜住
    return min(cutoff, shift.start_time)


def promote_head(db: OrmSession, shift: Shift,
                 position: Position | None = None) -> Enrollment | None:
    """有名额释放时，把候补队首转为「待确认」，开确认窗（2 小时，夜间不计时）并通知。

    只在确实空出名额时才提升：临期退选是占位不释放的，队列原地不动。
    pick 模式下队列是按岗位分的，只提升该岗位的队首。
    过了自动转正截止点就不再自动提升，改由管理者手动转。
    """
    if now() > auto_promote_deadline(db, shift):
        return None
    if not has_room(db, shift, position):
        return None
    rows = waitlist_rows(db, shift.id)
    if position is not None:
        rows = [r for r in rows if r.position_id == position.id]
    head = next((r for r in rows if r.status == "waitlisted"), None)
    if head is None:
        return None
    return start_confirm(db, head, shift)


def confirm_deadline_from(start: datetime) -> datetime:
    """从 start 起只累计白天（PROMOTE_QUIET_END 点到 PROMOTE_QUIET_START 点）的时间，
    累计满 PROMOTE_CONFIRM_HOURS 小时的那一刻就是截止。纯函数，不看场次。

    例（2 小时，夜间 23:00–8:00）：14:00 → 16:00；22:30 → 次日 9:30；
    1:00 → 10:00；7:00 → 10:00；恰好 23:00 → 次日 10:00；21:00 → 23:00
    （刚好在夜间开始那一刻用满，不再拖到第二天）。
    """
    remaining = timedelta(hours=config.PROMOTE_CONFIRM_HOURS)
    t = start
    while True:
        if t.hour >= config.PROMOTE_QUIET_START:        # 深夜：跳到次日早上
            t = (t + timedelta(days=1)).replace(
                hour=config.PROMOTE_QUIET_END, minute=0, second=0, microsecond=0)
        elif t.hour < config.PROMOTE_QUIET_END:         # 凌晨：跳到当天早上
            t = t.replace(hour=config.PROMOTE_QUIET_END, minute=0, second=0,
                          microsecond=0)
        day_end = t.replace(hour=config.PROMOTE_QUIET_START, minute=0, second=0,
                            microsecond=0)
        if t + remaining <= day_end:
            return t + remaining
        remaining -= day_end - t
        t = day_end


def promote_confirm_deadline(start: datetime, shift: Shift) -> datetime:
    """转正确认窗的截止时间：夜间不计时（confirm_deadline_from），再按场次封顶。

    封顶规则：
      · 场次还没开始 → 截止不晚于场次开始。手动转正可能贴着开场（比如开场前
        1 小时、或前一晚 23 点后转正），过了开场还在等确认没有意义——人要么
        来了，要么位子该让负责人另想办法。窗口因此可能短于 2 小时，这是负责人
        选择贴近开场转正的代价，通知里写的是封顶后的时间，本人看得到。
      · 场次已经开始（活动进行中手动补人）→ 不晚于场次结束；
      · 场次也已结束 → 不封顶，照常给满窗口（封到过去就等于一转正就超时，
        白白记本人一次放弃）。
    """
    deadline = confirm_deadline_from(start)
    if shift.start_time > start:
        return min(deadline, shift.start_time)
    if shift.end_time and shift.end_time > start:
        return min(deadline, shift.end_time)
    return deadline


def start_confirm(db: OrmSession, enr: Enrollment, shift: Shift) -> Enrollment:
    """把一条候补转为「待确认」，开确认窗并通知本人。

    名额从这一刻起就占住了：本人在窗口内确认才成为正式报名，超时按放弃处理、
    名额顺延下一位。自动递补与管理者手动转正走的是同一条路。
    """
    t = now()
    enr.status = "pending_confirm"
    enr.promoted_at = t
    enr.confirm_deadline = promote_confirm_deadline(t, shift)
    user = db.get(User, enr.user_id)
    activity = db.get(Activity, enr.activity_id)
    # 带上 activity_id：这条通知看完就要去活动页确认，消息卡片据此可点击跳转
    msgcenter.send_user(db, user, "waitlist_promoted",
                    data={"activity_id": activity.id},
                    activity=activity.title, shift=shift.name,
                    deadline=f"{enr.confirm_deadline:%m-%d %H:%M}")
    return enr


def _settle_position(db: OrmSession, enr: Enrollment, user: User,
                     shift: Shift) -> None:
    """转正后落实岗位并通知：assign 模式候补时没选岗位，这里挑一个。"""
    if not enr.position_id and shift.positions:
        ok = [p for p in shift.positions if not unmet_requirements(user, p)]
        if ok:
            best = max(ok, key=lambda p: p.quota - position_occupied_count(db, p.id))
            enr.position_id = best.id
            enr.assigned_at = now()
            enr.assign_forced = False
    if enr.position_id:
        position = db.get(Position, enr.position_id)
        activity = db.get(Activity, enr.activity_id)
        msgcenter.send_user(db, user, "assignment", activity=activity.title,
                        shift=shift.name, position=position.name,
                        position_time=position.time_text(),
                        position_loc=position.location or "待定")
    sync_attached(db, enr)


def confirm_promotion(db: OrmSession, enr: Enrollment, user: User) -> Enrollment:
    """确认候补转正：成为正式报名。

    有岗位的场次自动挑剩余名额最多的岗位——岗位之间互斥且无独立时间，
    不需要再做时段过滤。
    """
    shift = db.get(Shift, enr.shift_id)
    enr.status = "enrolled"
    enr.confirm_deadline = None
    _settle_position(db, enr, user, shift)
    return enr


def expire_stale_promotions(db: OrmSession) -> int:
    """扫掉超时未确认的转正：记放弃 → 计数 → 顺延下一位。

    由 main.py 的后台协程每分钟调用；学生打开「我的报名」时也会顺手触发一次，
    让页面上看到的状态和实际一致。
    """
    from .locks import shift_lock

    expired = (db.query(Enrollment)
               .filter(Enrollment.status == "pending_confirm",
                       Enrollment.confirm_deadline < now()).all())
    if not expired:
        return 0
    by_shift: dict[str, list[str]] = defaultdict(list)
    for enr in expired:
        by_shift[enr.shift_id].append(enr.id)
    db.commit()      # 读完先还连接，别占着连接排锁（理由同报名）

    # 一个场次一个事务：放弃与顺延一起提交。场次锁随事务结束而放，要是
    # 「先提交放弃、再另拿一次锁递补」，中间那一瞬，别人能看到空出来的位子
    # 抢在队首前面报上；合成一个事务就没有这个窗口
    n = 0
    for sid, ids in by_shift.items():
        with shift_lock(db, sid):
            hit = 0
            for enr_id in ids:
                fresh = db.get(Enrollment, enr_id)
                db.refresh(fresh)
                if (fresh.status != "pending_confirm"
                        or fresh.confirm_deadline >= now()):
                    continue      # 用户在这一瞬间点了确认
                fresh.status = "cancelled"
                fresh.cancel_type = "expired"
                fresh.cancelled_at = now()
                hit += 1
            if hit:
                db.flush()        # 递补要按落库后的人数算（会话不自动 flush）
                promote_all(db, db.get(Shift, sid))
            db.commit()
            n += hit
    return n


def promote_all(db: OrmSession, shift: Shift) -> int:
    """把该场次所有空出名额的队列各提升一位。

    pick 模式下队列按岗位分，得逐个岗位看；assign 模式只有一条队。
    过了自动转正截止点整个不动（见 auto_promote_deadline）。
    """
    if shift.needs_assign():
        # 队列排在占位岗位上，名额按整场数（见 has_room）
        return 1 if promote_head(db, shift, shift.placeholder()) else 0
    n = 0
    for position in shift.positions:
        if promote_head(db, shift, position):
            n += 1
    return n


def fill_vacancies(db: OrmSession, shift: Shift) -> int:
    """把空着的名额一次填满。

    退选一次只空一个位，promote_all 提一位就够；扩容一次可能空出好几个，
    得一直提到没名额或没人排队。待确认也占名额，所以循环必然收敛。
    """
    total = 0
    while True:
        n = promote_all(db, shift)
        if not n:
            return total
        total += n
