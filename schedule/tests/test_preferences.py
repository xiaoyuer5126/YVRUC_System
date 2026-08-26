"""优先条件:C6 排序 + S1 分阶段放行。

「优先」这个词在排班里有一个别处没有的含义:**它是故意打破先到先得的**。
所以这几条用例既要证明它起作用,也要证明它没越界——优先不是必须,
名额有余时不符合的人照样进得来。
"""
from __future__ import annotations

from app.conditions import from_pairs
from app.core import Position, Project, Volunteer, solve


def person(prj: Project, name: str, wishes: list[str], minute: int, **tags) -> str:
    key = f"{name}138{minute:04d}"
    prj.volunteers[key] = Volunteer(key=key, name=name, contact=f"138{minute:04d}", tags=tags)
    for pid in wishes:
        prj.add_signup(key, pid, f"2026-07-20 09:{minute:02d}")
    return key


def who(rows, pid):
    return {a.vol_key.split("1")[0] for a in rows if a.pid == pid}


def test_S1_晚报名但符合优先条件的人先进():
    """这就是「优先分配」的定义，不是 bug。

    P 名额 1，优先条件「性别=女」。甲（男）09:01 报名，乙（女）09:02 报名。
    纯先到先得会给甲；有优先条件时第一阶段只放女生进，所以乙拿到。
    """
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="礼仪", quota=1,
                                  preferences=[from_pairs({"性别": "女"})])
    person(prj, "甲", ["P"], 1, 性别="男")
    person(prj, "乙", ["P"], 2, 性别="女")

    assert who(solve(prj), "P") == {"乙"}


def test_优先不是必须_名额有余时不符合的人也能进():
    """第二阶段会开放。名额 2 只有一个女生时，男生照样排得进去。"""
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="礼仪", quota=2,
                                  preferences=[from_pairs({"性别": "女"})])
    person(prj, "甲", ["P"], 1, 性别="男")
    person(prj, "乙", ["P"], 2, 性别="女")

    assert who(solve(prj), "P") == {"甲", "乙"}


def test_没有优先条件的岗位_完全按先到先得():
    """S1 只对带优先条件的岗位生效，别的岗位一点都不该受影响。"""
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="普通", quota=1)
    person(prj, "甲", ["P"], 1, 性别="男")
    person(prj, "乙", ["P"], 2, 性别="女")

    assert who(solve(prj), "P") == {"甲"}


def test_多档优先按先后比_不是加权():
    """第 1 优先胜过第 2 优先，哪怕后者报名更早。

    P 名额 1，第 1 优先「年级=2023」，第 2 优先「性别=女」。
    甲（2024 女，最早）满足第 2 档，乙（2023 男）满足第 1 档 → 乙拿到。
    加权求和会算成平手甚至反过来，这条用例就是钉死「按先后比」。
    """
    prj = Project()
    prj.positions["P"] = Position(
        pid="P", name="要紧岗", quota=1,
        preferences=[from_pairs({"年级": "2023"}), from_pairs({"性别": "女"})])
    person(prj, "甲", ["P"], 1, 性别="女", 年级="2024")
    person(prj, "乙", ["P"], 2, 性别="男", 年级="2023")

    assert who(solve(prj), "P") == {"乙"}


def test_都不符合任何一档时_退回先到先得():
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="礼仪", quota=1,
                                  preferences=[from_pairs({"性别": "女"})])
    person(prj, "甲", ["P"], 1, 性别="男")
    person(prj, "乙", ["P"], 2, 性别="男")

    assert who(solve(prj), "P") == {"甲"}


def test_S1_不让人在第一阶段被挪进不该进的岗位():
    """增广会把人从满员岗位挪走，被挪走的人去哪儿也得守这道闸门。

    A 名额 1 无优先条件，B 名额 1 优先「性别=女」。
    甲（男，最早）报了 A 和 B，乙（男）只报 A，丙（女）只报 B。
    第一阶段：甲只能进 A（B 不放男生），丙进 B。乙想要 A，A 满了，
    要把甲挪走——甲唯一的备选是 B，但第一阶段不放他进，所以挪不动。
    最终 B 必须是丙的。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="普通", quota=1)
    prj.positions["B"] = Position(pid="B", name="礼仪", quota=1,
                                  preferences=[from_pairs({"性别": "女"})])
    person(prj, "甲", ["A", "B"], 1, 性别="男")
    person(prj, "乙", ["A"], 2, 性别="男")
    person(prj, "丙", ["B"], 3, 性别="女")

    rows = solve(prj)
    assert who(rows, "B") == {"丙"}, "优先条件岗位被不符合的人占了"


def test_优先条件不越过必选条件():
    """必选是硬的，优先是软的。符合优先但不符合必选的人，一样进不来。"""
    prj = Project()
    prj.positions["P"] = Position(
        pid="P", name="礼仪", quota=2,
        requirements=from_pairs({"年级": "2023"}),
        preferences=[from_pairs({"性别": "女"})])
    person(prj, "甲", ["P"], 1, 性别="女", 年级="2025")   # 符合优先，不符合必选
    person(prj, "乙", ["P"], 2, 性别="男", 年级="2023")   # 反过来

    assert who(solve(prj), "P") == {"乙"}
