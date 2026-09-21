"""消息模板与调用处的参数必须对得上。

模板缺参时只会在正文里留一串「{shift}」发给志愿者，接口照样 200，
测试也照样通过——除非专门查。这里静态扫描所有 send_user 调用处，
把它传的关键字与模板占位符逐一比对。
"""
from __future__ import annotations

import ast
import pathlib
import re

from backend.integrations.msgcenter import TEMPLATES, render

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
# render 自己补的，调用方不用传
IMPLICIT = {"extra"}


def _placeholders(template_key: str) -> set[str]:
    title, body = TEMPLATES[template_key]
    return set(re.findall(r"\{(\w+)", title + body)) - IMPLICIT


def _call_sites():
    """(文件, 行号, 模板名, 传入的关键字集合)。"""
    for path in BACKEND.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "send_user" or len(node.args) < 3:
                continue
            key = node.args[2]
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            kwargs = {k.arg for k in node.keywords if k.arg}
            yield path.name, node.lineno, key.value, kwargs


def test_每个调用处都能填满模板():
    problems = []
    for fname, lineno, key, kwargs in _call_sites():
        if key not in TEMPLATES:
            problems.append(f"{fname}:{lineno} 用了不存在的模板 {key}")
            continue
        missing = _placeholders(key) - kwargs
        if missing:
            problems.append(
                f"{fname}:{lineno} 模板 {key} 缺参数 {sorted(missing)}"
                f"（正文里会留下花括号）")
    assert not problems, "\n" + "\n".join(problems)


def test_模板里的占位符都被用上():
    """模板改了字段名而调用处没改，会留下没人填的占位符。"""
    used: dict[str, set[str]] = {}
    for _f, _l, key, kwargs in _call_sites():
        used.setdefault(key, set()).update(kwargs)
    for key, placeholders in ((k, _placeholders(k)) for k in TEMPLATES):
        if key not in used:
            continue          # custom 之类由别处组装
        extra = placeholders - used[key]
        assert not extra, f"模板 {key} 的 {sorted(extra)} 没有任何调用处提供"


def test_签到提醒渲染完整():
    """这条真出过事：正文里留了 {shift} 和 {position} 发给了志愿者。"""
    title, body = render("checkin_reminder", activity="校庆晚会", shift="当日班",
                         position="观众引导", loc="北门")
    assert "{" not in title and "{" not in body, body
    assert "当日班" in body and "观众引导" in body
