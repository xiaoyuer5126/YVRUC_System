"""求解器的时间闸门。

算法本身是桌面版原样搬来的,那部分不重测;这里只盯**新加的那一件事**:
超时能不能真的停下来。

这一条不是锦上添花——`find_slot` 的回溯增广是指数级的,岗位互相可替代得厉害
时能跑上几个小时。没有闸门的话,一份设置得不巧的岗位表就能把工作进程占死,
而使用者只看到页面一直转。
"""
from __future__ import annotations

import time

import pytest

from app.core import Position, Project, SolveTimeout, Volunteer, solve


def make_project(n_positions: int, n_people: int, quota: int = 2, per_person: int = 6):
    """造一个「人人都报很多岗位、名额又都不够」的项目。

    这正是增广重排最忙的形状:每个位置都有人惦记,腾一个位置就要连锁挪动一串人。
    """
    prj = Project()
    for i in range(n_positions):
        pid = f"P{i:03d}"
        prj.positions[pid] = Position(pid=pid, name=f"岗位{i}", quota=quota)
    pids = list(prj.positions)
    for j in range(n_people):
        key = f"人{j:04d}13800{j:06d}"
        prj.volunteers[key] = Volunteer(key=key, name=f"人{j}", contact=f"13800{j:06d}")
        for k in range(per_person):
            prj.add_signup(key, pids[(j + k * 7) % len(pids)], f"2026-07-20 09:{j % 60:02d}")
    return prj


def test_正常规模能算完并且不误报超时():
    # 人均报 3 个岗位。**别往上加**：人均 6 个的时候，12 岗位 40 人就要跑
    # 十几分钟（见 README 的规模表），那是求解器本身的问题，不是这条用例的。
    prj = make_project(n_positions=12, n_people=40, per_person=3)
    started = time.monotonic()
    assignments = solve(prj, timeout=30)
    assert time.monotonic() - started < 30
    assert assignments, "应该排出东西来"
    # 硬约束:没有超额
    filled = {}
    for a in assignments:
        filled[a.pid] = filled.get(a.pid, 0) + 1
    for pid, n in filled.items():
        assert n <= prj.positions[pid].quota


def test_到点就停_而且是在热点循环里停的():
    """给一个大盘子和一个极小的预算。

    关键不只是「抛了异常」,而是**抛得快**:如果检查点只放在最外层那个
    `for _ in range(MAX_PASSES)` 上,一轮内部就能跑很久,这里的耗时会远超预算。
    """
    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    started = time.monotonic()
    with pytest.raises(SolveTimeout):
        solve(prj, timeout=0.05)
    elapsed = time.monotonic() - started
    assert elapsed < 3.0, f"超时后又跑了 {elapsed:.1f} 秒才停,检查点放得不够深"


def test_超时会说清楚卡在哪个岗位():
    """「卡住了」和「卡在 P03 检录A组 的增广重排上」对排查的价值差得远。

    ⚠️ 形状是特意挑的:**建表极快、求解极慢**。用 60 岗 600 人那种大盘子的话
    这条会 flaky——`desired_map()` 和第一次排序本身就要几十毫秒,机器一忙就可能
    在还没进到内层循环时就超时,那时求解器确实说不出卡在哪(hint 为空是**对的**),
    但用例会红。12 岗 40 人建表不到一毫秒,而人均报 6 个岗位在深度 6 下要跑
    十几分钟,所以超时必然发生在 find_slot 里,那里 `at` 一定已经填好了。
    """
    prj = make_project(n_positions=12, n_people=40, quota=2, per_person=6)
    with pytest.raises(SolveTimeout) as caught:
        solve(prj, timeout=0.05, max_depth=6, rescue_depth=0)
    assert "岗位" in caught.value.hint
    assert "0.05" in str(caught.value) or "排班超过" in str(caught.value)


def test_不给预算就不限时():
    """离线跑批和单测要用。默认 None,不引入任何计时开销。"""
    prj = make_project(n_positions=8, n_people=20)
    assert solve(prj) is not None


def test_超时不返回半成品():
    """抛出点可能落在增广的中途,那时排班是被拆了一半的。

    调用方拿到的必须是异常而不是一份「看不出哪里不对」的排班——所以项目里
    原来的 assignments 一行都不能动。
    """
    prj = make_project(n_positions=60, n_people=600, quota=3, per_person=10)
    before = list(prj.assignments)
    with pytest.raises(SolveTimeout):
        solve(prj, timeout=0.05)
    assert prj.assignments == before
