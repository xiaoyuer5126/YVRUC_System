"""条件树:校验、求值、体检。

这套东西是必选条件、优先条件、小组长资格三处共用的地基,判错一条就是
「明明报了名却排不上」或者「不该进的进来了」,所以边界要压实。
"""
from __future__ import annotations

import pytest

from app.conditions import (ConditionError, check, describe, from_pairs, match,
                            normalize, tags_used)


def tree(*items):
    return normalize({"all": list(items)})


def leaf(tag, op, value=None):
    return {"tag": tag, "op": op} if value is None else {"tag": tag, "op": op, "value": value}


# ————————————————————————————————————————————— 求值

def test_空条件恒通过():
    for empty in (None, {}, {"all": []}):
        assert match(empty, {}) is True
        assert match(empty, {"性别": "男"}) is True


def test_多条是与的关系_少一条就不算():
    t = tree(leaf("性别", "eq", "女"), leaf("年级", "eq", "2023"))
    assert match(t, {"性别": "女", "年级": "2023"})
    assert not match(t, {"性别": "女", "年级": "2024"})
    assert not match(t, {"性别": "女"})


def test_或组里命中一条就够():
    t = tree({"any": [leaf("年级", "eq", "2023"), leaf("年级", "eq", "2024")]})
    assert match(t, {"年级": "2023"})
    assert match(t, {"年级": "2024"})
    assert not match(t, {"年级": "2025"})


def test_不等于_对没填的标签也成立():
    """「年级≠2025」时没填年级的人算不算?算。

    这不是显然的,所以钉死:`ne` 判的是「这个值不是它」,没填就不是它。
    要排除没填的人,再加一条 filled。
    """
    t = tree(leaf("年级", "ne", "2025"))
    assert match(t, {"年级": "2023"})
    assert not match(t, {"年级": "2025"})
    assert match(t, {})

    both = tree(leaf("年级", "ne", "2025"), leaf("年级", "filled"))
    assert not match(both, {})


def test_属于与不属于():
    t = tree(leaf("年级", "in", ["2023", "2024"]))
    assert match(t, {"年级": "2023"})
    assert not match(t, {"年级": "2025"})
    assert not match(tree(leaf("年级", "not_in", ["2023"])), {"年级": "2023"})


def test_包含是子串():
    t = tree(leaf("技能", "contains", "急救"))
    assert match(t, {"技能": "急救证;摄影"})
    assert not match(t, {"技能": "摄影"})


def test_已填写与未填写():
    assert match(tree(leaf("健康证明", "filled")), {"健康证明": "已交"})
    assert not match(tree(leaf("健康证明", "filled")), {"健康证明": "  "})
    assert match(tree(leaf("健康证明", "empty")), {})
    assert not match(tree(leaf("健康证明", "empty")), {"健康证明": "已交"})


def test_数值比较():
    t = tree(leaf("服务时长", "gte", 20))
    assert match(t, {"服务时长": "20"})
    assert match(t, {"服务时长": "35.5"})
    assert not match(t, {"服务时长": "19"})
    assert match(tree(leaf("服务时长", "between", [10, 20])), {"服务时长": "15"})
    assert not match(tree(leaf("服务时长", "between", [10, 20])), {"服务时长": "25"})


def test_数值转不动就判不满足_而不是报错():
    """脏数据是常态。求解不能因为一个「20小时」就崩,但也不能悄悄放行。"""
    t = tree(leaf("服务时长", "gte", 20))
    for dirty in ("20小时", "二十", "", "—"):
        assert match(t, {"服务时长": dirty}) is False


def test_字符串比较归一化_全角半角大小写空格():
    assert match(tree(leaf("性别", "eq", "女")), {"性别": " 女 "})
    assert match(tree(leaf("班级", "eq", "A1")), {"班级": "ａ１"})
    assert match(tree(leaf("性别", "eq", "Female")), {"性别": "female"})
    assert match(tree(leaf("服务时长", "gte", 20)), {"服务时长": "２５"})


# ————————————————————————————————————————————— 校验

def test_阈值填错当场拒_不等到求解():
    """条件里的阈值是人在网页上填的一个数,没有「脏数据」一说。

    静默容忍的话,表现是「这个岗位一个人都排不上」而没人知道为什么。
    """
    with pytest.raises(ConditionError):
        tree(leaf("服务时长", "gte", "二十"))
    with pytest.raises(ConditionError):
        tree(leaf("服务时长", "between", [1]))
    with pytest.raises(ConditionError):
        tree(leaf("性别", "eq", ""))
    with pytest.raises(ConditionError):
        tree(leaf("", "eq", "女"))
    with pytest.raises(ConditionError):
        tree(leaf("年级", "没这个运算符", "x"))
    with pytest.raises(ConditionError):
        tree(leaf("年级", "in", []))


def test_最多两层():
    with pytest.raises(ConditionError):
        normalize({"all": [{"any": [{"any": [leaf("年级", "eq", "2023")]}]}]})
    with pytest.raises(ConditionError):
        normalize({"any": [leaf("年级", "eq", "2023")]})


def test_归一化会把阈值转成数字_省得每次求值都转():
    t = tree(leaf("服务时长", "gte", "20"))
    assert t["all"][0]["value"] == 20.0
    assert tree(leaf("时长", "between", [20, 10]))["all"][0]["value"] == [10.0, 20.0]


# ————————————————————————————————————————————— 体检与说明

def test_体检只报判不出来的_不报不满足():
    t = tree(leaf("服务时长", "gte", 20), leaf("性别", "eq", "女"))
    assert check(t, {"服务时长": "35", "性别": "男"}) == []      # 不满足,但判得出来
    problems = check(t, {"服务时长": "20小时", "性别": "女"})
    assert len(problems) == 1
    assert "20小时" in problems[0] and "不是数字" in problems[0]
    assert "没填" in check(t, {"性别": "女"})[0]


def test_说明拼成人话():
    t = tree(leaf("性别", "eq", "女"),
             {"any": [leaf("年级", "eq", "2023"), leaf("年级", "eq", "2024")]},
             leaf("服务时长", "gte", 20))
    assert describe(t) == "性别=女 且 (年级=2023 或 年级=2024) 且 服务时长≥20"
    assert describe(None) == ""


def test_用到哪些标签():
    t = tree(leaf("性别", "eq", "女"),
             {"any": [leaf("年级", "eq", "2023"), leaf("学院", "eq", "信息")]})
    assert tags_used(t) == {"性别", "年级", "学院"}


def test_from_pairs_是给示例和测试的便捷构造():
    assert match(from_pairs({"性别": "女"}), {"性别": "女"})
    assert not match(from_pairs({"性别": "女"}), {"性别": "男"})
    assert from_pairs({}) == {"all": []}
