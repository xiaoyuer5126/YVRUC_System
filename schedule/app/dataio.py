"""表格的读与写。

从桌面版的 dataio.py 搬来,改动只有一处但是贯穿全文件:**不碰磁盘**。

- 读:收 `bytes` + 文件名(用来判 csv/xlsx),不再收路径。上传的报名表里有
  身份证号和联系方式,解析完原始字节立刻丢掉,不落盘、不入库(入库的是解析后的
  项目结构,那是另一回事,见 models.py);
- 写:吐 `bytes`,由接口层直接当附件回给浏览器,服务器上不留文件;
- 项目文件那一套(save_project/load_project)整个删掉——项目现在存在数据库里,
  `Project.to_dict()/from_dict()` 原样复用,序列化格式一个字没改。
"""
from __future__ import annotations

import csv
import datetime as dt
import io
import os

from openpyxl import Workbook, load_workbook

from app.conditions import describe
from app.core import (LEADER, LOCKED, Position, Project, Volunteer, build_queue,
                      diff_pairs, format_kv, identity_key, parse_time,
                      position_stats)

# ======================= 读取 =======================

_ENCODINGS = ("utf-8-sig", "gbk", "utf-8")


def read_rows(data: bytes, filename: str = "") -> list[list[str]]:
    """把上传的表格读成行列表(全字符串)。

    .xlsx 走 openpyxl,其余按 CSV 处理并自动尝试编码——问卷平台导出的 CSV
    十有八九是 GBK。
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext == ".xls":
        raise ValueError("不支持旧版 .xls,请在 Excel 里另存为 .xlsx 或 CSV")
    if ext in (".xlsx", ".xlsm"):
        return read_xlsx_rows(data)
    last = None
    for enc in _ENCODINGS:
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError as exc:
            last = exc
    else:
        raise ValueError(f"无法识别文件编码({last})。请另存为 UTF-8 的 CSV 或 .xlsx")
    return [row for row in csv.reader(io.StringIO(text))]


def _cell_str(v) -> str:
    """把 Excel 单元格值转成与 CSV 一致的字符串。"""
    if v is None:
        return ""
    if isinstance(v, dt.datetime):
        return f"{v:%Y-%m-%d %H:%M:%S}"
    if isinstance(v, dt.date):
        return f"{v:%Y-%m-%d}"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))          # 身份证号/名额别显示成 3.0
    return str(v).strip()


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    try:
        ws = wb.worksheets[0]
        return [[_cell_str(c) for c in row] for row in ws.iter_rows(values_only=True)]
    finally:
        wb.close()


def _norm(h):
    return (h or "").strip().strip("*＊ ").lower()


def _index_headers(header_row, aliases):
    """返回 {字段名: 列号}。"""
    idx = {}
    for col, h in enumerate(header_row):
        n = _norm(h)
        for field_name, names in aliases.items():
            if field_name not in idx and n in names:
                idx[field_name] = col
    return idx


def _get(row, idx, field_name):
    c = idx.get(field_name)
    if c is None or c >= len(row):
        return ""
    return (row[c] or "").strip()


POS_ALIASES = {
    "pid": {"岗位id", "id", "编号", "岗位编号"},
    "name": {"岗位名称", "名称", "岗位", "name"},
    "time_note": {"时间说明", "时间", "时段", "time"},
    "quota": {"名额", "人数", "需求人数", "quota"},
    "min_quota": {"最低人数", "保底人数", "最少人数", "下限"},
    "leader_count": {"组长人数", "小组长人数", "组长", "小组长"},
    # 「要求」列**不解析**,只当参照存下来。在 Excel 里写条件的人看不到标签的
    # 实际取值,写出来的十有八九对不上真实数据(写「性别=女」而表里是「女生」);
    # 而主办方实际会写的是自然语言(「女生优先,最好有急救证」),本来就不该被
    # 解析。条件在网页上点,那里能看见值域。见 RULES.md 6.1。
    "req_note": {"要求", "岗位要求", "requirements", "保底要求", "备注"},
}

SIGNUP_ALIASES = {
    "time": {"提交答卷时间", "报名时间", "提交时间", "时间", "填写时间"},
    "name": {"姓名", "名字", "name"},
    "sid": {"身份证号", "身份证", "身份证号码", "证件号", "证件号码"},
    "contact": {"联系方式", "电话", "手机", "手机号", "联系电话"},
    "wechat": {"微信", "微信号", "微信id", "wechat", "vx", "wx"},
}

# 宽表志愿列 0/1 取值:这些(小写)算「没报名」,其余非空一律算「报名」
_FALSY = {"", "0", "0.0", "否", "n", "no", "false", "x", "×", "无", "空",
          "na", "n/a", "-", "/", "／"}


def _truthy(v):
    """宽表志愿单元格是否表示「报名」:1/是/√ 等为真,0/空/否 等为假。"""
    return (v or "").strip().lower() not in _FALSY


def _number_cell(raw: str, label: str, where: str, warnings: list) -> int:
    if not raw:
        return 0
    try:
        return max(0, int(float(raw)))
    except ValueError:
        warnings.append(f"{where}:{label}「{raw}」不是数字,按 0 处理")
        return 0


def import_positions(project: Project, data: bytes, filename: str = ""):
    """导入岗位表。返回警告列表。

    冲突岗位、同批岗位都不在表里填,导入后在页面的矩阵里勾选。

    **已存在的岗位按字段合并,不整体覆盖。** 条件是在网页上一条条点出来的,
    重新导入一次岗位表(改了个名额、加了一行)就把它们冲掉,是没法接受的——
    表里有的字段更新,`requirements` / `preferences` / `leader` 原样保留。
    """
    rows = read_rows(data, filename)
    warnings = []
    if not rows:
        return ["文件为空"]
    idx = _index_headers(rows[0], POS_ALIASES)
    if "pid" not in idx and "name" not in idx:
        return ["未找到「岗位ID」或「岗位名称」列,请检查表头"]

    seen = set()
    for r, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        pid = _get(row, idx, "pid") or _get(row, idx, "name")
        if not pid:
            warnings.append(f"第{r}行:无岗位ID/名称,跳过")
            continue
        where = f"第{r}行(岗位{pid})"
        pos = project.positions.get(pid)
        if pos is None:
            pos = Position(pid=pid)
            project.positions[pid] = pos
        elif pid in seen:
            warnings.append(f"第{r}行:岗位「{pid}」在表里重复,以后出现的为准")
        seen.add(pid)

        # 表里没有这一列 = 不改这个字段,而不是清零。少一列就把名额抹成 0
        # 是最难查的那种数据丢失:表面上导入成功了,岗位还在,只是谁也排不进去。
        def cell(field, label, current):
            if field not in idx:
                return current
            return _number_cell(_get(row, idx, field), label, where, warnings)

        quota = cell("quota", "名额", pos.quota)
        min_quota = cell("min_quota", "最低人数", pos.min_quota)
        leader_count = cell("leader_count", "组长人数", pos.leader.count)

        # 这两条在表里夹住而不是拒掉:一行填错不该让整份表导不进来。
        # (网页上改成这样会被当场拒——那是人一个个填的,拒得起。)
        if min_quota > quota:
            warnings.append(f"{where}:最低人数 {min_quota} 超过名额 {quota},按 {quota} 处理")
            min_quota = quota
        if leader_count > quota:
            warnings.append(f"{where}:组长人数 {leader_count} 超过名额 {quota},按 {quota} 处理")
            leader_count = quota

        pos.name = _get(row, idx, "name") or pos.name or pid
        pos.quota = quota
        pos.min_quota = min_quota
        pos.leader.count = leader_count
        if "time_note" in idx:
            pos.time_note = _get(row, idx, "time_note")
        if "req_note" in idx:
            pos.req_note = _get(row, idx, "req_note")
    return warnings


def import_signups(project: Project, data: bytes, filename: str = ""):
    """导入报名表(一行一人)。

    志愿岗位用宽表列表示:每个岗位一列,表头就是岗位ID,值为 1 表示报名,
    0/留空 表示未报名(需先导入岗位表,否则无法识别哪些列是岗位)。
    标签也用列表示:表头是标签名、单元格是值(如表头「性别」、值「女」),
    除固定字段列和岗位列之外的所有列都当作标签列。返回 (新增条数, 警告)。
    """
    rows = read_rows(data, filename)
    warnings = []
    if not rows:
        return 0, ["文件为空"]
    header = rows[0]
    idx = _index_headers(header, SIGNUP_ALIASES)
    if "name" not in idx:
        return 0, ["未找到「姓名」列,请检查表头"]
    if "contact" not in idx:
        return 0, ["未找到「联系方式」列。姓名+联系方式是识别「同一个人」的依据,"
                   "这一列必须有,且每个人都要填。"]

    # 宽表志愿列:表头就是岗位ID(不匹配岗位名称),且未被固定字段占用
    used = set(idx.values())
    by_id = {pid.strip().lower(): pid for pid in project.positions}
    wide_cols = {}  # 列号 -> pid
    for c, h in enumerate(header):
        if c not in used and by_id.get(_norm(h)):
            wide_cols[c] = by_id[_norm(h)]

    if not wide_cols:
        return 0, ["没有找到岗位列。报名表需要每个岗位一列,表头写岗位ID"
                   "(如 P01),值 1 表示报名、0 或留空表示没报名;"
                   "并且要先导入岗位表,程序才知道哪些列是岗位。"]

    # 表头写错时,该岗位会整列丢失且不易察觉,这里明确提示
    missing = [pid for pid in project.positions if pid not in wide_cols.values()]
    if missing:
        warnings.append("这些岗位在报名表里没有对应列(没人能报名,请检查表头是否写错):"
                        + "、".join(missing))

    # 标签列:剩下的列,表头即标签名、单元格即该标签的值
    tag_cols = {}  # 列号 -> 标签名
    for c, h in enumerate(header):
        if c in used or c in wide_cols:
            continue
        label = (h or "").strip().strip("*＊ ")
        if label:
            tag_cols[c] = label
    replace_tags = bool(tag_cols)   # 表里带标签列时,标签以最后一次报名为准

    # 先按身份(姓名+联系方式)把所有行归并;同一个人出现多次时,
    # 只保留「最后一次」——报名时间最晚的一行,时间无法解析则取文件中靠后者。
    records = {}   # identity -> (排序键, 记录)
    order = 0
    for r, row in enumerate(rows[1:], start=2):
        if not any(c.strip() for c in row):
            continue
        name = _get(row, idx, "name")
        if not name:
            warnings.append(f"第{r}行:无姓名,跳过")
            continue
        contact = _get(row, idx, "contact")
        if not contact:
            warnings.append(f"第{r}行({name}):没填联系方式,无法确定是不是同一个人,"
                            f"跳过该行")
            continue
        order += 1
        ident = identity_key(name, contact)

        tags = {}
        for c, label in tag_cols.items():
            cell = (row[c] if c < len(row) else "").strip()
            if cell:
                tags[label] = cell

        chosen = []
        for c, pid in wide_cols.items():     # 宽表 0/1
            cell = row[c] if c < len(row) else ""
            if _truthy(cell):
                chosen.append(pid)

        time_raw = _get(row, idx, "time")
        sortkey = (parse_time(time_raw) or dt.datetime.min, order)
        rec = {"name": name, "contact": contact, "sid": _get(row, idx, "sid"),
               "wechat": _get(row, idx, "wechat"), "tags": tags,
               "chosen": chosen, "time": time_raw}
        prev = records.get(ident)
        if prev is None or sortkey >= prev[0]:
            records[ident] = (sortkey, rec)

    # 按报名时间从早到晚应用,保持「先到先得」的入库顺序。
    #
    # **报名岗位是覆盖语义**:表里这个人勾了什么,他的报名就是什么。取并集的话,
    # 「我改主意了,只想报 P03」这件事永远做不到——旧的 P01、P02 会一直粘着,
    # 而个人资料那几个字段又是覆盖的,同一次导入里两种语义会让人彻底搞不清。
    # 没出现在这份表里的人不受影响。
    #
    # 撤掉的报名会连带撤掉对应的**自动**排班;LOCKED 那些留着并单独警告——
    # 人工调岗本来就允许把人排进他没报名的岗位(带 forced 标记),
    # 悄悄删掉等于把人家的手工决定抹了。
    added = removed = 0
    dropped_auto, kept_locked = [], []
    bad_time = []
    for ident, (_sk, rec) in sorted(records.items(), key=lambda kv: kv[1][0]):
        vol = project.merge_volunteer(Volunteer(
            key=ident, name=rec["name"], id_card=rec["sid"],
            contact=rec["contact"], wechat=rec["wechat"],
            tags=dict(rec["tags"])))
        if replace_tags:
            vol.tags = dict(rec["tags"])
        if rec["time"] and parse_time(rec["time"]) is None:
            bad_time.append(f"{rec['name']}（{rec['time']}）")

        chosen = set(rec["chosen"])
        for pid in {s.pid for s in project.signups if s.vol_key == vol.key} - chosen:
            project.remove_signup(vol.key, pid)
            removed += 1
            for a in [x for x in project.assignments
                      if x.vol_key == vol.key and x.pid == pid]:
                if a.status == LOCKED:
                    kept_locked.append(f"{rec['name']}→{pid}")
                else:
                    project.assignments.remove(a)
                    dropped_auto.append(f"{rec['name']}→{pid}")
        for pid in rec["chosen"]:
            if project.add_signup(vol.key, pid, rec["time"]):
                added += 1

    if removed:
        warnings.append(f"有 {removed} 条旧报名在新表里没有勾选，已撤销"
                        f"（报名岗位以最新一份表为准）。")
    if dropped_auto:
        warnings.append("连带撤掉了这些自动排班：" + _brief(dropped_auto))
    if kept_locked:
        warnings.append("这些排班已锁定，虽然报名撤销了但**没有删**，"
                        "要去掉请到排班页手动移除：" + _brief(kept_locked))
    if bad_time:
        warnings.append(f"这 {len(bad_time)} 个人的报名时间认不出来，"
                        f"先到先得时会被排在所有人后面：" + _brief(bad_time))
    return added, warnings


def _brief(items, limit=8):
    """警告里只举几个例子——列全了没人看,一个不举又不知道去查谁。"""
    head = "、".join(items[:limit])
    return head + (f" 等 {len(items)} 处" if len(items) > limit else "")


# ======================= 导出 =======================

# 排班表的固定列。标签**不在这里**——它铺成动态列插在「微信」和「状态」之间,
# 见 tag_columns / schedule_header。
SCHEDULE_HEAD_FIXED = ["岗位ID", "岗位名称", "时间说明", "名额", "已排/名额", "序号",
                       "角色", "姓名", "身份证号", "联系方式", "微信"]
SCHEDULE_TAIL_FIXED = ["状态", "强制标记", "提交答卷时间"]


def tag_columns(project: Project) -> list[str]:
    """所有出现过的标签名,按**第一次出现的顺序**。

    不排序:`volunteers` 是有序字典,第一次出现的顺序就是报名表里的列序,
    而人核对导出结果时是拿它跟原始报名表对着看的。按拼音重排只会让两边对不上。
    """
    seen = []
    for vol in project.volunteers.values():
        for tag in (vol.tags or {}):
            if tag not in seen:
                seen.append(tag)
    return seen


def _tag_headers(tags: list[str]) -> list[str]:
    """标签的表头。和固定列**撞名**的加个前缀区分开。

    撞得上:报名表里除了固定字段列和岗位列,剩下的全当标签,所以一个叫「状态」
    或者「序号」的问卷列会变成标签。真撞了而不加区分的话,导出的表里会出现两个
    同名列头,拿去做数据透视时挑中哪个全看运气。
    """
    fixed = set(SCHEDULE_HEAD_FIXED) | set(SCHEDULE_TAIL_FIXED)
    return [f"标签·{t}" if t in fixed else t for t in tags]


def schedule_header(project: Project) -> list[str]:
    return SCHEDULE_HEAD_FIXED + _tag_headers(tag_columns(project)) + SCHEDULE_TAIL_FIXED

QUEUE_HEADER = ["岗位ID", "岗位名称", "候补排位", "姓名", "身份证号", "联系方式",
                "微信", "标签", "提交答卷时间", "已获岗位数", "原因"]


# ======================= 导出的排序 =======================
#
# ⚠️ **这一段只管导出的 xlsx，不影响网页上的显示。**
#
# 两者的读者不一样，要的顺序也不一样：
#
# - 网页是**工作台**。岗位按导入表的原始行序排，因为经办人是对着那张表在核对；
#   候补队列按求解器的轮次顺序排，因为「你是第几位候补」必须和实际排班同一套
#   判据，否则是骗人。这两条都在 core.position_stats / core.build_queue 里，
#   **别去动它们**——那是网页和求解器共用的。
# - 导出的 xlsx 是**成品**，要打印、要群发、要给人按名字找自己。所以这里重新排：
#   岗位按 ID、变更按类型分组。排班表里**人按报名先后**（组长仍然置顶）——
#   排班本身就是先到先得排出来的，导出的表跟着同一个顺序，拿它跟报名表对着看
#   才对得上；拼音只当平局兜底。其余几张表仍按拼音，那些是拿来找人的。
#
# 排序只发生在 export_xlsx 里，上面那两个函数的返回值一个字不改。


def pinyin_key(name: str) -> tuple:
    """姓名的拼音排序键。

    不能用 Python 默认的字符串序：那是按 Unicode 码点比，「张三」会排在
    「李四」前面（张 U+5F20 < 李 U+674E），打印出来看就是没排过。

    末尾垫上原文：同音不同字（「张三」/「章三」）要有个稳定的先后，
    否则两次导出的行序可能不一样，diff 起来全是噪声。
    """
    from pypinyin import Style, lazy_pinyin

    return (lazy_pinyin(name or "", style=Style.NORMAL), name or "")


def _pid_key(pid: str) -> tuple:
    """岗位 ID 的排序键：字母段按字母、数字段按数值。

    直接按字符串排的话 `P10` 会插到 `P2` 前面。拆成 (前缀, 数字, 原文) 之后
    P2 < P10，而 T 组和 P 组仍然各自成块。认不出数字的原样垫底。
    """
    import re

    m = re.match(r"^([^\d]*)(\d+)(.*)$", pid or "")
    if m:
        return (m.group(1), 0, int(m.group(2)), m.group(3))
    return (pid or "", 1, 0, "")


# 变更表的分组顺序。先看新增了谁，再看谁换了岗，最后看谁被移除——
# 这也是经办人通知人的顺序：新增的要发通知，换岗的要改通知，移除的要道歉。
_DIFF_ORDER = {"新增": 0, "换岗": 1, "移除": 2}


def schedule_rows(project: Project):
    """排班表(按岗位分组)。**导出专用**,排序见本文件「导出的排序」那一段。

    岗位按 ID 排;岗位内先组长后组员,同角色按拼音。

    组长排在前面是因为这张表会打印出来当现场花名册——找组长是最高频的动作,
    让他固定在每一组的第一行,比藏在按拼音排的中间好找得多。

    **标签一个一列**,不是挤成「性别=女;年级=2023」一格。挤成一格的话,
    Excel 里没法按标签筛选、排序、做透视——而那正是拿到这张表之后最常干的事
    (「把所有女生挑出来」「按年级分组点名」)。列的顺序和条数由 `tag_columns` 定,
    每一行都必须和它对齐,所以这两个函数得一起改。
    """
    rows = []
    tags = tag_columns(project)
    desired = project.desired_map()
    for pid in sorted(project.positions, key=_pid_key):
        pos = project.positions[pid]
        items = [a for a in project.assignments if a.pid == pid]

        def _rowkey(a, pid=pid):
            v = project.volunteers.get(a.vol_key)
            it = desired.get(a.vol_key, {}).get(pid)
            # 组长 0、组员 1:先组长后组员;同角色内按**报名先后**。
            #
            # 之前是按拼音排的,改掉是因为这张表最常被用来回答「谁先报的」——
            # 排班本身就是先到先得排出来的,导出的表跟着同一个顺序,拿它跟报名表
            # 对着看才对得上。拼音退成平局兜底(整批导入时时间常常一模一样),
            # 顺便保住确定性。
            #
            # 报名项可能不存在:人工调岗允许把人排进他没报名的岗位。那种排最后。
            return (0 if a.role == LEADER else 1,
                    it.time_key() if it else (2, dt.datetime.max, 0),
                    pinyin_key(v.name if v else a.vol_key))

        items.sort(key=_rowkey)
        filled = len(items)
        for i, a in enumerate(items, start=1):
            v = project.volunteers.get(a.vol_key)
            it = desired.get(a.vol_key, {}).get(pid)
            held = (v.tags or {}) if v else {}
            rows.append([
                pid, pos.name, pos.time_note, pos.quota, f"{filled}/{pos.quota}",
                i, "组长" if a.role == LEADER else "组员",
                v.name if v else a.vol_key,
                v.id_card if v else "", v.contact if v else "",
                v.wechat if v else "",
                # 没填这个标签就留空格,不是跳过——一行少一格,后面所有列都会串位
                *[held.get(t, "") for t in tags],
                "锁定" if a.status == "LOCKED" else "自动",
                "强制" if a.forced else "",
                it.time_raw if it else "",
            ])
    return rows


def queue_rows(project: Project):
    """候补队列。**导出专用**:岗位按 ID 排。

    ⚠️ **岗位内部的顺序绝不能动。** `rank` 就是候补排位,它和求解器共用同一套
    轮次判据(见 core.build_queue),第 1 位就是下一个补上的人。按拼音重排的话,
    表上第一行的人并不是下一个该补的人——那比不排还糟。
    """
    order = {pid: i for i, pid in enumerate(sorted(project.positions, key=_pid_key))}
    rows = sorted(build_queue(project),
                  key=lambda r: (order.get(r["pid"], len(order)), r["rank"]))
    return [[r["pid"], r["pos_name"], r["rank"], r["name"], r["id_card"],
             r["contact"], r["wechat"], format_kv(r["tags"]),
             r["time_raw"], r["count"], r["reason"]]
            for r in rows]


def personal_rows(project: Project):
    """个人班表:一人一行,群发通知用。"""
    by_vol = {}
    for a in project.assignments:
        by_vol.setdefault(a.vol_key, []).append(a.pid)
    rows = []
    for vk, pids in by_vol.items():
        v = project.volunteers.get(vk)
        detail = ";".join(
            f"{p} {project.positions[p].name}({project.positions[p].time_note})"
            if p in project.positions else p for p in pids)
        rows.append([v.name if v else vk, v.id_card if v else "",
                     v.contact if v else "", v.wechat if v else "",
                     len(pids), detail])
    # 岗位数多的在前(要重点核对的就那几个人),同岗位数按拼音——这张表是拿来
    # 挨个通知的,按名字找人才顺手。
    rows.sort(key=lambda r: (-r[4], pinyin_key(r[0])))
    return rows


def export_xlsx(project: Project) -> bytes:
    """5 张 sheet 合并成一个 xlsx,返回字节。

    ⚠️ 这份字节里**有身份证号和联系方式**,是给经办人下载的成品。
    服务器上不留副本——直接当响应体发出去。
    """
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(title, header, rows):
        ws = wb.create_sheet(title)
        ws.append(header)
        for r in rows:
            ws.append(r)

    sheet("排班表", schedule_header(project), schedule_rows(project))
    sheet("候补队列", QUEUE_HEADER, queue_rows(project))

    # position_stats 是网页和求解器共用的,它按岗位表原始行序返回——**不动它**,
    # 只在这里为导出重排一次。
    st = sorted(position_stats(project), key=lambda s: _pid_key(s["pid"]))
    sheet("岗位统计",
          ["岗位ID", "岗位名称", "时间说明", "名额", "最低人数", "已排", "缺口",
           "状态", "组长名额", "已排组长", "必选条件", "优先条件", "组长条件", "表格备注"],
          [[s["pid"], s["name"], s["time_note"], s["quota"], s["min_quota"],
            s["filled"], s["gap"], s["state"], s["leader_quota"], s["leader_filled"],
            describe(project.positions[s["pid"]].requirements) or "不限",
            " > ".join(describe(c) for c in project.positions[s["pid"]].preferences) or "无",
            describe(project.positions[s["pid"]].leader.conditions) or "不限",
            project.positions[s["pid"]].req_note]
           for s in st])

    sheet("个人班表",
          ["姓名", "身份证号", "联系方式", "微信", "岗位数", "岗位明细"],
          personal_rows(project))

    rows, _ = diff_pairs(project.prev_pairs, project.current_pairs())

    def _name(vol_key: str) -> str:
        v = project.volunteers.get(vol_key)
        return v.name if v else vol_key

    # 按类型分组（新增 → 换岗 → 移除），组内按拼音。这是拿着表挨个通知的顺序。
    rows = sorted(rows, key=lambda r: (_DIFF_ORDER.get(r["type"], len(_DIFF_ORDER)),
                                       pinyin_key(_name(r["vol_key"]))))
    sheet("变更表", ["类型", "姓名", "原岗位", "新岗位"],
          [[r["type"], _name(r["vol_key"]), r["from"], r["to"]] for r in rows])

    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


# ======================= 内嵌示例表格 =======================

# 「要求」这一列**不解析**,只是原样存成参照,写自然语言就行——条件在网页上点。
SAMPLE_POSITIONS = [
    ["岗位ID", "岗位名称", "时间说明", "名额", "最低人数", "组长人数", "要求"],
    ["P01", "检录A组", "8月1日 8:00-11:00", 3, 2, 1, "组长最好有过检录经验"],
    ["P02", "检录B组", "8月1日 8:00-11:00", 2, 2, 0, ""],
    ["P03", "物资组", "8月1日 9:00-12:00", 2, 1, 0, "要能搬东西"],
    ["P04", "颁奖礼仪", "8月1日 14:00-16:00", 2, 2, 0, "女生"],
    ["P05", "场地撤收", "8月1日 15:00-18:00", 4, 2, 1, ""],
    ["P06", "机动组", "8月2日 全天", 2, 0, 0, "谁都行,兜底用"],
]

SAMPLE_SIGNUPS = [
    ["提交答卷时间", "姓名", "身份证号", "联系方式", "微信", "性别", "年级",
     "P01", "P02", "P03", "P04", "P05", "P06"],
    ["2026-07-20 09:01", "张三", "110101200501011234", "13800000001",
     "zhangsan_wx", "男", "2023", 1, 1, "", "", 1, ""],
    ["2026-07-20 09:03", "李四", "110101200502022345", "13800000002",
     "", "女", "2023", 1, "", "", 1, "", ""],
    ["2026-07-20 09:10", "王五", "110101200503033456", "13800000003",
     "wangwu", "男", "2024", 1, "", 1, "", "", ""],
    ["2026-07-20 09:15", "赵六", "110101200504044567", "13800000004",
     "", "男", "2024", "", 1, "", "", 1, ""],
    ["2026-07-20 10:02", "孙七", "110101200505055678", "13800000005",
     "sunqi", "男", "2023", 1, "", "", "", 1, 1],
    ["2026-07-20 10:30", "周八", "110101200506066789", "13800000006",
     "", "女", "2025", "", "", 1, 1, "", ""],
    ["2026-07-20 11:00", "吴九", "110101200507077890", "13800000007",
     "wujiu", "男", "2025", 1, "", "", "", "", ""],
    ["2026-07-20 11:45", "郑十", "110101200508088901", "13800000008",
     "", "女", "2023", "", 1, "", "", "", 1],
    ["2026-07-21 08:20", "冯一", "110101200509099012", "13800000009",
     "fengyi", "男", "2024", "", "", "", "", 1, ""],
    ["2026-07-21 09:00", "陈二", "110101200510100123", "13800000010",
     "", "女", "2025", 1, "", "", "", 1, ""],
]


def apply_sample_conditions(project: Project) -> None:
    """给示例项目补上几条真条件。

    条件不从表格解析(见 POS_ALIASES 那段),但示例项目总得让人看见条件长什么样、
    在哪儿改——不然新用户只会看到一列写着自然语言的「要求」,以为它在起作用。
    """
    from app.conditions import from_pairs, normalize

    if "P04" in project.positions:                       # 颁奖礼仪:必选女生
        project.positions["P04"].requirements = from_pairs({"性别": "女"})
    if "P01" in project.positions:                       # 检录A组:优先高年级,组长要 23/24 级
        project.positions["P01"].preferences = [from_pairs({"年级": "2023"})]
        project.positions["P01"].leader.conditions = normalize(
            {"all": [{"tag": "年级", "op": "in", "value": ["2023", "2024"]}]})


def sample_xlsx() -> bytes:
    """示例岗位表 + 报名表,两张 sheet 一个文件。

    示例里的身份证号是编的,别当真——它们只是为了让格式一目了然。
    长数字串按文本存,否则 Excel 会显示成科学计数法。
    """
    wb = Workbook()
    wb.remove(wb.active)
    for title, rows in (("岗位表", SAMPLE_POSITIONS), ("报名表", SAMPLE_SIGNUPS)):
        ws = wb.create_sheet(title)
        for row in rows:
            ws.append(row)
        for col_i, head in enumerate(rows[0], start=1):
            letter = ws.cell(row=1, column=col_i).column_letter
            ws.column_dimensions[letter].width = max(12, len(str(head)) * 2 + 4)
            if head in ("身份证号", "联系方式"):
                for r in range(2, len(rows) + 1):
                    ws.cell(row=r, column=col_i).number_format = "@"
    buffer = io.BytesIO()
    wb.save(buffer)
    return buffer.getvalue()
