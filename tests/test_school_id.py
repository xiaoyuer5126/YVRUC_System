"""学号 / 工号解析。

年级与学段在开放平台的档案里没有，只能从账号本身推——推错了不会报错，
只会让人被错误的招募范围静默筛掉，所以边界要钉住。
"""
from __future__ import annotations

import pytest

from backend import config, school_id


@pytest.mark.parametrize("sid,stage", [
    ("2025001717", "博士研究生"),
    ("2025101717", "硕士研究生"),
    ("2025201717", "本科生"),
])
def test_中间两位决定学段(sid, stage):
    user_type, grade, parsed = school_id.parse(sid)
    assert (user_type, grade, parsed) == ("student", "2025", stage)


def test_用户本人的学号():
    assert school_id.parse("2025201717") == ("student", "2025", "本科生")


def test_教工号按位数区分():
    """8 位是教工号，与 10 位学号不会混淆。"""
    assert school_id.parse("20200001") == ("teacher", "", "")
    assert school_id.parse("2025201717")[0] == "student"


def test_认不出的学段留空不硬猜():
    """留学生的中间两位可能与对照表不同（用户说「到时候再说」）。

    猜成某个学段的代价是：这个人被错误的招募范围筛掉，而且没人看得出
    是哪里错了。留空至少是「未知」，能被发现。
    """
    assert school_id.parse("2025991717")[2] == ""
    assert school_id.parse("2025991717")[1] == "2025"


def test_映射表与_STAGES_同源():
    """取值必须落在 STAGES 里，否则筛选条件永远匹配不上。"""
    assert set(config.STAGE_BY_CODE.values()) <= set(config.STAGES)


@pytest.mark.parametrize("bad", ["", "   ", "abcdefghij", "202520171", "2025",
                                 "20252017171", None])
def test_奇怪的输入不抛异常(bad):
    user_type, grade, stage = school_id.parse(bad)
    assert user_type == "student"
    assert grade == "" and stage == ""


def test_位数不对仍然按学生放进来():
    """推不出年级学段不等于要拦住人——先能登录，资料由本人和管理员补。"""
    assert school_id.parse("1234567")[0] == "student"
