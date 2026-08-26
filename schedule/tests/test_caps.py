"""岗位组上限:一个人在一组岗位里最多占几个。

这条规则存在的理由就是**冲突图表达不了它**——冲突是成对的关系,而
「这三个不能全占」是三元的。第一条用例把这件事钉死:成对怎么勾都拼不出来。
"""
from __future__ import annotations

from itertools import combinations

import pytest

from app.core import (Cap, Position, Project, Volunteer, build_queue,
                      identity_key, solve)


def build(quota=5, n_people=3):
    """三个时段:8-12 / 12-14 / 14-18。每个人三个都报。"""
    prj = Project()
    for pid, name in (("AM", "8-12"), ("NOON", "12-14"), ("PM", "14-18")):
        prj.positions[pid] = Position(pid=pid, name=name, quota=quota)
    for i in range(n_people):
        key = identity_key(f"人{i}", f"138{i:04d}")
        prj.volunteers[key] = Volunteer(key=key, name=f"人{i}", contact=f"138{i:04d}")
        for pid in prj.positions:
            prj.add_signup(key, pid, f"2026-07-20 09:{i:02d}")
    return prj


def held(rows):
    out = {}
    for a in rows:
        out.setdefault(a.vol_key, set()).add(a.pid)
    return out


# ————————————————————————————————————————— 为什么非要新加一条

SLOTS = ["AM", "NOON", "PM"]
PAIRS = list(combinations(SLOTS, 2))


def _wants(pids, conflicts=(), caps=()):
    """造一个只报 pids 这几个岗位的人，看他最终能不能全拿到。"""
    prj = build(quota=5, n_people=0)
    key = identity_key("独苗", "13800000000")
    prj.volunteers[key] = Volunteer(key=key, name="独苗", contact="13800000000")
    for pid in pids:
        prj.add_signup(key, pid, "2026-07-20 09:00")
    for a, b in conflicts:
        prj.set_conflict(a, b, True)
    prj.caps = list(caps)
    return held(solve(prj)).get(key, set()) == set(pids)


def test_成对的冲突图拼不出这条要求():
    """**穷举证明**：三个时段之间一共只有 3 对，勾法一共 2³ = 8 种，一种都不行。

    要的是「任意两个都允许、三个凑齐不允许」。而成对约束下，允许的组合对子集
    封闭：三对全允许 ⇒ 三个凑齐必然也允许。所以这不是「没实现」，是做不到——
    这条用例把 8 种勾法逐个跑一遍，钉住这个结论。
    """
    for r in range(len(PAIRS) + 1):
        for chosen in combinations(PAIRS, r):
            two_ok = all(_wants(list(pair), conflicts=chosen) for pair in PAIRS)
            three_ok = _wants(SLOTS, conflicts=chosen)
            assert not (two_ok and not three_ok), \
                f"居然拼出来了？勾法 {chosen}——那这条规则就白加了"


def test_一组上限二就做到了成对做不到的事():
    """同一个判据，换成岗位组：任意两个都允许，三个凑齐被挡。"""
    cap = (Cap(name="8月1日全天", pids=SLOTS, limit=2),)
    assert all(_wants(list(pair), caps=cap) for pair in PAIRS)
    assert not _wants(SLOTS, caps=cap)


def test_一组三个上限二_正好挡住从早干到晚():
    """任意两个都行，三个凑齐不行——这就是要的语义。"""
    prj = build()
    prj.caps = [Cap(name="8月1日全天", pids=["AM", "NOON", "PM"], limit=2)]
    got = held(solve(prj))

    assert got, "不该把人全挡住"
    for vk, pids in got.items():
        assert len(pids) == 2, f"{vk} 拿到了 {sorted(pids)}"
        assert pids != {"AM", "NOON", "PM"}


def test_上限不影响组外的岗位():
    """组只管组里的。加一个不在组里的岗位，照样排得进去。"""
    prj = build()
    prj.positions["EXTRA"] = Position(pid="EXTRA", name="第二天机动", quota=5)
    for vk in prj.volunteers:
        prj.add_signup(vk, "EXTRA", "2026-07-20 09:00")
    prj.caps = [Cap(name="8月1日全天", pids=["AM", "NOON", "PM"], limit=2)]

    for vk, pids in held(solve(prj)).items():
        assert "EXTRA" in pids, "组外的岗位不该被组上限挡住"
        assert len(pids & {"AM", "NOON", "PM"}) <= 2


def test_多个组各判各的_可以重叠():
    """两组重叠时都要满足。AM/NOON 最多 1 个，整天最多 2 个。"""
    prj = build()
    prj.caps = [
        Cap(name="上午段", pids=["AM", "NOON"], limit=1),
        Cap(name="全天", pids=["AM", "NOON", "PM"], limit=2),
    ]
    for vk, pids in held(solve(prj)).items():
        assert len(pids & {"AM", "NOON"}) <= 1
        assert len(pids) <= 2


def test_候补原因点得出是哪一组():
    """只说「超出上限」的话，人拿着这句话还是不知道该松哪一条。"""
    prj = build(quota=5, n_people=1)
    prj.caps = [Cap(name="8月1日全天", pids=["AM", "NOON", "PM"], limit=2)]
    prj.assignments = solve(prj)

    reasons = [r["reason"] for r in build_queue(prj)]
    assert len(reasons) == 1
    assert "超出岗位组上限" in reasons[0]
    assert "8月1日全天" in reasons[0] and "2" in reasons[0]


# ————————————————————————————————————————— 边界

def test_上限比成员数还大会被夹住_因为它永远拦不住人():
    prj = build()
    prj.caps = [Cap(name="白设的", pids=["AM", "NOON", "PM"], limit=9)]
    assert Project.from_dict(prj.to_dict()).caps[0].limit == 3


def test_删岗位时组里跟着剔掉_剔空就丢掉整组():
    prj = build()
    prj.caps = [Cap(name="全天", pids=["AM", "NOON", "PM"], limit=2),
                Cap(name="就俩", pids=["AM", "NOON"], limit=1)]
    prj.remove_position("AM")
    assert prj.caps[0].pids == ["NOON", "PM"]
    assert prj.caps[1].pids == ["NOON"]

    prj.remove_position("NOON")
    prj.remove_position("PM")
    assert prj.caps == [], "成员被删光的组该整个丢掉"


def test_指向不存在岗位的成员会被静默剔掉():
    """和冲突图同一套做法：岗位删了，提到它的关系就该跟着没，
    而不是让整份项目读不出来。"""
    prj = build()
    data = prj.to_dict()
    data["caps"] = [{"name": "混了个不存在的", "pids": ["AM", "NOPE", "PM"], "limit": 1}]
    assert Project.from_dict(data).caps[0].pids == ["AM", "PM"]


def test_上限为零就是这组谁都不能占():
    prj = build()
    prj.caps = [Cap(name="封了", pids=["AM", "NOON", "PM"], limit=0)]
    assert solve(prj) == []


@pytest.mark.parametrize("limit", [1, 2, 3])
def test_人工锁定的行可以突破上限_求解器不在其上再加(limit):
    """H1 优先于一切：锁定行原样端着，哪怕它已经把组撑爆了。"""
    from app.core import LOCKED, Assignment

    prj = build(n_people=1)
    vk = next(iter(prj.volunteers))
    prj.assignments = [Assignment(pid, vk, LOCKED, True) for pid in ("AM", "NOON", "PM")]
    prj.caps = [Cap(name="全天", pids=["AM", "NOON", "PM"], limit=limit)]

    rows = solve(prj)
    assert sorted(a.pid for a in rows) == ["AM", "NOON", "PM"]
    assert all(a.status == LOCKED for a in rows)
