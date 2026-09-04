"""智能导出排班表（《设计方案》阶段五.1 的核心痛点）。

导出逻辑：
  · 第 1 个 Sheet：活动概览（各组已排/名额、签到统计）；
  · 之后按"场次"分 Sheet，字段：
      姓名 学号 学院 手机号 | 岗位 岗位时间 岗位地点 签到状态 签退时间
    这张表直接交付现场负责人用于点名和排班，无需人工二次制表；
  · 有未分配人员的组追加「未分配名单」Sheet。

安全（《设计方案》风险应对）：正式版对文件强制加密（密码经企业微信单独
发送下载人）并记录每次下载；演示版先做**下载留痕**（审计中心可见），
加密在对接企业微信后启用。
"""
from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session as OrmSession

from .models import Activity, CheckinRecord, Enrollment, Shift, User, now

HEADER_FILL = PatternFill("solid", fgColor="9E2235")   # 人大红
HEADER_FONT = Font(name="微软雅黑", bold=True, color="FFFFFF")
BODY_FONT = Font(name="微软雅黑", size=11)
CENTER = Alignment(horizontal="center", vertical="center")

SHEET_FORBIDDEN = set(':\\/?*[]')


def _safe_sheet_name(name: str, used: set[str]) -> str:
    base = "".join(c for c in name if c not in SHEET_FORBIDDEN).strip() or "场次"
    base = base[:28]
    candidate, i = base, 1
    while candidate in used:
        i += 1
        candidate = f"{base[:26]}~{i}"
    used.add(candidate)
    return candidate


def _checked_records(db: OrmSession, activity_id: str) -> dict[str, CheckinRecord]:
    rows = db.query(CheckinRecord).filter(
        CheckinRecord.activity_id == activity_id).all()
    return {r.user_id: r for r in rows}


def _style_header(ws, ncols: int) -> None:
    for col in range(1, ncols + 1):
        cell = ws.cell(row=1, column=col)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = CENTER
    ws.freeze_panes = "A2"


def build_activity_workbook(db: OrmSession, activity: Activity) -> BytesIO:
    wb = Workbook()
    shifts = sorted(activity.shifts, key=lambda g: g.sort)

    # ---------- 概览 Sheet ----------
    overview = wb.active
    overview.title = "活动概览"
    overview.append(["志愿服务管理平台 · 排班表导出"])
    overview.append(["活动名称", activity.title])
    overview.append(["活动类型", f"{activity.recruit_label()} · {activity.label_status()}"])
    # 公示联系方式优先用结构化 contacts（"姓名 电话"，多人分号连接），
    # 为空则回退旧字段 contact_info
    contact_text = "；".join(
        f"{(c or {}).get('name', '')} {(c or {}).get('phone', '')}".strip()
        for c in (activity.contacts or []) if isinstance(c, dict)
    ).strip("；")
    overview.append(["公示联系方式",
                     contact_text or activity.contact_info or "—"])
    span = activity.time_span()
    if span:
        overview.append(["整体时间",
                         f"{span[0]:%Y-%m-%d %H:%M} ~ {span[1]:%Y-%m-%d %H:%M}"])
    overview.append([])
    overview.append(["场次", "类型", "时间段", "模式", "已报名/名额", "已到岗"])
    checked = _checked_records(db, activity.id)
    from sqlalchemy import func
    for g in shifts:
        enrolled = (db.query(func.count(Enrollment.id))
                     .filter(Enrollment.shift_id == g.id,
                             Enrollment.status == "enrolled").scalar() or 0)
        here = (db.query(func.count(func.distinct(CheckinRecord.user_id)))
                .filter(CheckinRecord.shift_id == g.id).scalar() or 0)
        # 培训/彩排不招人，名额那一列写它属于哪个服务场次更有用
        if g.is_attached():
            parent = db.get(Shift, g.parent_id)
            quota = f"{enrolled} 人（随「{parent.name if parent else '?'}」）"
        else:
            quota = f"{enrolled}/{g.total_capacity()}"
        overview.append([g.name, g.kind_label(), g.span_text(),
                         g.mode_label() if not g.is_attached() else "—",
                         quota, here])
    overview["A1"].font = Font(name="微软雅黑", bold=True, size=14)
    _style_header(overview, 6)
    for col, w in zip("ABCDE", (24, 30, 14, 14, 10)):
        overview.column_dimensions[col].width = w

    # ---------- 每个场次一个 Sheet ----------
    used_names: set[str] = {"活动概览"}
    for g in shifts:
        positions = {s.id: s for s in g.positions}
        rows = (db.query(Enrollment, User)
                .filter(Enrollment.shift_id == g.id,
                        Enrollment.status == "enrolled")
                .join(User, Enrollment.user_id == User.id).all())
        # 这张表是拿去现场点名的，所以按现场的组织方式排：先按岗位分块，
        # 块内组长排头（点名时先找他），其余按报名先后。
        # 没岗位的（统一分配还没排完）沉到最后，一眼看得出还差谁没安排。
        def _order(pair):
            enr, u = pair
            st = positions.get(enr.position_id) if enr.position_id else None
            return (0 if st else 1,            # 有岗位的在前
                    st.sort if st else 0,      # 岗位按管理者排的顺序
                    0 if enr.is_leader else 1,  # 组长置顶，但仍在本岗位内
                    enr.joined_at or now())     # 其余按报名时间
        rows.sort(key=_order)

        ws = wb.create_sheet(_safe_sheet_name(g.name, used_names))
        has_positions = bool(positions)
        header = ["姓名", "学号", "学院", "手机号", "场次时间"]
        header += (["岗位组", "岗位", "组长", "服务地点", "接受调剂"]
                   if has_positions else [])
        header += ["签到状态", "签退时间"]
        ws.append(header)
        _style_header(ws, len(header))

        for enr, u in rows:
            line = [u.name, u.student_id, u.college, u.phone, g.span_text()]
            st = positions.get(enr.position_id) if enr.position_id else None
            if has_positions:
                line += [(st.group_name if st and st.group_name else "—"),
                         st.name if st else "待分配",
                         "组长 ★" if enr.is_leader else "",
                         st.location if st else "—",
                         "是" if enr.allow_reassign else "否"]
            rec = checked.get(u.id)
            line += ["已到岗 ✓" if rec else "未签到"]
            line += [rec.checkout_at.strftime("%m-%d %H:%M")
                     if rec and rec.checkout_at else "—"]
            ws.append(line)

        for idx, w in enumerate((12, 14, 16, 15, 18), start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w
        for idx in range(6, len(header) + 1):
            ws.column_dimensions[get_column_letter(idx)].width = 14

        # 未分配名单（有岗位且有人没分到时才有意义）。
        # 「待分配」占位岗位里的人也算——他们在表里挂着一个岗位名，
        # 但那不是真岗位，现场按它去点名会找不到地方。
        if has_positions:
            holder = g.placeholder()
            unassigned = [(e, u) for e, u in rows
                          if not e.position_id
                          or (holder and e.position_id == holder.id)]
            if unassigned:
                start = ws.max_row + 2
                ws.cell(row=start, column=1,
                        value="以下人员尚未分配岗位（调配期处理）").font = \
                    Font(bold=True, color="C00000")
                for i, (_e, u) in enumerate(unassigned, start=start + 1):
                    ws.cell(row=i, column=1, value=u.name)
                    ws.cell(row=i, column=2, value=u.student_id)

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
