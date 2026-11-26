"""普通岗位各自带一个场次：拆开挂了多个岗位的服务场次

岗位自带时段之后，kind=service、mode=pick 的场次恰好装一个岗位：场次名与岗位名
一致，场次的时段就是这个岗位的时段。库里挂了两个以上岗位的这种场次，第一个岗位
留在原场次，其余每个岗位各建一个新场次——复制原场次的全部字段（含暂停、锁定、
自动锁定用掉没用掉、组长空缺提醒发没发、日报发到哪天）与全部时段，再把挂在被
移走岗位上的报名、意向、签到码、签到记录、时长明细一起搬过去。

顺带把这类场次的条件与地点并进岗位：普通岗位的条件和地点只看岗位那一份
（场次级条件只有「截止后统一分配」用得上，报名时还没有岗位可选）。

Revision ID: 0024
Revises: 0023
Create Date: 2026-11-26
"""
from __future__ import annotations

import json
import secrets
import string
import uuid

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

# 新场次照抄原场次的这些列：id 与 name 另给（name = 岗位名）
SHIFT_COLUMNS = ("activity_id", "location", "mode", "kind",
                 "applies_to_positions", "hours_mode", "requirement_tags",
                 "note", "enroll_paused", "lock_hours", "list_locked",
                 "auto_lock_spent", "leader_alert_sent", "sort",
                 "flexible_time", "digest_sent_on")

# 补一条「申请中」的意向记录时照抄的列：id、shift_id、position_id 另给
INTENT_COLUMNS = ("activity_id", "user_id", "status", "cancel_type",
                  "allow_reassign", "leader_willing", "answers", "is_leader",
                  "assign_forced", "excused", "excuse_reason", "source",
                  "joined_at")

# 复制整场统一码时照抄的列：id、shift_id、period_id、token、code_group 另给
TASK_COLUMNS = ("activity_id", "position_id", "mode", "valid_from", "valid_to",
                "active", "created_by", "created_at")


def _uid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _new_token(used: set[str]) -> str:
    """固定签到码：6 位大写字母数字（同 models.new_checkin_token）。

    token 是唯一列，所以避开库里已经有的那些；复制出来的码是另一张纸，
    不能和原来那张同号。
    """
    while True:
        token = "".join(secrets.choice(string.ascii_uppercase + string.digits)
                        for _ in range(6))
        if token not in used:
            used.add(token)
            return token


def _pick_shifts(conn) -> dict[str, list[dict]]:
    """每个 service/pick 场次下的岗位，按场次归拢（占位岗位也算，理论上不该有）。"""
    rows = conn.execute(sa.text(
        "SELECT p.id, p.shift_id, p.name, p.location, p.requirement_tags "
        "FROM positions p JOIN shifts s ON s.id = p.shift_id "
        "WHERE s.kind = 'service' AND s.mode = 'pick' "
        "ORDER BY p.shift_id, p.sort, p.id")).mappings().all()
    out: dict[str, list[dict]] = {}
    for row in rows:
        out.setdefault(row["shift_id"], []).append(dict(row))
    return out


def _copy_periods(conn, old_shift: str, new_shift: str) -> dict[str, str]:
    """把原场次的时段整套复制给新场次，返回 {原时段 id: 新时段 id}。

    签到提醒发没发也跟着复制：拆场次不该让同一段服务再提醒一遍。
    """
    rows = conn.execute(sa.text(
        "SELECT id, start_time, end_time, sort, reminder_sent_at "
        "FROM shift_periods WHERE shift_id = :old ORDER BY start_time, sort"),
        {"old": old_shift}).mappings().all()
    mapping = {}
    for row in rows:
        new_id = _uid("sp")
        mapping[row["id"]] = new_id
        conn.execute(sa.text(
            "INSERT INTO shift_periods "
            "(id, shift_id, start_time, end_time, sort, reminder_sent_at) "
            "VALUES (:id, :shift_id, :start_time, :end_time, :sort, :sent)"),
            {"id": new_id, "shift_id": new_shift,
             "start_time": row["start_time"], "end_time": row["end_time"],
             "sort": row["sort"], "sent": row["reminder_sent_at"]})
    return mapping


def _move_by_period(conn, table: str, new_shift: str, periods: dict[str, str],
                    where: str, params: dict) -> None:
    """把一张按（场次, 时段）记事的表上的行搬到新场次：时段按起止时间对过去。

    period_id 为空的行只改 shift_id——它本来就没落到具体某一段上。
    """
    for old_period, new_period in periods.items():
        conn.execute(sa.text(
            f"UPDATE {table} SET shift_id = :new, period_id = :np "
            f"WHERE {where} AND period_id = :op"),
            {**params, "new": new_shift, "np": new_period, "op": old_period})
    conn.execute(sa.text(
        f"UPDATE {table} SET shift_id = :new "
        f"WHERE {where} AND period_id IS NULL"),
        {**params, "new": new_shift})


def _split_shift(conn, old_shift: str, positions: list[dict],
                 tokens: set[str]) -> None:
    """一个挂了多个岗位的服务场次：第一个岗位留下，其余各带一个新场次走。"""
    keep, moving = positions[0], positions[1:]

    # 申请中的意向：先拍一份快照。下面逐个岗位搬动之后，原场次上就查不全了
    intents = conn.execute(sa.text(
        "SELECT * FROM enrollments "
        "WHERE shift_id = :old AND status = 'applied'"),
        {"old": old_shift}).mappings().all()
    wanted = {
        (row["activity_id"], row["user_id"]): list(row["wanted_positions"] or [])
        for row in conn.execute(sa.text(
            "SELECT activity_id, user_id, wanted_positions FROM applications"
        )).mappings().all()}

    # 整场统一码：原行先补上 code_group，复制出来的几行跟它共用——对外仍是一张码
    whole = conn.execute(sa.text(
        "SELECT * FROM checkin_tasks WHERE shift_id = :old AND mode = 'shift' "
        "AND position_id IS NULL"), {"old": old_shift}).mappings().all()
    groups = {}
    for task in whole:
        group = task["code_group"] or _uid("cg")
        groups[task["id"]] = group
        if not task["code_group"]:
            conn.execute(sa.text(
                "UPDATE checkin_tasks SET code_group = :g WHERE id = :id"),
                {"g": group, "id": task["id"]})

    # 场次上的学习材料（挂岗位的跟着岗位走，不用动）
    materials = conn.execute(sa.text(
        "SELECT * FROM attachments WHERE owner_type = 'shift' "
        "AND owner_id = :old"), {"old": old_shift}).mappings().all()

    for position in moving:
        new_shift = _uid("g")
        cols = ", ".join(SHIFT_COLUMNS)
        conn.execute(sa.text(
            f"INSERT INTO shifts (id, name, {cols}) "
            f"SELECT :new, :name, {cols} FROM shifts WHERE id = :old"),
            {"new": new_shift, "name": position["name"][:128],
             "old": old_shift})
        periods = _copy_periods(conn, old_shift, new_shift)

        # 岗位本身：新场次下只有它一个
        conn.execute(sa.text(
            "UPDATE positions SET shift_id = :new, sort = 0 WHERE id = :pid"),
            {"new": new_shift, "pid": position["id"]})
        # 报名记录（含已退选、未录取的历史）跟着岗位走
        conn.execute(sa.text(
            "UPDATE enrollments SET shift_id = :new WHERE position_id = :pid"),
            {"new": new_shift, "pid": position["id"]})
        # 意向：勾了这个岗位的、以及没写岗位（整场都行）的，在新场次补一条。
        # 岗位就写在意向记录上的那一条已经随岗位搬过去了，不再补
        for row in intents:
            if row["position_id"] == position["id"]:
                continue
            key = (row["activity_id"], row["user_id"])
            if row["position_id"] is not None \
                    and position["id"] not in wanted.get(key, []):
                continue
            cols = ", ".join(INTENT_COLUMNS)
            conn.execute(sa.text(
                f"INSERT INTO enrollments (id, shift_id, position_id, {cols}) "
                f"SELECT :id, :shift, :pid, {cols} FROM enrollments "
                f"WHERE id = :src"),
                {"id": _uid("enr"), "shift": new_shift, "pid": position["id"],
                 "src": row["id"]})

        # 分岗位的签到码跟着岗位走；整场统一码给新场次各复制一行
        _move_by_period(conn, "checkin_tasks", new_shift, periods,
                        "shift_id = :old AND position_id = :pid",
                        {"old": old_shift, "pid": position["id"]})
        for task in whole:
            new_task = _uid("tsk")
            cols = ", ".join(TASK_COLUMNS)
            conn.execute(sa.text(
                f"INSERT INTO checkin_tasks "
                f"(id, shift_id, period_id, token, code_group, {cols}) "
                f"SELECT :id, :shift, :period, :token, :group, {cols} "
                f"FROM checkin_tasks WHERE id = :src"),
                {"id": new_task, "shift": new_shift,
                 "period": periods.get(task["period_id"]),
                 "token": _new_token(tokens), "group": groups[task["id"]],
                 "src": task["id"]})
            # 定位签到的锚点跟着码走：同一个地方，新场次照样按定位签
            conn.execute(sa.text(
                "INSERT INTO checkin_geos "
                "(id, task_id, lat, lng, radius_m, accuracy_m, set_by, set_at) "
                "SELECT :id, :task, lat, lng, radius_m, accuracy_m, set_by, set_at "
                "FROM checkin_geos WHERE task_id = :src"),
                {"id": _uid("geo"), "task": new_task, "src": task["id"]})

        # 签到记录与时长明细：岗位认得出的按岗位走；扫整场统一码、没落到岗位上
        # 的，按这个人现在站在哪一场认（报名记录上面刚搬过去）
        owned = ("shift_id = :old AND (position_id = :pid OR "
                 "(position_id IS NULL AND user_id IN "
                 "(SELECT user_id FROM enrollments WHERE shift_id = :new2 "
                 "AND status <> 'cancelled')))")
        for table in ("checkin_records", "hours_segments"):
            _move_by_period(conn, table, new_shift, periods, owned,
                            {"old": old_shift, "pid": position["id"],
                             "new2": new_shift})

        for row in materials:
            # 文件本身共用一份：复制的是「这一场也有这份材料」这条记录
            conn.execute(sa.text(
                "INSERT INTO attachments (id, owner_type, owner_id, activity_id, "
                "kind, title, stored_name, size, url, uploaded_by, created_at) "
                "SELECT :id, owner_type, :owner, activity_id, kind, title, "
                "stored_name, size, url, uploaded_by, created_at "
                "FROM attachments WHERE id = :src"),
                {"id": _uid("att"), "owner": new_shift, "src": row["id"]})

    # 原场次剩下的那个岗位：场次名对齐到它
    conn.execute(sa.text(
        "UPDATE shifts SET name = :name WHERE id = :old"),
        {"name": keep["name"][:128], "old": old_shift})
    conn.execute(sa.text("UPDATE positions SET sort = 0 WHERE id = :pid"),
                 {"pid": keep["id"]})
    # 原来「整场都行」的意向，现在这一场就是这个岗位
    conn.execute(sa.text(
        "UPDATE enrollments SET position_id = :pid WHERE shift_id = :old "
        "AND status = 'applied' AND position_id IS NULL"),
        {"pid": keep["id"], "old": old_shift})


def _merge_tags(shift_tags: dict, position_tags: dict) -> dict:
    """场次级条件并进岗位级：两边都有同一个标签就取交集（两边都要满足），
    只一边有就取那一边。

    交集空了说明原来的两套条件互相矛盾、谁都报不进来；这时留场次那一份，
    不让它变成「不限」——把条件悄悄放宽比留着一个谁都不满足的条件更糟。
    """
    out = dict(position_tags or {})
    for tag, values in (shift_tags or {}).items():
        mine = out.get(tag)
        if not mine:
            out[tag] = values
            continue
        both = [v for v in mine if v in values]
        out[tag] = both or values
    return out


def _collapse_tags_and_location(conn) -> None:
    """普通岗位的条件与地点收口到岗位上，场次那一份清空。"""
    rows = conn.execute(sa.text(
        "SELECT p.id, p.location, p.requirement_tags, "
        "       s.id AS shift_id, s.location AS shift_location, "
        "       s.requirement_tags AS shift_tags "
        "FROM positions p JOIN shifts s ON s.id = p.shift_id "
        "WHERE s.kind = 'service' AND s.mode = 'pick'")).mappings().all()
    for row in rows:
        merged = _merge_tags(row["shift_tags"], row["requirement_tags"])
        location = row["location"] or row["shift_location"] or ""
        conn.execute(sa.text(
            "UPDATE positions SET requirement_tags = CAST(:tags AS JSON), "
            "location = :loc WHERE id = :pid"),
            {"tags": json.dumps(merged, ensure_ascii=False),
             "loc": location[:128], "pid": row["id"]})
    conn.execute(sa.text(
        "UPDATE shifts SET requirement_tags = CAST('{}' AS JSON), location = '' "
        "WHERE kind = 'service' AND mode = 'pick'"))


def upgrade() -> None:
    conn = op.get_bind()
    tokens = {row[0] for row in conn.execute(
        sa.text("SELECT token FROM checkin_tasks"))}
    for shift_id, positions in _pick_shifts(conn).items():
        if len(positions) > 1:
            _split_shift(conn, shift_id, positions, tokens)
    _collapse_tags_and_location(conn)


def downgrade() -> None:
    # 拆开之后，哪几个岗位原来同属一场、哪条报名是拆出来补的，库里都不再有
    # 记录可循：合回去只能靠猜，猜错就是把两个岗位的名单混成一份。
    raise NotImplementedError("岗位自带时段不可逆：如需回退请从备份恢复")
