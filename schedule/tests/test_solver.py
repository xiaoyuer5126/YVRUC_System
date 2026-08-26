"""两段式求解策略。

这一层要守住的承诺：**正常项目走主段，主段算不完也得有结果可交**，
而且降级那一版的硬约束一条都不能松——降级只是少挪几层，不是放宽规则。
"""
from __future__ import annotations

import pytest

from app.core import SolveTimeout, solve
from app.solver import MAIN_DEPTH, run_solve

from tests.test_core import make_project


def test_正常项目走主段_不降级():
    """人均报 3 个岗位，主段深度 2 应该轻松算完，结果就是主深度那一版。"""
    prj = make_project(n_positions=12, n_people=40, per_person=3)
    outcome = run_solve(prj, main_seconds=60, fallback_seconds=60)
    assert outcome.degraded is False

    expected = solve(make_project(n_positions=12, n_people=40, per_person=3),
                     max_depth=MAIN_DEPTH)
    assert [(a.pid, a.vol_key) for a in outcome.assignments] == \
           [(a.pid, a.vol_key) for a in expected]


def test_人均报六个岗位也不会把主段拖垮():
    """这个形状在深度 6 下要跑十几分钟，正是把主段压到 2 的理由。

    给主段一个很小的预算：算得完就说明深度 2 确实便宜，不是靠预算撑着。
    保底重试单独给一点点——它用的是大深度，正好是这个形状最贵的地方，
    而这条用例要量的是主段，不是它。
    """
    prj = make_project(n_positions=12, n_people=40, quota=2, per_person=6)
    outcome = run_solve(prj, main_seconds=10, fallback_seconds=60, rescue_seconds=0.5)
    assert outcome.degraded is False, "深度 2 连这个形状都算不动的话，主段选错了"


def test_保底重试超时不影响主段结果():
    """保底重试是锦上添花的一趟：算不完就保留主段结果照常返回。

    抛出点可能落在增广的中途（那时排班是被拆了一半的），所以它必须先拍快照、
    超时了整个还原——把半成品端出去比不补人糟得多。
    """
    prj = make_project(n_positions=12, n_people=40, quota=2, per_person=6)
    tight = run_solve(prj, main_seconds=10, fallback_seconds=60, rescue_seconds=0.01)
    assert tight.degraded is False, "重试超时不该把整趟判成降级"
    assert tight.rescue_timed_out is True

    # 结果必须和「压根不做重试」逐字一致——还原干净了才谈得上安全
    plain = solve(make_project(n_positions=12, n_people=40, quota=2, per_person=6),
                  max_depth=MAIN_DEPTH, rescue_depth=0)
    assert [(a.pid, a.vol_key, a.role) for a in tight.assignments] == \
           [(a.pid, a.vol_key, a.role) for a in plain]


def test_主段算不动就退到降级段_而不是把人晾着():
    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    outcome = run_solve(prj, main_seconds=0.05, fallback_seconds=60, rescue_seconds=0.5)
    assert outcome.degraded is True
    assert outcome.assignments, "降级了也要排出东西来"
    filled = {}
    for a in outcome.assignments:
        filled[a.pid] = filled.get(a.pid, 0) + 1
    for pid, n in filled.items():
        assert n <= prj.positions[pid].quota


def test_两段都算不完才报错():
    """连深度 1 都跑不完，说明该去拆分活动了，不是该继续等。"""
    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    with pytest.raises(SolveTimeout):
        run_solve(prj, main_seconds=0.05, fallback_seconds=0.05, rescue_seconds=0.5)


def test_两段各有各的预算_互不挪用():
    """降级段是保底出结果的那一段，不能因为主段花了时间就饿着它。

    主段给 0.05 秒必然超时；降级段仍然应该拿到完整的预算，把结果算出来。
    """
    import time as _time

    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    started = _time.monotonic()
    outcome = run_solve(prj, main_seconds=0.05, fallback_seconds=60, rescue_seconds=0.5)
    elapsed = _time.monotonic() - started
    assert outcome.degraded is True
    assert outcome.assignments
    # 主段 0.05 秒 + 降级段实际用时；总共远不到 60 秒，说明降级段没被掐
    assert elapsed < 30


def test_降级那一版也遵守岗位要求和冲突():
    """降级只是少挪几层，硬约束一条都不能松。"""
    from app.conditions import from_pairs
    from app.core import eligible

    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    # 给一半岗位加上性别要求，再连一串冲突
    pids = list(prj.positions)
    for i, pid in enumerate(pids):
        if i % 2 == 0:
            prj.positions[pid].requirements = from_pairs({"性别": "女"})
    for i in range(0, len(pids) - 1, 2):
        prj.set_conflict(pids[i], pids[i + 1], True)
    for i, vk in enumerate(prj.volunteers):
        prj.volunteers[vk].tags = {"性别": "女" if i % 3 == 0 else "男"}

    outcome = run_solve(prj, main_seconds=0.05, fallback_seconds=60, rescue_seconds=0.5)
    assert outcome.degraded is True
    held = {}
    for a in outcome.assignments:
        held.setdefault(a.vol_key, []).append(a.pid)
    for vk, got in held.items():
        vol = prj.volunteers[vk]
        for pid in got:
            assert eligible(vol, prj.positions[pid]), "排进了不符合要求的岗位"
        for i, a in enumerate(got):
            for b in got[i + 1:]:
                assert not prj.is_conflict(a, b), "同一个人被排进了两个冲突岗位"


def test_深度2比深度1多排上一个人_这是主段不压到1的理由():
    """两步腾位链：深度 1 断在第一步，深度 2 才走得通。

    P1、P2 名额各 1，P3 名额 2 且已被丁锁定一个（把 P3 的缺口率压到 0.5，
    否则乙会先被推去 P3，这条链根本不成形）。
        甲(最早) 报 P1/P2 → 落在 P1
        乙       报 P2/P3 → 落在 P2（P2 缺口率 1.0 > P3 的 0.5）
        丙(最晚) 只报 P1  → P1 满了，要腾位
    深度 1：把甲挪去 P2，P2 满，到底 → 丙空手。
    深度 2：甲→P2、乙→P3、丙进 P1，四个人都有岗位。
    """
    from app.core import Assignment, Position, Project, Volunteer

    def build():
        prj = Project()
        prj.positions["P1"] = Position(pid="P1", name="P1", quota=1)
        prj.positions["P2"] = Position(pid="P2", name="P2", quota=1)
        prj.positions["P3"] = Position(pid="P3", name="P3", quota=2)
        for name, wishes, minute in (("甲", ["P1", "P2"], "01"),
                                     ("乙", ["P2", "P3"], "02"),
                                     ("丙", ["P1"], "03"),
                                     ("丁", ["P3"], "00")):
            key = f"{name}138"
            prj.volunteers[key] = Volunteer(key=key, name=name, contact="138")
            for pid in wishes:
                prj.add_signup(key, pid, f"2026-07-20 09:{minute}")
        prj.assignments = [Assignment("P3", "丁138", "LOCKED", False)]
        return prj

    shallow = {a.vol_key for a in solve(build(), max_depth=1)}
    main = {a.vol_key for a in solve(build(), max_depth=MAIN_DEPTH)}
    assert "丙138" not in shallow, "深度 1 本来就该把丙晾在外面"
    assert "丙138" in main, "深度 2 应该能腾出这条两步链"
    assert len(main) == len(shallow) + 1
