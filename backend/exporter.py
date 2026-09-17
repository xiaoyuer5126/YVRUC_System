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

import re
import zipfile
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy.orm import Session as OrmSession

from . import config, scoring
from .models import (Activity, Attachment, CheckinRecord, Enrollment,
                     FormQuestion, HoursRecord, Shift, User, now)

MATERIAL_DIR = config.DATA_DIR / "materials"
ATTACH_DIR = "附件"


def disarm_formulas(wb) -> None:
    """存盘前把「看着像公式」的单元格按文本存。

    openpyxl 见到 = 开头的字符串就当公式收下（data_type 'f'），Excel 打开
    即执行。这些表里有志愿者自己填的问卷答案和微信号，谁都可以写一句
    =HYPERLINK("http://…","点我")，拿到表的人一打开就中招。
    平台从不导出真公式，所以在唯一的出口统一扫一遍，比逐处转义可靠。
    """
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if cell.data_type == "f":
                    cell.data_type = "s"


class Export:
    """一次导出的产物：工作簿，加上答案里引用到的上传文件。

    有文件就打成 zip（Excel + 附件目录），单元格里写相对路径并加超链接，
    解压后在 Excel 里点得开。不用平台的下载链接——那要登录，发给别人打不开。
    """

    def __init__(self, db: OrmSession) -> None:
        self.db = db
        self.wb = Workbook()
        self.files: list[tuple[str, Path]] = []      # (包内路径, 磁盘路径)
        self._used: set[str] = set()

    def attach(self, u: User, q: FormQuestion, attachment_id) -> str:
        """把一份文件答案登记进包里，返回单元格该写的相对路径；文件不在了给空。"""
        att = self.db.get(Attachment, str(attachment_id))
        if att is None:
            return ""
        path = MATERIAL_DIR / att.stored_name
        if not path.exists():
            return ""
        ext = Path(att.stored_name).suffix
        base = re.sub(r'[\\/:*?"<>|]', "_", f"{u.name}_{u.student_id}_{q.title}")
        arc = f"{ATTACH_DIR}/{base}{ext}"
        n = 2
        while arc in self._used:
            arc = f"{ATTACH_DIR}/{base}({n}){ext}"
            n += 1
        self._used.add(arc)
        self.files.append((arc, path))
        return arc

    def link(self, cell, arc: str) -> None:
        cell.value = arc
        cell.hyperlink = arc
        cell.font = Font(color="0563C1", underline="single")

    def bundle(self, xlsx_name: str) -> tuple[BytesIO, str]:
        """(内容, 扩展名)：没有文件就是 .xlsx，有就是 .zip。"""
        disarm_formulas(self.wb)
        buf = BytesIO()
        self.wb.save(buf)
        buf.seek(0)
        if not self.files:
            return buf, "xlsx"
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr(xlsx_name, buf.getvalue())
            for arc, path in self.files:
                z.write(path, arc)
        out.seek(0)
        return out, "zip"

    def write_answers(self, ws, u: User, questions: list[FormQuestion],
                      answers: dict | None, line: list) -> None:
        """在 line 后面接上一题一列的答案并写入；文件题写包内路径并加超链接。"""
        links = []
        for q in questions:
            v = (answers or {}).get(q.id)
            if q.kind == "file" and v:
                arc = self.attach(u, q, v)
                links.append((len(line) + 1, arc))
                line.append(arc)
            elif isinstance(v, list):
                line.append("、".join(str(x) for x in v))
            else:
                line.append("" if v is None else str(v))
        ws.append(line)
        for col, arc in links:
            if arc:
                self.link(ws.cell(row=ws.max_row, column=col), arc)

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


def build_activity_workbook(db: OrmSession, activity: Activity) -> Export:
    ex = Export(db)
    wb = ex.wb
    shifts = sorted(activity.shifts, key=lambda g: g.sort)

    # ---------- 概览 Sheet ----------
    overview = wb.active
    overview.title = "活动概览"
    overview.append(["志愿服务管理平台 · 排班表导出"])
    overview.append(["活动名称", activity.title])
    overview.append(["活动类型", f"{activity.recruit_label()} · {activity.label_status()}"])
    # 联系人优先用结构化 contacts（"姓名 电话"，多人分号连接），
    # 为空则回退旧字段 contact_info
    contact_text = "；".join(
        f"{(c or {}).get('name', '')} {(c or {}).get('phone', '')}".strip()
        for c in (activity.contacts or []) if isinstance(c, dict)
    ).strip("；")
    overview.append(["联系人",
                     contact_text or activity.contact_info or "—"])
    span = activity.time_span()
    if span:
        overview.append(["活动时间",
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

    # 即选即得的报名问卷答案挂在报名记录上，一题一列跟在签到后面；
    # 筛选活动的答案在「报名问卷结果」那份导出里
    questions = [] if activity.is_screened() else (
        db.query(FormQuestion).filter(FormQuestion.activity_id == activity.id)
        .order_by(FormQuestion.sort).all())

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
        header += [q.title for q in questions]
        ws.append(header)
        _style_header(ws, len(header))

        for enr, u in rows:
            line = [u.name, u.student_id, u.college, u.phone, g.span_text()]
            st = positions.get(enr.position_id) if enr.position_id else None
            if has_positions:
                line += [(st.group_name if st and st.group_name else "—"),
                         st.name if st else "待分配",
                         "组长 ★" if enr.is_leader else "",
                         (st.location or activity.location) if st else "—",
                         "是" if enr.allow_reassign else "否"]
            rec = checked.get(u.id)
            line += ["已到岗 ✓" if rec else "未签到"]
            line += [rec.checkout_at.strftime("%m-%d %H:%M")
                     if rec and rec.checkout_at else "—"]
            ex.write_answers(ws, u, questions, enr.answers, line)

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
                        value="以下人员尚未分配岗位").font = \
                    Font(bold=True, color="C00000")
                for i, (_e, u) in enumerate(unassigned, start=start + 1):
                    ws.cell(row=i, column=1, value=u.name)
                    ws.cell(row=i, column=2, value=u.student_id)

    # ---------- 志愿时长 Sheet：有账才有这一页 ----------
    hours_rows = (db.query(HoursRecord, User)
                  .join(User, HoursRecord.user_id == User.id)
                  .filter(HoursRecord.activity_id == activity.id)
                  .order_by(User.name).all())
    if hours_rows:
        ws = wb.create_sheet(_safe_sheet_name("志愿时长", used_names))
        header = ["姓名", "学号", "学院", "时长(h)", "报酬方式", "本人确认",
                  "来源", "明细"]
        ws.append(header)
        _style_header(ws, len(header))
        for rec, u in hours_rows:
            ws.append([u.name, u.student_id, u.college, rec.hours,
                       rec.reward_label(),
                       rec.confirmed_at.strftime("%m-%d %H:%M") if rec.confirmed_at else "未确认",
                       "签到统计" if rec.source == "checkin" else "手动添加",
                       rec.parts_text()])
        for idx, w in enumerate((12, 14, 16, 10, 10, 14, 10, 40), start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w

    return ex


def build_hours_workbook(db: OrmSession, activity: Activity) -> BytesIO:
    """时长记录：一人一行，姓名、身份证号、岗位（留空）、时长、备注。

    备注是明细拼成的一行（时段 + 岗位名，分号隔开，不换行）。
    选了劳务的人不导：平台不给他们记时长。
    """
    rows = (db.query(HoursRecord, User)
            .join(User, HoursRecord.user_id == User.id)
            .filter(HoursRecord.activity_id == activity.id,
                    HoursRecord.reward == "hours")
            .order_by(User.name).all())
    wb = Workbook()
    ws = wb.active
    ws.title = "时长记录"
    header = ["姓名", "身份证号", "岗位", "时长", "备注"]
    ws.append(header)
    _style_header(ws, len(header))
    for rec, u in rows:
        ws.append([u.name, u.id_card or "", "", rec.hours, rec.parts_text()])
    for col, w in zip("ABCDE", (12, 22, 12, 8, 48)):
        ws.column_dimensions[col].width = w
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_application_workbook(db: OrmSession, activity: Activity) -> Export:
    """报名问卷结果：一人一行，一题一列。

    筛选是在表格里做的事——把答案摊平了导出来，部长们习惯在 Excel 里排序、
    筛选、分工看。被显示条件挡掉的题对那个人本来就不存在，留空。
    """
    from .models import Application

    questions = (db.query(FormQuestion)
                 .filter(FormQuestion.activity_id == activity.id)
                 .order_by(FormQuestion.sort).all())
    apps = (db.query(Application)
            .filter(Application.activity_id == activity.id)
            .order_by(Application.created_at).all())
    shifts = {g.id: g for g in activity.main_shifts()}
    positions = {p.id: p for g in shifts.values() for p in g.positions}

    ex = Export(db)
    ws = ex.wb.active
    ws.title = "报名问卷结果"
    header = ["姓名", "学号", "学院", "校区", "学段", "手机号", "提交时间",
              "意向场次", "录取结果", "组长意愿", "内部提示分"]
    header += [q.title for q in questions]
    ws.append(header)
    _style_header(ws, len(header))

    for app in apps:
        u = db.get(User, app.user_id)
        rows = (db.query(Enrollment)
                .filter(Enrollment.activity_id == activity.id,
                        Enrollment.user_id == app.user_id).all())
        wanted = []
        for e in rows:
            g = shifts.get(e.shift_id)
            if g is None:
                continue
            st = positions.get(e.position_id) if e.position_id else None
            mark = "（录取）" if e.status == "enrolled" else ""
            wanted.append(f"{g.name}{'·' + st.name if st else ''}{mark}")
        line = [u.name, u.student_id, u.college, u.campus, u.stage, u.phone,
                f"{app.created_at:%Y-%m-%d %H:%M}", "、".join(wanted),
                app.status_label(), "愿意" if app.leader_willing else "",
                scoring.score_of(db, u.id)]
        ex.write_answers(ws, u, questions, app.answers, line)

    ws.column_dimensions["A"].width = 12
    for i in range(len(header) - len(questions) + 1, len(header) + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 26
    return ex
