"""活动的几份导出表。

  · 排班表（build_activity_workbook）：交给现场负责人点名、排班。
    活动概览 → 志愿者信息（一人一行，报保险、报入校用）→ 每个场次一张名单。
  · 问卷结果（build_answers_workbook）：志愿者管理页按场次、岗位挑人导出，
    答案一题一列，上传的文件按「场次/岗位」分文件夹另打附件包。
  · 报名问卷结果（build_application_workbook）：报名后筛选的全部报名者。
  · 时长记录（build_hours_workbook）：给志愿北京录时长。

每次下载都记审计（见 routers/staff_ops 的导出接口）。
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

from . import config, hours as hours_flow, scoring
from .models import (Activity, Attachment, CheckinRecord, Enrollment,
                     FormQuestion, HoursRecord, Position, Shift, User, now)

MATERIAL_DIR = config.DATA_DIR / "materials"


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

    Excel 与附件分开下：导出永远只给 .xlsx，附件另走一个接口打成 zip。
    多数人只要表格，不必每次都拖着几十兆的附件走一趟。
    单元格里写附件在 zip 里的文件名（一字不差，便于对照），不加超链接：
    表和包是分两次下的，解压工具各有各的放法（有的按包名再套一层目录），
    相对链接十有八九点不开，还不如一个能直接拿去搜的文件名。
    也不用平台的下载链接——那要登录，发给别人打不开。
    """

    def __init__(self, db: OrmSession) -> None:
        self.db = db
        self.wb = Workbook()
        self.files: list[tuple[str, Path]] = []      # (包内文件名, 磁盘路径)
        self._used: set[str] = set()

    def attach(self, u: User, q: FormQuestion, attachment_id, folder: str = "") -> str:
        """把一份文件答案登记进附件包，返回单元格该写的文件名；文件不在了给空。

        folder 是包内的目录（如「上午场/引导岗/」），单元格里写的也带着它。
        """
        att = self.db.get(Attachment, str(attachment_id))
        if att is None:
            return ""
        path = MATERIAL_DIR / att.stored_name
        if not path.exists():
            return ""
        ext = Path(att.stored_name).suffix
        base = folder + _safe_path_part(f"{u.name}_{u.student_id}_{q.title}")
        arc = f"{base}{ext}"
        n = 2
        while arc in self._used:
            arc = f"{base}({n}){ext}"
            n += 1
        self._used.add(arc)
        self.files.append((arc, path))
        return arc

    def xlsx(self) -> BytesIO:
        """工作簿本身，不含附件。"""
        disarm_formulas(self.wb)
        buf = BytesIO()
        self.wb.save(buf)
        buf.seek(0)
        return buf

    def attachments_zip(self) -> BytesIO | None:
        """附件包，文件名与 Excel 里写的一致；没有附件给 None。

        登记之后、打包之前文件没了（被清理）的也跳过，不让整包失败。
        """
        if not self.files:
            return None
        out = BytesIO()
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
            for arc, path in self.files:
                try:
                    z.write(path, arc)
                except FileNotFoundError:
                    continue
        out.seek(0)
        return out

    def write_answers(self, ws, u: User, questions: list[FormQuestion],
                      answers: dict | None, line: list, folder: str = "") -> None:
        """在 line 后面接上一题一列的答案并写入；文件题写附件包内的文件名。"""
        for q in questions:
            v = (answers or {}).get(q.id)
            if q.kind == "file" and v:
                line.append(self.attach(u, q, v, folder))
            elif isinstance(v, list):
                line.append("、".join(str(x) for x in v))
            else:
                line.append("" if v is None else str(v))
        ws.append(line)


def _safe_path_part(name: str) -> str:
    """附件包里的一段文件名或目录名：Windows 不认的字符换成下划线。"""
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "_"


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


def roster_sheet_names(activity: Activity) -> dict[str, str]:
    """排班表里每个场次的工作表名（场次 id → 表名）。导出和导入排班共用：
    名字截断、重名加序号都按同一套规则，导回来才找得到自己那张表。"""
    used = {"活动概览", "志愿者信息"}
    return {g.id: _safe_sheet_name(g.name, used)
            for g in sorted(activity.shifts, key=lambda g: g.sort)}


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


def _id_card_text(u: User) -> str:
    if u.id_card:
        return u.id_card
    return "无大陆身份证" if u.no_mainland_id else ""


def _roster_order(positions: dict):
    """名单的排法：先按岗位分块，块内组长排头（点名时先找他），其余按报名先后。
    没岗位的（统一分配还没排完）沉到最后，一眼看得出还差谁没安排。"""
    def key(pair):
        enr, _u = pair
        st = positions.get(enr.position_id) if enr.position_id else None
        return (0 if st else 1, st.sort if st else 0,
                0 if enr.is_leader else 1, enr.joined_at or now())
    return key


def build_activity_workbook(db: OrmSession, activity: Activity) -> BytesIO:
    """排班表：活动概览、志愿者信息、每个场次一张名单。

    这张表是拿去现场点名、报保险的。问卷答案另有「问卷结果」导出，
    时长另有「时长记录」导出，都不混在这儿。
    """
    wb = Workbook()
    shifts = sorted(activity.shifts, key=lambda g: g.sort)

    # ---------- 概览 Sheet ----------
    overview = wb.active
    overview.title = "活动概览"
    overview.append(["志愿服务管理平台 · 排班表导出"])
    overview.append(["活动名称", activity.title])
    overview.append(["活动类型", f"{activity.recruit_label()} · {activity.label_status()}"])
    # 联系人是「姓名 电话」，多人分号连接
    contact_text = "；".join(
        f"{(c or {}).get('name', '')} {(c or {}).get('phone', '')}".strip()
        for c in (activity.contacts or []) if isinstance(c, dict)
    ).strip("；")
    overview.append(["联系人", contact_text or "—"])
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

    # 每个场次的在册名单，按点名的顺序排好；志愿者信息与各场次名单共用
    rosters = []
    for g in shifts:
        positions = {s.id: s for s in g.positions}
        rows = (db.query(Enrollment, User)
                .filter(Enrollment.shift_id == g.id,
                        Enrollment.status == "enrolled")
                .join(User, Enrollment.user_id == User.id).all())
        rows.sort(key=_roster_order(positions))
        rosters.append((g, positions, rows))

    # ---------- 志愿者信息 Sheet：全活动每人一行 ----------
    info = wb.create_sheet("志愿者信息")
    header = ["姓名", "学号", "学院", "身份证号", "手机号", "微信号"]
    info.append(header)
    _style_header(info, len(header))
    seen: set[str] = set()
    for _g, _positions, rows in rosters:
        for _enr, u in rows:
            if u.id in seen:
                continue
            seen.add(u.id)
            info.append([u.name, u.student_id, u.college, _id_card_text(u),
                         u.phone, u.wechat])
    for col, w in zip("ABCDEF", (12, 14, 16, 22, 15, 16)):
        info.column_dimensions[col].width = w

    # ---------- 每个场次一个 Sheet ----------
    sheet_names = roster_sheet_names(activity)
    for g, positions, rows in rosters:
        ws = wb.create_sheet(sheet_names[g.id])
        has_positions = bool(positions)
        header = ["姓名", "学号", "性别", "学院", "手机号", "微信号", "场次时间"]
        header += ["岗位组", "岗位", "组长", "服务地点"] if has_positions else []
        header += ["签到状态", "签退时间"]
        ws.append(header)
        _style_header(ws, len(header))

        for enr, u in rows:
            line = [u.name, u.student_id, u.gender, u.college, u.phone, u.wechat,
                    g.span_text()]
            st = positions.get(enr.position_id) if enr.position_id else None
            if has_positions:
                line += [(st.group_name if st and st.group_name else "—"),
                         st.name if st else Position.PLACEHOLDER_NAME,
                         "组长 ★" if enr.is_leader else "",
                         (st.location or activity.location) if st else "—"]
            rec = checked.get(u.id)
            line += ["已到岗 ✓" if rec else "未签到"]
            line += [rec.checkout_at.strftime("%m-%d %H:%M")
                     if rec and rec.checkout_at else "—"]
            ws.append(line)

        for idx, w in enumerate((12, 14, 6, 16, 15, 16, 18), start=1):
            ws.column_dimensions[get_column_letter(idx)].width = w
        for idx in range(8, len(header) + 1):
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

    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_answers_workbook(db: OrmSession, activity: Activity,
                           shift_ids: set[str] | None = None,
                           position_ids: set[str] | None = None) -> Export:
    """问卷结果：在册志愿者每人每个岗位一行，一题一列。

    shift_ids / position_ids 为空表示不限。挑了岗位时，还没分到岗位的人不在其中。
    附件按「场次/岗位/姓名_学号_题目」分文件夹：部长们拿去按点位看简历，
    一个文件夹就是一个点位的人。同一个人在两个岗位，文件就在两个文件夹里各放一份。
    报名后筛选的答案在报名记录（Application）上，即选即得的在每条报名上。
    """
    from .models import Application

    questions = (db.query(FormQuestion)
                 .filter(FormQuestion.activity_id == activity.id)
                 .order_by(FormQuestion.sort).all())
    screened = activity.is_screened()
    apps = {a.user_id: a for a in db.query(Application)
            .filter(Application.activity_id == activity.id).all()} if screened else {}

    ex = Export(db)
    ws = ex.wb.active
    ws.title = "问卷结果"
    header = ["姓名", "学号", "学院", "手机号", "场次", "岗位"]
    header += [q.title for q in questions]
    ws.append(header)
    _style_header(ws, len(header))

    for g in sorted(activity.main_shifts(), key=lambda x: x.sort):
        if shift_ids and g.id not in shift_ids:
            continue
        positions = {s.id: s for s in g.positions}
        rows = (db.query(Enrollment, User)
                .filter(Enrollment.shift_id == g.id,
                        Enrollment.status == "enrolled")
                .join(User, Enrollment.user_id == User.id).all())
        rows.sort(key=_roster_order(positions))
        for enr, u in rows:
            st = positions.get(enr.position_id) if enr.position_id else None
            if position_ids and (st is None or st.id not in position_ids):
                continue
            pos_name = st.name if st else "未分配岗位"
            folder = f"{_safe_path_part(g.name)}/{_safe_path_part(pos_name)}/"
            answers = (apps[u.id].answers if u.id in apps else {}) if screened \
                else enr.answers
            ex.write_answers(ws, u, questions, answers,
                             [u.name, u.student_id, u.college, u.phone, g.name, pos_name],
                             folder)

    for col, w in zip("ABCDEF", (12, 14, 16, 15, 18, 16)):
        ws.column_dimensions[col].width = w
    for i in range(len(header) - len(questions) + 1, len(header) + 1):
        ws.column_dimensions[get_column_letter(i)].width = 26
    return ex


def build_hours_workbook(db: OrmSession, activity: Activity) -> BytesIO:
    """时长记录：一人一行，姓名、学号、身份证号、时长、备注。

    备注是明细拼成的一行（时段 + 岗位名，分号隔开，不换行）。
    选了劳务的人不导：平台不给他们记时长。党校同学也不导：这张表是给志愿北京
    录时长的，他们的时长不能同时拿去做学分认定（见 hours.PARTY_SCHOOL）。
    """
    party = hours_flow.party_school_users(db, activity.id)
    rows = [(rec, u) for rec, u in (
        db.query(HoursRecord, User)
        .join(User, HoursRecord.user_id == User.id)
        .filter(HoursRecord.activity_id == activity.id,
                HoursRecord.reward == "hours")
        .order_by(User.name).all()) if u.id not in party]
    wb = Workbook()
    ws = wb.active
    ws.title = "时长记录"
    header = ["姓名", "学号", "身份证号", "时长", "备注"]
    ws.append(header)
    _style_header(ws, len(header))
    for rec, u in rows:
        ws.append([u.name, u.student_id, u.id_card or "", rec.hours,
                   rec.parts_text()])
    for col, w in zip("ABCDE", (12, 14, 22, 8, 48)):
        ws.column_dimensions[col].width = w
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_application_workbook(db: OrmSession, activity: Activity) -> Export:
    """报名问卷结果：一人一行，一题一列。

    筛选是在表格里做的事——把答案摊平了导出来，部长们习惯在 Excel 里排序、
    筛选、分工看。序号是交申请的先后，排序打乱了也找得回来。被显示条件挡掉的
    题对那个人本来就不存在，留空。
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
    header = ["序号", "姓名", "学号", "学院", "校区", "学段", "手机号", "提交时间",
              "意向场次", "录取结果", "组长意愿", "内部提示分"]
    header += [q.title for q in questions]
    ws.append(header)
    _style_header(ws, len(header))

    for seq, app in enumerate(apps, 1):
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
        line = [seq, u.name, u.student_id, u.college, u.campus, u.stage, u.phone,
                f"{app.created_at:%Y-%m-%d %H:%M}", "、".join(wanted),
                app.status_label(), "愿意" if app.leader_willing else "",
                scoring.score_of(db, u.id)]
        ex.write_answers(ws, u, questions, app.answers, line)

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 12
    for i in range(len(header) - len(questions) + 1, len(header) + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 26
    return ex
