"""活动的几份导出表。

  · 排班表（build_activity_workbook）：交给现场负责人点名、排班。
    活动概览 → 志愿者信息（一人一行，报保险、报入校用）→ 每个场次一张名单。
  · 问卷结果（build_answers_workbook）：志愿者管理页按场次、岗位挑人导出，
    答案一题一列，上传的文件按「场次/岗位」分文件夹另打附件包。
  · 报名问卷结果（build_application_workbook）：报名后筛选的全部报名者。
  · 时长记录（build_hours_workbook）：给志愿北京录时长；另附「劳务」页，
    列出有劳务的人，给发劳务用。
  · 活动汇总（build_activities_summary_workbook）：一场活动一行，给校青协做统计。

每次下载都记审计（见 routers/staff_ops 的导出接口）。
"""
from __future__ import annotations

import re
import zipfile
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import and_, func
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import selectinload

from . import config, hours as hours_flow, scoring
from .serializers import (admin_status_of, answer_attachments,
                          recruit_scope_text)
from .models import (Activity, Attachment, CheckinRecord, Department, Enrollment,
                     FormQuestion, HoursClaim, HoursRecord, HoursSegment,
                     Position, Shift, User, ViolationRecord, days_text, now)

MATERIAL_DIR = config.MATERIAL_DIR


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

    def __init__(self, db: OrmSession, activity_id: str) -> None:
        self.db = db
        self.wb = Workbook()
        self.files: list[tuple[str, Path]] = []      # (包内文件名, 磁盘路径)
        self._used: set[str] = set()
        # 这个活动的问卷附件一次取齐：导出几百人、每人几道文件题，
        # 逐个按 id 去查就是几百条 SQL
        self._atts = answer_attachments(db, activity_id)

    def attach(self, u: User, q: FormQuestion, attachment_id, folder: str = "") -> str:
        """把一份文件答案登记进附件包，返回单元格该写的文件名；文件不在了给空。

        folder 是包内的目录（如「上午场/引导岗/」），单元格里写的也带着它。
        """
        att = self._atts.get(str(attachment_id))
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
    if activity.time_span():
        overview.append(["活动时间", activity.time_text()])
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
        # 培训/彩排不招人，名额那一列写有多少人要来
        if g.is_attached():
            quota = f"{enrolled} 人（随服务场次）"
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
    header = ["姓名", "学号", "性别", "学院", "年级", "校区", "学段",
              "身份证号", "手机号", "微信号"]
    info.append(header)
    _style_header(info, len(header))
    seen: set[str] = set()
    for _g, _positions, rows in rosters:
        for _enr, u in rows:
            if u.id in seen:
                continue
            seen.add(u.id)
            info.append([u.name, u.student_id, u.gender, u.college, u.grade,
                         u.campus, u.stage, _id_card_text(u), u.phone, u.wechat])
    for col, w in zip("ABCDEFGHIJ", (12, 14, 6, 16, 8, 14, 12, 22, 15, 16)):
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
                         st.place() if st else "—"]
            rec = checked.get(u.id)
            line += ["已到岗 ✓" if rec else "未签到"]
            line += [rec.checkout_at.strftime("%Y-%m-%d %H:%M")
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

    ex = Export(db, activity.id)
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
    """时长记录与劳务：两页，各一人一行。

    「时长记录」给志愿北京录时长：只导计入志愿时长的部分，时长是
    HoursRecord.counted_hours，备注只拼记志愿时长的那几段（时段 + 岗位名，分号
    隔开，不换行）；选了劳务的段平台不给记时长，整笔都选了劳务的人不导。党校
    同学也不导：这张表是给志愿北京录时长的，他们的时长不能同时拿去做学分认定
    （见 hours.PARTY_SCHOOL）。
    「劳务」给发劳务用：本活动所有 pay_hours() > 0 的人，按姓名排序，一人一行；
    党校同学不能选劳务，不会出现在这页。劳务时长是 HoursRecord.pay_hours，
    备注只拼按劳务计的那几段。
    """
    party = hours_flow.party_school_users(db, activity.id)
    pairs = (db.query(HoursRecord, User)
             .join(User, HoursRecord.user_id == User.id)
             .filter(HoursRecord.activity_id == activity.id)
             .order_by(User.name).all())
    rows = [(rec, u) for rec, u in pairs
            if u.id not in party
            and not (rec.pay_hours() > 0 and rec.counted_hours() <= 0)]
    pay_rows = [(rec, u) for rec, u in pairs if rec.pay_hours() > 0]

    wb = Workbook()
    ws = wb.active
    ws.title = "时长记录"
    header = ["姓名", "学号", "身份证号", "时长", "备注"]
    ws.append(header)
    _style_header(ws, len(header))
    for rec, u in rows:
        ws.append([u.name, u.student_id, u.id_card or "", rec.counted_hours(),
                   rec.parts_text(hours_only=True)])
    for col, w in zip("ABCDE", (12, 14, 22, 8, 48)):
        ws.column_dimensions[col].width = w

    pay = wb.create_sheet("劳务")
    pay_header = ["姓名", "学号", "学院", "劳务时长", "所在时段与岗位"]
    pay.append(pay_header)
    _style_header(pay, len(pay_header))
    for rec, u in pay_rows:
        pay.append([u.name, u.student_id, u.college, rec.pay_hours(),
                    rec.parts_text(pay_only=True)])
    for col, w in zip("ABCDE", (12, 14, 16, 10, 48)):
        pay.column_dimensions[col].width = w

    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def build_claims_workbook(db: OrmSession, claims: list[HoursClaim]) -> BytesIO:
    """校外时长补登：给志愿北京录时长用，一条一行。

    只导通过的（已撤销的不再计入时长）。活动名称、活动日期、服务描述、备注都
    带上，录的时候能跟本人交的那条对上；身份证号取档案里的，提交就要求资料完整，
    所以不会缺。
    """
    wb = Workbook()
    ws = wb.active
    ws.title = "校外时长补登"
    header = ["姓名", "学号", "学院", "身份证号", "活动名称", "活动日期",
              "时长", "服务描述", "备注"]
    ws.append(header)
    _style_header(ws, len(header))
    # 提交人一次取齐：一条一查的话，一学期几百条补登就是几百条 SQL
    users = {u.id: u for u in db.query(User)
             .filter(User.id.in_({c.user_id for c in claims})).all()} \
        if claims else {}
    for c in claims:
        u = users.get(c.user_id)
        if u is None:
            continue
        ws.append([u.name, u.student_id, u.college or "", u.id_card or "",
                   c.title, c.service_date.isoformat(), c.counted_hours(),
                   c.service_desc, c.note])
    for col, w in zip("ABCDEFGHI", (12, 14, 16, 22, 28, 24, 8, 48, 20)):
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
    # 人、意向、提示分都按整份名单一次取齐：一人一条的话，三百份申请就是
    # 一千多条 SQL，导出要等上好几秒
    user_ids = [a.user_id for a in apps]
    users = {u.id: u for u in db.query(User)
             .filter(User.id.in_(set(user_ids))).all()} if user_ids else {}
    wanted_rows: dict[str, list[Enrollment]] = {uid_: [] for uid_ in user_ids}
    if user_ids:
        for e in (db.query(Enrollment)
                  .filter(Enrollment.activity_id == activity.id,
                          Enrollment.user_id.in_(set(user_ids))).all()):
            wanted_rows.setdefault(e.user_id, []).append(e)
    scores = scoring.briefs(db, user_ids)

    ex = Export(db, activity.id)
    ws = ex.wb.active
    ws.title = "报名问卷结果"
    header = ["序号", "姓名", "学号", "学院", "校区", "学段", "手机号", "提交时间",
              "意向场次", "录取结果", "组长意愿", "内部提示分"]
    header += [q.title for q in questions]
    ws.append(header)
    _style_header(ws, len(header))

    for seq, app in enumerate(apps, 1):
        u = users[app.user_id]
        rows = wanted_rows.get(app.user_id, [])
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
                scores[u.id]["score"]]
        ex.write_answers(ws, u, questions, app.answers, line)

    ws.column_dimensions["A"].width = 6
    ws.column_dimensions["B"].width = 12
    for i in range(len(header) - len(questions) + 1, len(header) + 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = 26
    return ex


# 进汇总表的状态：报名中、已截止、已结束，加上走完外联流程的两个终态
# （待外联确认、已完成）——那也是办完的活动，漏掉等于长期统计少一块。
# 草稿与审批线上的还没办，列进去等于把没发生的事算成数据。
SUMMARY_STATUSES = ("published", "closed", "finished", "vb_pending", "completed")
# 列按「是哪场活动 → 谁办的 → 什么时候办的 → 办在哪、招什么人 → 数字」排：
# 组织单位、联合主办方、归口审批部门三列挨着，拿到表的人一眼对得出归口是不是
# 跟主办一致；地点与范围跟着时间状态走，后面才是人数时数。
SUMMARY_HEADER = ("活动编号", "活动名称", "组织单位", "联合主办方", "归口审批部门",
                  "时间", "状态", "活动地点", "招募范围", "志愿者人数", "人次",
                  "总志愿时数", "劳务时数", "违规条数")
# 表底的口径说明：拿到表的人不必回头问人数怎么算、时数为什么不是整数。
# 写在合计行下面空一行处，表头与数据行的位置不因此挪动（见 _rows 用例）
SUMMARY_NOTES = (
    "说明：按活动结束时间归属区间，一场活动的人数、人次、时长整场算在它结束的那一侧；"
    "人数、人次按服务场次的报名计，补记录活动只有时长。",
    "时长按 0.5 小时向上取整（一人一场一笔，志愿与劳务两半各自取整）。",
    "归口审批部门是活动立项时归口的部门，没有写「—」；活动地点取活动填的地点，"
    "没填依次用服务场次的地点、岗位的地点，都没有写「—」。",
    "招募范围是这场活动限的校区、学段、学院，三项都没限写「面向全校」；"
    "违规条数按活动加总，迟到早退、无故请假、消极志愿、其他四类不分列。",
)
# 表里「这一列没有值」的写法：空着一格容易被当成漏填
SUMMARY_EMPTY = "—"


def activities_ending_in(db: OrmSession, *, start: datetime | None = None,
                         before: datetime | None = None
                         ) -> list[tuple[tuple[datetime, datetime], Activity]]:
    """结束时间落在区间里的活动（SUMMARY_STATUSES 那几个状态），带上活动时间。

    按活动算：一场活动整场归到它结束的那一侧，跨月办完的只算一次；办完再补录
    的时长跟着活动走，不另按补录那天归。数据总览的「活动总数」「累计时长」与
    活动汇总导出都用它，同一个区间两边数出来一样。排不出时间的（没有场次的
    招募活动）没有结束时间可归，跳过。start 起（含）、before 止（不含）。
    """
    picked = []
    # 场次一次带出来：time_span() 要用，逐场活动再去取就是一场一条 SQL，
    # 全库导出时条数跟着活动数涨
    for act in (db.query(Activity).options(selectinload(Activity.shifts))
                .filter(Activity.status.in_(SUMMARY_STATUSES))):
        span = act.time_span()
        if span is None:
            continue
        if start is not None and span[1] < start:
            continue
        if before is not None and span[1] >= before:
            continue
        picked.append((span, act))
    picked.sort(key=lambda x: (x[0][1], x[1].title))
    return picked


def activity_hours(db: OrmSession, ids: list[str] | None = None
                   ) -> dict[str, tuple[float, float]]:
    """各活动的（志愿时数, 劳务时数）：每一笔按结算口径算完再按活动相加。

    两半按笔在 SQL 里求和（段上的 record_id 有索引），结算走
    HoursRecord.settle_sums 一处：两处各写一个取整，界面上看到的和导出去的
    迟早差 0.5。ids 为 None 就是全部活动。
    """
    is_pay = and_(HoursSegment.kind == "shift", HoursSegment.reward == "pay")
    q = (db.query(HoursRecord.activity_id,
                  func.coalesce(func.sum(HoursSegment.hours).filter(~is_pay), 0.0),
                  func.coalesce(func.sum(HoursSegment.hours).filter(is_pay), 0.0))
         .join(HoursSegment, HoursSegment.record_id == HoursRecord.id)
         .group_by(HoursRecord.id, HoursRecord.activity_id))
    if ids is not None:
        if not ids:
            return {}
        q = q.filter(HoursRecord.activity_id.in_(ids))
    out: dict[str, tuple[float, float]] = {}
    for aid, volunteer, paid in q:
        counted, pay = HoursRecord.settle_sums(volunteer, paid)
        have = out.get(aid, (0.0, 0.0))
        out[aid] = (have[0] + counted, have[1] + pay)
    return out


def build_activities_summary_workbook(
        db: OrmSession, start: datetime | None = None,
        end: datetime | None = None) -> tuple[BytesIO, int]:
    """活动级汇总：一场活动一行，末行合计，表底口径说明，给校青协做统计。

    进表的是 SUMMARY_STATUSES 那几个状态的活动，含不公开招募（补记录）活动：
    它们没有场次、不收报名，时长靠导入名单，统计时同样算数。
    区间按活动**结束时间**落在里头算（end 含当天整天，见 activities_ending_in），
    这场活动的人数、人次、时长整场都算在这一行。
    人数是报名记录按人去重后的个数，人次是报名记录的条数——一个人报两个场次，
    人数算一个、人次算两个。补记录活动没有场次、不收报名，人数与人次都是 0，
    只有时长。
    归口审批部门、活动地点、招募范围、违规条数四列都取库里现成的值，缺的写
    SUMMARY_EMPTY（见 SUMMARY_NOTES 的口径）。
    返回工作簿和进表的活动数：条数要写进导出留痕。
    """
    before = end + timedelta(days=1) if end is not None else None
    picked = activities_ending_in(db, start=start, before=before)
    ids = [act.id for _span, act in picked]
    hours = activity_hours(db, ids)
    counts: dict[str, tuple[int, int]] = {}
    depts: dict[str, str] = {}
    shift_locs: dict[str, str] = {}
    position_locs: dict[str, str] = {}
    violations: dict[str, int] = {}
    if ids:
        dept_ids = {a.approval_dept_id for _s, a in picked if a.approval_dept_id}
        if dept_ids:
            depts = dict(db.query(Department.id, Department.name)
                         .filter(Department.id.in_(dept_ids)))
        # 活动地点缺了往下找：先服务场次的地点，再岗位的地点。培训/彩排的地点
        # 是筹备的地方，不是这场活动办在哪，不参与。都是批量取，不逐场去查。
        for aid, loc in (db.query(Shift.activity_id, Shift.location)
                         .filter(Shift.activity_id.in_(ids),
                                 Shift.kind == "service")
                         .order_by(Shift.sort, Shift.start_time)):
            if loc.strip() and aid not in shift_locs:
                shift_locs[aid] = loc.strip()
        for aid, loc in (db.query(Shift.activity_id, Position.location)
                         .join(Position, Position.shift_id == Shift.id)
                         .filter(Shift.activity_id.in_(ids),
                                 Shift.kind == "service")
                         .order_by(Shift.sort, Position.sort)):
            if loc.strip() and aid not in position_locs:
                position_locs[aid] = loc.strip()
        violations = dict(db.query(ViolationRecord.activity_id,
                                   func.count(ViolationRecord.id))
                          .filter(ViolationRecord.activity_id.in_(ids))
                          .group_by(ViolationRecord.activity_id))
        counts = {aid: (people, seats) for aid, people, seats in
                  db.query(Enrollment.activity_id,
                           func.count(func.distinct(Enrollment.user_id)),
                           func.count(Enrollment.id))
                  .join(Shift, Shift.id == Enrollment.shift_id)
                  .filter(Enrollment.activity_id.in_(ids),
                          Enrollment.status == "enrolled",
                          # 只数服务场次：培训/彩排的报名是跟着服务场次自动挂上的，
                          # 同一个人会被再算一次
                          Shift.kind == "service")
                  .group_by(Enrollment.activity_id)}

    wb = Workbook()
    ws = wb.active
    ws.title = "活动汇总"
    ws.append(list(SUMMARY_HEADER))
    _style_header(ws, len(SUMMARY_HEADER))
    seats_total, volunteer_total, pay_total, bad_total = 0, 0.0, 0.0, 0
    for span, act in picked:
        people, seats = counts.get(act.id, (0, 0))
        counted, paid = hours.get(act.id, (0.0, 0.0))
        # 状态列与活动管理页同一套口径（见 serializers.admin_status_of）：
        # 拿表的人照着状态与页面上的对，两边说得不一样就要来回问
        label = admin_status_of(act.status, act.reg_start, act.reg_end,
                                span, act.last_shift_start())[1]
        place = (act.location.strip() or shift_locs.get(act.id)
                 or position_locs.get(act.id) or SUMMARY_EMPTY)
        bad = violations.get(act.id, 0)
        ws.append([act.id, act.title, act.organizer, act.co_organizer or "",
                   depts.get(act.approval_dept_id or "", SUMMARY_EMPTY),
                   days_text(*span), label, place, recruit_scope_text(act),
                   people, seats, round(counted, 1), round(paid, 1), bad])
        seats_total += seats
        volunteer_total += counted
        pay_total += paid
        bad_total += bad
    # 合计行：人数不去重就没有意义，只合计人次、时数与违规条数。其余格子留空，
    # 按列名落位——列增删时不必数格子对不对得上
    total = [""] * len(SUMMARY_HEADER)
    total[0] = "合计"
    for name, value in (("人次", seats_total),
                        ("总志愿时数", round(volunteer_total, 1)),
                        ("劳务时数", round(pay_total, 1)),
                        ("违规条数", bad_total)):
        total[SUMMARY_HEADER.index(name)] = value
    ws.append(total)
    # 口径说明落在表底：合计行下面空一行，再逐行写 SUMMARY_NOTES
    note_row = ws.max_row + 2
    for i, line in enumerate(SUMMARY_NOTES):
        ws.cell(row=note_row + i, column=1, value=line)
    for col, w in zip("ABCDEFGHIJKLMN",
                      (18, 32, 20, 20, 16, 26, 12, 24, 30, 12, 8, 10, 10, 10)):
        ws.column_dimensions[col].width = w
    disarm_formulas(wb)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf, len(picked)
