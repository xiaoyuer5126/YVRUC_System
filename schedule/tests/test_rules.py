"""规则配置的校验与预设。

这个模块是「几十万种组合」的入口，坏配置在这里拦不住的话，后面每一处都要
自己防一遍。所以校验要严:未知的键、重复的项、关掉不许关的，一律当场拒。
"""
from __future__ import annotations

import pytest

from app import rules
from app.core import Position, Project, Volunteer, solve


def test_空配置用默认预设():
    for empty in (None, {}, ""):
        cfg = rules.normalize(empty)
        assert cfg["preset"] == rules.DEFAULT_PRESET
        assert cfg == rules.from_preset(rules.DEFAULT_PRESET)


def test_三套预设都是合法配置():
    for name in rules.PRESETS:
        cfg = rules.from_preset(name)
        assert rules.normalize(cfg) == cfg


def test_先到先得不许关():
    """它是唯一能对志愿者公开交代的规则，允许关掉等于允许暗箱操作。"""
    cfg = rules.from_preset("balanced")
    cfg["order"] = [k for k in cfg["order"] if k != "signup_time"]
    with pytest.raises(rules.RuleError, match="先到先得"):
        rules.normalize(cfg)


def test_先到先得的位置可以变():
    """不许关，但排在摊平前面是允许的——那就是纯先到先得，一种真实的办法。"""
    cfg = rules.normalize({"preset": "custom", "order": ["signup_time", "spread"],
                           "candidate": ["gap"], "toggles": {}})
    assert rules.order_fields(cfg) == ("signup_time", "spread")
    assert rules.round_cap(cfg) is False


def test_坏配置一律当场拒():
    base = rules.from_preset("balanced")
    for bad, why in (
        ({**base, "order": [*base["order"], "没这条规则"]}, "未知规则"),
        ({**base, "candidate": ["gap", "gap"]}, "重复项"),
        ({**base, "toggles": {"没这个开关": True}}, "未知开关"),
        ({**base, "preset": "没这套预设"}, "未知预设"),
        ({**base, "order": "不是数组"}, "类型不对"),
    ):
        with pytest.raises(rules.RuleError):
            rules.normalize(bad)
    with pytest.raises(rules.RuleError):
        rules.normalize("整个不是对象")


def test_候选排序可以全关_只按岗位表顺序():
    """极端但合法：C 层全关，岗位表顺序那个兜底还在，结果依然是确定性的。"""
    cfg = rules.normalize({"preset": "custom", "order": ["signup_time"],
                           "candidate": [], "toggles": {}})
    prj = Project()
    for i in range(3):
        prj.positions[f"P{i}"] = Position(pid=f"P{i}", name=f"P{i}", quota=1)
    key = "甲138"
    prj.volunteers[key] = Volunteer(key=key, name="甲", contact="138")
    for pid in prj.positions:
        prj.add_signup(key, pid, "2026-07-20 09:01")
    prj.rules = cfg

    first = [(a.pid, a.vol_key) for a in solve(prj)]
    assert first == [(a.pid, a.vol_key) for a in solve(prj)]
    assert first[0][0] == "P0", "全关之后就该按岗位表顺序挑"


def test_describe_说得出基于哪套改的():
    cfg = rules.from_preset("floor_first")
    assert "保底优先" in rules.describe(cfg)[0]
    cfg["preset"] = "custom"
    assert "自定义" in rules.describe(cfg)[0]
    assert any("先到先得" in line for line in rules.describe(cfg))


def test_关掉一个开关会在说明里写出来():
    cfg = rules.from_preset("balanced")
    cfg["toggles"]["cohort_swap"] = False
    assert any("已关闭" in line for line in rules.describe(cfg))
