"""一键分配的匹配算法：与暴力最优解对拍。

匹配写错的表现是「有人没排上、岗位却空着」——不会报错，只会让老师
以为是名额不够，手动补的时候还未必补得对。所以这里用穷举出的最大匹配
逐个校验，而不是只测几个手写用例。
"""
from __future__ import annotations

import itertools
import random

from backend.assign import match


def brute_force_max(candidates, quota):
    """穷举所有分配方案，返回能排上的最多人数。"""
    people = [who for who, _ in candidates]
    options = {who: opts for who, opts in candidates}
    best = 0
    # 每个人要么不排，要么去他能去的某个岗位
    choices = [[None] + list(options[w]) for w in people]
    for combo in itertools.product(*choices):
        used: dict[str, int] = {}
        ok = True
        for pid in combo:
            if pid is None:
                continue
            used[pid] = used.get(pid, 0) + 1
            if used[pid] > quota[pid]:
                ok = False
                break
        if ok:
            best = max(best, sum(1 for c in combo if c is not None))
    return best


def check(candidates, quota):
    placed = match(candidates, quota)
    options = {who: opts for who, opts in candidates}
    # 结果必须合法：只去能去的岗位，且不超名额
    used: dict[str, int] = {}
    for who, pid in placed.items():
        assert pid in options[who], f"{who} 被排进了不满足条件的 {pid}"
        used[pid] = used.get(pid, 0) + 1
        assert used[pid] <= quota[pid], f"{pid} 超名额"
    return len(placed), brute_force_max(candidates, quota)


def test_贪心会漏人的经典反例():
    """甲不限(1)、乙限女(1)，报名顺序 女A、男B：必须两个人都排上。"""
    candidates = [("女A", ["甲", "乙"]), ("男B", ["甲"])]
    got, best = check(candidates, {"甲": 1, "乙": 1})
    assert got == best == 2


def test_随机对拍():
    rnd = random.Random(20260827)
    for case in range(300):
        n_pos = rnd.randint(1, 4)
        n_people = rnd.randint(1, 6)
        quota = {f"p{i}": rnd.randint(1, 2) for i in range(n_pos)}
        candidates = []
        for j in range(n_people):
            opts = [p for p in quota if rnd.random() < 0.6]
            candidates.append((f"u{j}", opts))
        got, best = check(candidates, quota)
        assert got == best, (f"第{case}例没排到最优：排上 {got}，最优 {best}\n"
                             f"  岗位 {quota}\n  候选 {candidates}")


def test_先到先得():
    """名额只有一个时，报名早的人拿到。"""
    placed = match([("早", ["甲"]), ("晚", ["甲"])], {"甲": 1})
    assert placed == {"早": "甲"}


def test_连续性优先():
    """两个岗位都能去时，优先回到上次待过的岗位。"""
    placed = match([("甲同学", ["p1", "p2"])], {"p1": 1, "p2": 1},
                   prefer={"甲同学": "p2"})
    assert placed["甲同学"] == "p2"


def test_连续性不能以牺牲总人数为代价():
    """优先只是排序，不是硬约束：为了照顾连续性而少排人是不可接受的。"""
    candidates = [("A", ["p1", "p2"]), ("B", ["p1"])]
    placed = match(candidates, {"p1": 1, "p2": 1}, prefer={"A": "p1"})
    assert len(placed) == 2
