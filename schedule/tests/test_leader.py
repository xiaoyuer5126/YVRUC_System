"""小组长:H9/H10 资格与容量、C7/S2 组长槽优先、P2 补位。

一条语义前提贯穿全文件:**组长含在名额之内**。名额 3、组长 1 就是 2 个组员
加 1 个组长,不是 3+1。这样 quota 始终是「这个岗位站几个人」这一个意思,
导出表里的「已排/名额」不用解释。
"""
from __future__ import annotations

from app.conditions import from_pairs
from app.core import (LEADER, MEMBER, Assignment, Leader, Position, Project,
                      Volunteer, solve)


def person(prj: Project, name: str, wishes: list[str], minute: int, **tags) -> str:
    key = f"{name}138{minute:04d}"
    prj.volunteers[key] = Volunteer(key=key, name=name, contact=f"138{minute:04d}", tags=tags)
    for pid in wishes:
        prj.add_signup(key, pid, f"2026-07-20 09:{minute:02d}")
    return key


def roles(rows, pid):
    return {a.vol_key.split("1")[0]: a.role for a in rows if a.pid == pid}


def test_组长占名额而不是额外加人():
    """名额 3、组长 1 = 2 组员 + 1 组长，总数还是 3。"""
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="检录", quota=3, leader=Leader(count=1))
    for i, name in enumerate("甲乙丙丁", start=1):
        person(prj, name, ["P"], i)

    got = roles(solve(prj), "P")
    assert len(got) == 3, "总人数不该超过名额"
    assert sum(1 for r in got.values() if r == LEADER) == 1


def test_C7_够格的人先占组长槽():
    """组长是稀缺的：第一个来的合格者要是进了普通槽，组长槽可能一直空着。

    甲最早且够格 → 他就该是组长，而不是「先按普通槽排，回头再说」。
    """
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="检录", quota=2,
                                  leader=Leader(count=1,
                                                conditions=from_pairs({"经验": "有"})))
    person(prj, "甲", ["P"], 1, 经验="有")
    person(prj, "乙", ["P"], 2, 经验="无")

    got = roles(solve(prj), "P")
    assert got["甲"] == LEADER
    assert got["乙"] == MEMBER


def test_H9_不够格的人不会被塞进组长槽():
    """没人够格时，组长槽宁可空着——顶着组长名分的普通人比空缺更糟，
    因为导出表里那一行就写着「组长」，经办人是照着它安排的。
    """
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="检录", quota=2,
                                  leader=Leader(count=1,
                                                conditions=from_pairs({"经验": "有"})))
    person(prj, "甲", ["P"], 1, 经验="无")
    person(prj, "乙", ["P"], 2, 经验="无")

    got = roles(solve(prj), "P")
    assert all(r == MEMBER for r in got.values())
    # 组长槽空着，但普通槽只有 1 个（名额 2 - 组长 1），所以只排得下一个人
    assert len(got) == 1


def test_组长条件之外还要过必选条件():
    """组长首先也是这个岗位上的人。组长条件是额外一层，不是替代。"""
    prj = Project()
    prj.positions["P"] = Position(
        pid="P", name="检录", quota=2,
        requirements=from_pairs({"年级": "2023"}),
        leader=Leader(count=1, conditions=from_pairs({"经验": "有"})))
    person(prj, "甲", ["P"], 1, 经验="有", 年级="2025")   # 够格当组长，但必选没过
    person(prj, "乙", ["P"], 2, 经验="有", 年级="2023")

    got = roles(solve(prj), "P")
    assert "甲" not in got
    assert got["乙"] == LEADER


def test_P2_把普通槽里够格的人升上来():
    """P1 把人从别的岗位挪过来时，目标岗位的组长槽可能刚好空着——
    move 会按目标岗位重判角色，P2 再兜一遍剩下的。

    A 富余（无底线），B 名额 2 最低 2、组长 1，A×B 冲突。
    甲乙都报了 A 和 B 且够格当组长；主循环把一个塞进 A，P1 再调过来补 B 的底线，
    最后 B 上应该有一个组长。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="富余", quota=3)
    prj.positions["B"] = Position(pid="B", name="缺人", quota=2, min_quota=2,
                                  leader=Leader(count=1))
    prj.set_conflict("A", "B", True)
    for i, name in enumerate("甲乙丙", start=1):
        person(prj, name, ["A", "B"], i)

    got = roles(solve(prj), "B")
    assert len(got) == 2, "B 的最低线该被补满"
    assert sum(1 for r in got.values() if r == LEADER) == 1, "B 的组长槽该有人"


def test_换人时重判角色_不沿用被换掉那一行():
    """同批换人把 x 换成 y 时，如果那一行是组长而 y 不够格，
    y 不能顶着组长的名分坐进来。

    A、B 同批。A 名额 1 组长 1（条件：经验=有）；B 名额 2。
    """
    prj = Project()
    prj.positions["A"] = Position(pid="A", name="A", quota=1,
                                  leader=Leader(count=1,
                                                conditions=from_pairs({"经验": "有"})))
    prj.positions["B"] = Position(pid="B", name="B", quota=2)
    prj.set_cohort("A", "B", True)
    person(prj, "甲", ["A", "B"], 1, 经验="有")
    person(prj, "乙", ["A", "B"], 2, 经验="无")
    person(prj, "丙", ["B"], 3, 经验="无")

    rows = solve(prj)
    for a in rows:
        if a.role == LEADER:
            vol = prj.volunteers[a.vol_key]
            assert vol.tags.get("经验") == "有", "不够格的人被换进了组长槽"


def test_人工锁定的组长行原样保留():
    """人工可以把任何人钉成组长（界面上就能点），求解器原样端着，不改也不删。"""
    prj = Project()
    prj.positions["P"] = Position(pid="P", name="检录", quota=2,
                                  leader=Leader(count=1,
                                                conditions=from_pairs({"经验": "有"})))
    key = person(prj, "甲", ["P"], 1, 经验="无")
    person(prj, "乙", ["P"], 2, 经验="有")
    prj.assignments = [Assignment("P", key, "LOCKED", True, LEADER)]

    rows = solve(prj)
    locked = [a for a in rows if a.status == "LOCKED"]
    assert [(a.vol_key, a.role, a.forced) for a in locked] == [(key, LEADER, True)]
    # 组长槽已被这一行占掉，乙只能进普通槽
    assert roles(rows, "P").get("乙") == MEMBER
