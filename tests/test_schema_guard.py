"""启动时的库结构自检。

这个项目没有迁移工具：`create_all` 只建缺失的表、从不给已有的表加列。所以
「模型加了列、库里没有」不会在启动时报错，而是跑到某个页面才 500，前端还把它
渲染成空列表——2026-09-27、09-29、09-30 各出过一次（每次都要人去翻日志）。
这几条用例钉住那个自检：缺表、缺列都要发现，齐全时不许误报。
"""
from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table

from backend import database


class FakeInspector:
    """只实现用得到的那三个方法，避免为了造一个坏库去动真库。"""

    def __init__(self, tables: dict[str, list[str]]):
        self._tables = tables

    def has_table(self, name: str) -> bool:
        return name in self._tables

    def get_columns(self, name: str) -> list[dict]:
        return [{"name": c} for c in self._tables[name]]

    def get_table_names(self) -> list[str]:
        return list(self._tables)


def _metadata(*tables) -> MetaData:
    md = MetaData()
    for name, cols in tables:
        Table(name, md, *[Column(c, String) for c in cols], extend_existing=True)
    return md


def test_缺列会被发现():
    md = _metadata(("activities", ["id", "title", "held_start"]))
    insp = FakeInspector({"activities": ["id", "title"]})
    gaps = database.schema_gaps(md, insp)
    assert gaps == ["activities：缺列 ['held_start']"]


def test_缺整张表会被发现():
    md = _metadata(("activities", ["id"]), ("feedbacks", ["id", "body"]))
    gaps = database.schema_gaps(md, FakeInspector({"activities": ["id"]}))
    assert gaps == ["feedbacks：整张表都没有"]


def test_齐全时不误报():
    md = _metadata(("activities", ["id", "title"]))
    assert database.schema_gaps(md, FakeInspector({"activities": ["id", "title"]})) == []


def test_能看到改名留下的旧表():
    md = _metadata(("vb_confirms", ["id"]))
    insp = FakeInspector({"vb_confirms": ["id"], "vb_registrations": ["id"]})
    assert database.extra_tables(md, insp) == ["vb_registrations"]


def test_真库跟模型是一致的():
    """跑用例这个库也要对得上——它由 conftest 按模型建，按理永远一致；
    真出现差异说明有人手工动过测试库、或者 conftest 建表的方式变了。"""
    assert database.schema_gaps() == []
