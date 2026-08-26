"""导出 xlsx 的行序。

**导出和网页要的顺序不一样，这个文件守的是「改了导出没碰网页」。**

- 网页是工作台：岗位按导入表的原始行序（经办人对着那张表核对），候补队列按
  求解器的轮次顺序（「你是第几位候补」必须和实际排班同一套判据）。
- 导出是成品：要打印、要群发、要让人按名字找自己。所以岗位按 ID、人按拼音、
  变更按类型分组。

排序只写在 `export_xlsx` 那一侧，`core.position_stats` / `core.build_queue`
一个字不改——它们是网页和求解器共用的。
"""
from __future__ import annotations

import io

import pytest
from openpyxl import Workbook, load_workbook
from pypinyin import Style, lazy_pinyin

from app.core import LEADER, Project, position_stats
from app.dataio import (SAMPLE_SIGNUPS, _pid_key, apply_sample_conditions,
                        export_xlsx, import_positions, import_signups,
                        pinyin_key, sample_xlsx)
from app.solver import run_solve


def _signups_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    for row in SAMPLE_SIGNUPS:
        ws.append(row)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@pytest.fixture
def project() -> Project:
    p = Project()
    import_positions(p, sample_xlsx(), "示例.xlsx")
    import_signups(p, _signups_bytes(), "报名.xlsx")
    apply_sample_conditions(p)
    # **故意打乱**：让「导入表行序」和「pid 序」不一致，否则测不出有没有排。
    p.positions = {k: p.positions[k] for k in ["P05", "P01", "P03", "P06", "P02", "P04"]}
    p.assignments = run_solve(p, p.current_pairs()).assignments
    return p


def _sheet(project: Project, title: str):
    wb = load_workbook(io.BytesIO(export_xlsx(project)))
    ws = wb[title]
    return [[ws.cell(r, c).value for c in range(1, ws.max_column + 1)]
            for r in range(2, ws.max_row + 1)]


def _header(project: Project, title: str):
    """表头得现读:排班表的标签列是按项目动态生成的,列号写死会随数据漂。"""
    ws = load_workbook(io.BytesIO(export_xlsx(project)))[title]
    return [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]


def _by_pinyin(names: list[str]) -> list[str]:
    return sorted(names, key=lambda n: (lazy_pinyin(n or "", style=Style.NORMAL), n or ""))


# ---------------------------------------------------------------- 岗位按 pid


def test_岗位统计按_pid_排而不是导入表行序(project):
    pids = [row[0] for row in _sheet(project, "岗位统计")]
    assert pids == sorted(pids)
    # 前提校验：网页那边拿到的确实还是打乱后的原始行序，说明这次排序只发生在导出。
    assert [s["pid"] for s in position_stats(project)] != pids


def test_排班表的岗位也按_pid_排(project):
    seen: list[str] = []
    for row in _sheet(project, "排班表"):
        if row[0] not in seen:
            seen.append(row[0])
    assert seen == sorted(seen)


def test_pid_里的数字按数值比而不是按字符串(project):
    """`P10` 要排在 `P2` 后面。按字符串排的话 "P10" < "P2"。"""
    assert sorted(["P10", "P2", "P1"], key=_pid_key) == ["P1", "P2", "P10"]
    # 字母段不同的仍各自成块。
    assert sorted(["T01", "P02"], key=_pid_key) == ["P02", "T01"]


# ---------------------------------------------------------------- 姓名按拼音


def test_拼音键不是默认字符串序():
    """默认字符串序按 Unicode 码点：张(U+5F20) < 李(U+674E)，「张三」会跑到前面。"""
    assert sorted(["张三", "李四"]) == ["张三", "李四"]           # 默认序（错的）
    assert sorted(["张三", "李四"], key=pinyin_key) == ["李四", "张三"]


def test_个人班表岗位数多的在前_同岗位数按拼音(project):
    rows = _sheet(project, "个人班表")
    counts = [row[4] for row in rows]
    assert counts == sorted(counts, reverse=True)
    for n in set(counts):
        names = [row[0] for row in rows if row[4] == n]
        assert names == _by_pinyin(names)


def test_排班表岗位内先组长后组员_再按报名先后(project):
    """同角色内按**报名先后**，不是拼音。

    排班本身就是先到先得排出来的，导出跟着同一个顺序，拿它跟报名表对着看才对得上。
    （拼音只当平局兜底——整批导入时时间常常一模一样。其余几张表仍按拼音，
    那些是拿来找人的。）
    """
    rows = _sheet(project, "排班表")
    header = _header(project, "排班表")
    at_role, at_name = header.index("角色"), header.index("姓名")
    at_time = header.index("提交答卷时间")

    groups: dict[str, list[list]] = {}
    for row in rows:
        groups.setdefault(row[0], []).append(row)

    checked_leader = False
    for pid, group in groups.items():
        roles = [row[at_role] for row in group]
        # 组长必须全在前面。
        assert roles == sorted(roles, key=lambda r: 0 if r == "组长" else 1), pid
        if "组长" in roles:
            checked_leader = True
        for role in ("组长", "组员"):
            times = [row[at_time] for row in group if row[at_role] == role]
            assert times == sorted(times), (pid, role, times)
    assert checked_leader, "示例数据里没有组长，这条断言等于没测"
    # 顺带确认这条断言不是空转：至少有一组的报名先后和拼音序不一样
    assert any(
        [r[at_name] for r in g if r[at_role] == "组员"]
        != _by_pinyin([r[at_name] for r in g if r[at_role] == "组员"])
        for g in groups.values()
    ), "示例数据里报名先后和拼音序恰好一致，这条断言分不出两者"


# ---------------------------------------------------------------- 变更表分组


def test_变更表按类型分组_组内按拼音(project):
    """⚠️ 数据是**特意**构造成「自然顺序和目标顺序正好相反」的。

    `core.diff_pairs` 返回时按 vol_key 排序。随便挑几个人的话，类型顺序很可能
    碰巧就是对的——那样这条测试回退代码之后照样能过，等于没测。所以这里让
    名字最靠前的人当「移除」（类型该排最后）、最靠后的当「新增」（该排最前）。
    """
    cur = project.current_pairs()
    keys = sorted({vk for _, vk in cur})
    first, mid, last = keys[0], keys[len(keys) // 2], keys[-1]

    prev = [pr for pr in cur if pr[1] not in {last, mid}]  # last 只在新的里 → 新增
    prev.append(("P06", first))                            # first 多一个旧岗 → 移除
    prev.append(("P06", mid))                              # mid 旧岗换成新岗 → 换岗
    project.prev_pairs = prev

    rows = _sheet(project, "变更表")
    types = [row[0] for row in rows]
    assert set(types) == {"新增", "换岗", "移除"}, "三种类型没凑齐，测不出分组"

    rank = {"新增": 0, "换岗": 1, "移除": 2}
    assert [rank[t] for t in types] == sorted(rank[t] for t in types)
    for t in set(types):
        names = [row[1] for row in rows if row[0] == t]
        assert names == _by_pinyin(names), t

    # 自检：不分组的话顺序确实是错的——否则上面那条断言等于没测。
    from app.core import diff_pairs
    raw, _ = diff_pairs(project.prev_pairs, project.current_pairs())
    assert [rank[r["type"]] for r in raw] != sorted(rank[r["type"]] for r in raw), (
        "构造的数据没能让自然顺序和目标顺序相反，这条测试无法判别回归"
    )


# ---------------------------------------------------------------- 不该动的


def test_候补队列的岗位内排位绝不能重排(project):
    """岗位按 pid 排，但**组内必须保持候补排位**。

    `rank` 就是「下一个补谁」，和求解器共用判据。按拼音重排的话，表上第一行的人
    并不是下一个该补的人——那比不排还糟。
    """
    rows = _sheet(project, "候补队列")
    assert rows, "示例数据没产生候补，这条断言等于没测"

    seen: list[str] = []
    for row in rows:
        if row[0] not in seen:
            seen.append(row[0])
    assert seen == sorted(seen), "岗位没按 pid 排"

    by_pid: dict[str, list[int]] = {}
    for row in rows:
        by_pid.setdefault(row[0], []).append(row[2])
    for pid, ranks in by_pid.items():
        assert ranks == list(range(1, len(ranks) + 1)), (pid, ranks)


def test_网页那侧的顺序没有被改动(project):
    """`position_stats` / `build_queue` 是网页和求解器共用的，导出的排序不许碰它们。"""
    from app.core import build_queue
    from app.views import derive

    views = derive(project)
    # 岗位统计：仍然是导入表的原始行序（fixture 里打乱过）。
    assert [s["pid"] for s in views["stats"]] == list(project.positions)
    # 候补队列：仍然是 build_queue 的原始顺序。
    assert [r["pid"] for r in views["queue"]["items"]] == \
           [r["pid"] for r in build_queue(project)][: len(views["queue"]["items"])]
