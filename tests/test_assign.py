"""一键分配：二分图匹配、硬性条件、跨场次连续性、组长。"""
from __future__ import annotations

from backend.assign import match


def test_greedy_would_strand_but_matching_does_not():
    """岗位甲不限（1）、乙限女生（1）；报名顺序女A、男B。

    贪心让女A占了甲，男B进不了乙 → 乙空着、B没岗位。
    增广路要能把女A挪到乙，让两个人都排上。
    """
    placed = match(
        candidates=[("女A", ["甲", "乙"]), ("男B", ["甲"])],
        quota={"甲": 1, "乙": 1})
    assert placed == {"女A": "乙", "男B": "甲"}


def test_first_come_wins_when_slots_run_out():
    placed = match(candidates=[("先", ["甲"]), ("后", ["甲"])], quota={"甲": 1})
    assert placed == {"先": "甲"}


def test_no_eligible_position_means_unplaced():
    placed = match(candidates=[("甲只能去A", [])], quota={"A": 1})
    assert placed == {}


def test_continuity_preference_wins_when_both_fit():
    """两个岗位都能去时，优先回到上一个场次待过的岗位。"""
    placed = match(candidates=[("老手", ["甲", "乙"])],
                   quota={"甲": 1, "乙": 1},
                   prefer={"老手": "乙"})
    assert placed["老手"] == "乙"


def test_continuity_never_costs_a_placement():
    """连续性只是偏好：会让位置的人让位，不该让别人排不上。"""
    placed = match(
        candidates=[("甲", ["A", "B"]), ("乙", ["A"])],
        quota={"A": 1, "B": 1},
        prefer={"甲": "A"})
    assert set(placed) == {"甲", "乙"}
    assert placed["乙"] == "A" and placed["甲"] == "B"
