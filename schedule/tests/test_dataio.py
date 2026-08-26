"""表格读写。算法不重测，这里只盯搬迁时真正改掉的那件事：**不碰磁盘**。"""
from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

from app.core import Project, identity_key
from app.dataio import (export_xlsx, import_positions, import_signups,
                        sample_xlsx)


def to_xlsx(rows) -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in rows:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


POSITIONS = [
    ["岗位ID", "岗位名称", "时间说明", "名额", "要求"],
    ["P01", "检录A组", "8月1日上午", 2, ""],
    ["P02", "颁奖礼仪", "8月1日下午", 1, "性别=女"],
]

SIGNUPS = [
    ["提交答卷时间", "姓名", "身份证号", "联系方式", "微信", "性别", "P01", "P02"],
    ["2026-07-20 09:01", "张三", "110101200501011234", "13800000001", "zs", "男", 1, ""],
    ["2026-07-20 09:03", "李四", "110101200502022345", "13800000002", "", "女", 1, 1],
]


def loaded() -> Project:
    prj = Project()
    assert import_positions(prj, to_xlsx(POSITIONS), "岗位.xlsx") == []
    added, warns = import_signups(prj, to_xlsx(SIGNUPS), "报名.xlsx")
    assert added == 3, warns
    return prj


def test_从字节读xlsx_不需要落盘():
    prj = loaded()
    assert list(prj.positions) == ["P01", "P02"]
    # 「要求」列不再解析成条件，只当参照原样存下来（见 RULES.md 6.1）。
    assert prj.positions["P02"].req_note == "性别=女"
    assert prj.positions["P02"].requirements == {"all": []}
    assert len(prj.volunteers) == 2
    # 标签列（性别）被认出来了，岗位要求才比得上。
    # 用 identity_key() 而不是手写「姓名+联系方式」：它中间有个不可见的分隔符，
    # 正是为了让「张三1 + 3800…」和「张三 + 13800…」不会撞成同一个人。
    assert prj.volunteers[identity_key("李四", "13800000002")].tags == {"性别": "女"}


def test_从字节读csv_并且认得gbk():
    """问卷平台导出的 CSV 十有八九是 GBK，读不出来就整份表都没了。"""
    text = "\n".join(",".join(str(c) for c in row) for row in POSITIONS)
    prj = Project()
    assert import_positions(prj, text.encode("gbk"), "岗位.csv") == []
    assert prj.positions["P01"].name == "检录A组"


def test_导出的是字节_服务器上不留文件():
    prj = loaded()
    data = export_xlsx(prj)
    assert data[:2] == b"PK"           # xlsx 就是个 zip
    wb = load_workbook(io.BytesIO(data))
    assert wb.sheetnames == ["排班表", "候补队列", "岗位统计", "个人班表", "变更表"]


def test_示例表能生成且两张sheet都在():
    wb = load_workbook(io.BytesIO(sample_xlsx()))
    assert wb.sheetnames == ["岗位表", "报名表"]
    # 身份证号必须按文本存，否则 Excel 会显示成科学计数法
    ws = wb["报名表"]
    assert ws.cell(row=2, column=3).number_format == "@"


def test_没填联系方式的行会被跳过并说明原因():
    """姓名+联系方式是认人的唯一依据。少了它，两个同名的人会被合并成一个。"""
    rows = [r[:] for r in SIGNUPS]
    rows.append(["2026-07-20 10:00", "王五", "", "", "", "男", 1, ""])
    prj = Project()
    import_positions(prj, to_xlsx(POSITIONS), "岗位.xlsx")
    added, warns = import_signups(prj, to_xlsx(rows), "报名.xlsx")
    assert added == 3
    assert any("联系方式" in w for w in warns)
    assert "王五" not in {v.name for v in prj.volunteers.values()}


def test_旧版xls给一句人话():
    prj = Project()
    try:
        import_positions(prj, b"whatever", "岗位.xls")
    except ValueError as exc:
        assert "另存为" in str(exc)
    else:
        raise AssertionError("应该拒掉 .xls")


# ————————————————————————————————————————— 重新导入的语义

SIGNUPS_V2 = [
    ["提交答卷时间", "姓名", "身份证号", "联系方式", "微信", "性别", "P01", "P02"],
    # 张三改主意：只报 P02 了（原来报的是 P01）
    ["2026-07-20 10:00", "张三", "110101200501011234", "13800000001", "zs", "男", "", 1],
]


def test_重新导入时报名岗位以最新一份为准():
    """并集语义下「我改主意了，只想报 P02」这件事永远做不到——旧的会一直粘着。

    而个人资料那几个字段本来就是覆盖的，同一次导入里两种语义会让人彻底搞不清。
    """
    prj = loaded()
    zhang = identity_key("张三", "13800000001")
    assert sorted(s.pid for s in prj.signups if s.vol_key == zhang) == ["P01"]

    import_signups(prj, to_xlsx(SIGNUPS_V2), "报名2.xlsx")
    assert sorted(s.pid for s in prj.signups if s.vol_key == zhang) == ["P02"]


def test_没出现在新表里的人不受影响():
    prj = loaded()
    li = identity_key("李四", "13800000002")
    before = sorted(s.pid for s in prj.signups if s.vol_key == li)
    import_signups(prj, to_xlsx(SIGNUPS_V2), "报名2.xlsx")
    assert sorted(s.pid for s in prj.signups if s.vol_key == li) == before


def test_撤销报名会连带撤掉自动排班_但锁定的留着():
    """人工调岗本来就允许把人排进他没报名的岗位（带 forced 标记），
    悄悄删掉等于把人家的手工决定抹了。所以只删 AUTO，LOCKED 留着并警告。
    """
    from app.core import Assignment, solve

    prj = loaded()
    zhang = identity_key("张三", "13800000001")
    prj.assignments = solve(prj)
    assert any(a.pid == "P01" and a.vol_key == zhang for a in prj.assignments)

    _added, warns = import_signups(prj, to_xlsx(SIGNUPS_V2), "报名2.xlsx")
    assert not any(a.pid == "P01" and a.vol_key == zhang for a in prj.assignments)
    assert any("撤掉了这些自动排班" in w for w in warns)

    # 换成锁定的：撤了报名也不动它，只警告
    prj2 = loaded()
    prj2.assignments = [Assignment("P01", zhang, "LOCKED", False)]
    _added, warns2 = import_signups(prj2, to_xlsx(SIGNUPS_V2), "报名2.xlsx")
    assert [(a.pid, a.status) for a in prj2.assignments] == [("P01", "LOCKED")]
    assert any("已锁定" in w for w in warns2)


def test_认不出来的报名时间会被报出来():
    """认不出来的人会被排到所有人后面，而界面上看不出任何异样——
    这是最难发现的一类不公平，所以导入时就得说。
    """
    rows = [
        ["提交答卷时间", "姓名", "联系方式", "P01", "P02"],
        ["七月二十号那天", "王五", "13800000003", 1, ""],
    ]
    prj = loaded()
    _added, warns = import_signups(prj, to_xlsx(rows), "报名3.xlsx")
    assert any("认不出来" in w and "王五" in w for w in warns)


# ————————————————————————————————————————— 导出：标签铺成一列一个

def test_排班表把标签铺成每个标签一列():
    """挤成「性别=女;年级=2023」一格的话，Excel 里没法按标签筛选、排序、做透视，
    而那正是拿到这张表之后最常干的事。
    """
    from app.core import solve
    from app.dataio import schedule_header, schedule_rows, tag_columns

    prj = loaded()
    prj.assignments = solve(prj)

    assert tag_columns(prj) == ["性别"]
    header = schedule_header(prj)
    assert "标签" not in header, "打包成一格的那列该没了"
    assert header.index("性别") == header.index("微信") + 1, "标签插在微信和状态之间"
    assert header[header.index("性别") + 1] == "状态"

    # 值落在对的那一列上
    at = header.index("性别")
    by_name = {r[header.index("姓名")]: r[at] for r in schedule_rows(prj)}
    assert by_name["李四"] == "女"
    assert by_name["张三"] == "男"


def test_每一行的列数都和表头对齐():
    """一行少一格，后面所有列都会串位——而串位的表看起来是「有数据」的，
    只是姓名列里装着微信号。这是这张表最容易坏的地方。
    """
    from app.core import Volunteer, solve
    from app.dataio import schedule_header, schedule_rows

    prj = loaded()
    # 掺一个标签完全没填的人，再掺一个带独有标签的人
    key = identity_key("王五", "13800000003")
    prj.volunteers[key] = Volunteer(key=key, name="王五", contact="13800000003")
    prj.add_signup(key, "P01", "2026-07-20 09:05")
    key2 = identity_key("赵六", "13800000004")
    prj.volunteers[key2] = Volunteer(key=key2, name="赵六", contact="13800000004",
                                     tags={"年级": "2023"})
    prj.add_signup(key2, "P01", "2026-07-20 09:06")
    prj.assignments = solve(prj)

    width = len(schedule_header(prj))
    for row in schedule_rows(prj):
        assert len(row) == width, f"这一行 {len(row)} 格，表头 {width} 格"


def test_标签名撞上固定列会加前缀区分():
    """报名表里除固定字段和岗位列之外全当标签，所以真会冒出一个叫「状态」的标签。
    不区分的话导出表里会有两个同名列头，做透视时挑中哪个全看运气。
    """
    from app.core import Volunteer
    from app.dataio import schedule_header

    prj = loaded()
    key = identity_key("王五", "13800000003")
    prj.volunteers[key] = Volunteer(key=key, name="王五", contact="13800000003",
                                    tags={"状态": "在校"})
    header = schedule_header(prj)
    assert "标签·状态" in header
    assert header.count("状态") == 1, "固定的那个「状态」列还在，且只有一个"


def test_没有任何标签时不多出空列():
    from app.core import Project
    from app.dataio import schedule_header

    prj = Project()
    import_positions(prj, to_xlsx(POSITIONS), "岗位.xlsx")
    header = schedule_header(prj)
    assert header[header.index("微信") + 1] == "状态"


def test_排班表同角色内按报名先后排():
    """排班本身就是先到先得排出来的，导出跟着同一个顺序，
    拿它跟报名表对着看才对得上。组长仍然置顶。
    """
    from app.core import LEADER, Volunteer, solve
    from app.dataio import schedule_header, schedule_rows

    prj = loaded()
    # 再塞两个报 P01 的人，时间故意和拼音序反着来
    for name, contact, when in (("安一", "13800000009", "2026-07-20 23:00"),
                                ("周九", "13800000008", "2026-07-20 09:02")):
        key = identity_key(name, contact)
        prj.volunteers[key] = Volunteer(key=key, name=name, contact=contact)
        prj.add_signup(key, "P01", when)
    prj.positions["P01"].quota = 4
    prj.assignments = solve(prj)
    # 把最晚报名的那个设成组长，验证「组长置顶」压过时间
    for a in prj.assignments:
        if a.pid == "P01" and a.vol_key == identity_key("安一", "13800000009"):
            a.role = LEADER

    header = schedule_header(prj)
    at_name, at_time = header.index("姓名"), header.index("提交答卷时间")
    p01 = [r for r in schedule_rows(prj) if r[0] == "P01"]

    assert p01[0][at_name] == "安一", "组长该在第一行，哪怕他报得最晚"
    rest = [(r[at_name], r[at_time]) for r in p01[1:]]
    assert [t for _n, t in rest] == sorted(t for _n, t in rest), f"组员没按报名先后排：{rest}"
    assert rest[0][0] == "张三"       # 09:01
    assert rest[1][0] == "周九"       # 09:02，拼音在张三之后，时间在前
