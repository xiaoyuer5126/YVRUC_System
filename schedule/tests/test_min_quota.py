"""最低人数:C1 排序 + P1 补位。

两件事分开测,因为它们各自能单独失效,而失效的表现是一样的
(某个岗位达不到最低线),混在一起查不出是哪一半坏了。
"""
from __future__ import annotations

from app.core import Position, Project, Volunteer, solve


def person(prj: Project, name: str, wishes: list[str], minute: int) -> str:
    key = f"{name}138{minute:04d}"
    prj.volunteers[key] = Volunteer(key=key, name=name, contact=f"138{minute:04d}")
    for pid in wishes:
        prj.add_signup(key, pid, f"2026-07-20 09:{minute:02d}")
    return key


def filled(rows, pid):
    return sum(1 for a in rows if a.pid == pid)


def test_C1_没到最低线的岗位先补_哪怕它缺口率更小():
    """名额 6 排了 0（缺口率 1.0）vs 名额 2 最低 2 排了 0（也是 1.0）——
    平手时得让「有最低线」的那个先拿人，否则最低线形同虚设。

    甲只有一次挑的机会（每轮每人至多加一个岗位），他会挑 cand_key 排第一的那个。
    """
    prj = Project()
    prj.positions["BIG"] = Position(pid="BIG", name="大岗位", quota=6)
    prj.positions["MIN"] = Position(pid="MIN", name="有底线", quota=2, min_quota=2)
    person(prj, "甲", ["BIG", "MIN"], 1)

    rows = solve(prj)
    assert filled(rows, "MIN") == 1, "第一个人应该先去补有最低线的那个岗位"


def test_P1_从富余岗位调人补最低线():
    """C1 排序管不到的情况：人先到先得地占满了 A，轮到 B 时没人可挑了。

    A 名额 3 不设底线，B 名额 1 最低 1。
    甲乙丙都报了 A 和 B（时间从早到晚），丁只报 A。
    先到先得会把甲乙丙塞进 A（3 个满），丁再进不去；B 空着。
    P1 应该把甲（报 B 最早的那个）从 A 调到 B——A 从 3 掉到 2，它没有底线，
    掉了不心疼；B 从 0 到 1，达标。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="富余", quota=3)
    prj.positions["B"] = Position(pid="B", name="缺人", quota=1, min_quota=1)
    for i, name in enumerate(("甲", "乙", "丙"), start=1):
        person(prj, name, ["A", "B"], i)
    person(prj, "丁", ["A"], 4)

    rows = solve(prj)
    assert filled(rows, "B") == 1, "P1 应该调一个人过来补最低线"
    # 人次没变、没人掉岗：调走的那个人现在在 B
    assert len({a.vol_key for a in rows}) == 4
    assert {(a.pid, a.vol_key.split("1")[0]) for a in rows} >= {("B", "甲")}


def test_P1_挪走正好解掉冲突的人():
    """要挪的人现在待的岗位和目标岗位时间冲突——这恰恰是最该挪的一类。

    A 名额 3（无底线）、B 名额 2 最低 2，两者时间冲突，所以没人能同时占。
    甲乙丙都报了 A 和 B。主循环按岗位表顺序先把甲塞进 A，乙丙轮到时 B 缺口更大
    会去 B……真正要验的是 P1 判合法性时**带上 leaving**：不带的话，
    「他手上那个岗位和目标冲突」会被当成不能挪，而他一离开就没冲突了。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="富余", quota=3)
    prj.positions["B"] = Position(pid="B", name="缺人", quota=2, min_quota=2)
    prj.set_conflict("A", "B", True)
    for i, name in enumerate(("甲", "乙", "丙"), start=1):
        person(prj, name, ["A", "B"], i)

    rows = solve(prj)
    assert filled(rows, "B") == 2, "B 的最低线该被补满"
    # 冲突还是冲突：没有人同时占着 A 和 B
    held = {}
    for a in rows:
        held.setdefault(a.vol_key, set()).add(a.pid)
    assert all(not {"A", "B"} <= got for got in held.values())


def test_P1_不会拆东墙补西墙():
    """供给岗位自己正卡在最低线上时，不能把它抽到线下，宁可让目标岗位空着。

    A、B 名额都是 2、最低都是 2，两者时间冲突（一个人只能占一个）。
    只有甲乙丙三个人，都报了两个岗位。主循环排完是 A 满 2、B 只有 1。
    B 还差 1，而唯一的候选都在 A 上——从 A 抽一个，A 就跌破自己的最低线。
    P1 必须放弃：让一个岗位差人，好过让两个岗位都差人。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="也有底线", quota=2, min_quota=2)
    prj.positions["B"] = Position(pid="B", name="缺人", quota=2, min_quota=2)
    prj.set_conflict("A", "B", True)
    for i, name in enumerate(("甲", "乙", "丙"), start=1):
        person(prj, name, ["A", "B"], i)

    rows = solve(prj)
    assert filled(rows, "A") == 2, "A 被抽到了最低线以下"
    assert filled(rows, "B") == 1, "只有三个人，B 就是补不满"


def test_招不够也照样出排班_最低人数不是硬约束():
    """报名的人本来就不够时，不能因为「达不到最低线」就拒绝出结果。

    这是最低人数做成排序优先级而不是可行性约束的理由：做成硬约束的话，
    人不够就无解，而人不够恰恰是最常见的情况。
    """
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="缺人", quota=10, min_quota=8)
    person(prj, "甲", ["P"], 1)

    rows = solve(prj)
    assert filled(rows, "P") == 1


def test_P1_不动锁定行():
    """富余岗位里那个人是人工锁定的，就不能拿他去补别处。

    A 和 B 冲突，所以甲不可能两个都占；他被锁在 A 上，B 就只能空着——
    P1 宁可让最低线空着，也不动 LOCKED（H1 优先于一切）。
    """
    from app.core import LOCKED, Assignment

    prj = Project()
    prj.positions["A"] = Position(pid="A", name="富余", quota=3)
    prj.positions["B"] = Position(pid="B", name="缺人", quota=1, min_quota=1)
    prj.set_conflict("A", "B", True)
    key = person(prj, "甲", ["A", "B"], 1)
    prj.assignments = [Assignment("A", key, LOCKED, False)]

    rows = solve(prj)
    locked = [a for a in rows if a.status == LOCKED]
    assert [(a.pid, a.vol_key) for a in locked] == [("A", key)], "锁定行被动了"
    assert filled(rows, "B") == 0, "只有这一个人，而他锁在 A 上，B 就该空着"
