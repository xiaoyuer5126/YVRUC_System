"""消息模板与调用处的参数必须对得上。

模板缺参时只会在正文里留一串「{shift}」发给志愿者，接口照样 200，
测试也照样通过——除非专门查。这里静态扫描所有发模板消息的调用处
（send_user 本身，以及 notify_enrolled 这类转手的包装函数），
把它传的关键字与模板占位符逐一比对。
"""
from __future__ import annotations

import ast
import logging
import pathlib
import re

from backend.integrations.msgcenter import TEMPLATES, render

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"
# render 自己补的，调用方不用传
IMPLICIT = {"extra"}
# 模板名由调用方传进来、再原样转给 send_user 的函数 → 模板名是第几个位置参数。
# 新写一个这样的包装函数要登记在这里，否则 test_模板名都是字面量 会报出来。
WRAPPERS = {"send_user": 2, "notify_enrolled": 2, "_notify_activity_bg": 1}


def _placeholders(template_key: str) -> set[str]:
    title, body = TEMPLATES[template_key]
    return set(re.findall(r"\{(\w+)", title + body)) - IMPLICIT


def _name(fn) -> str:
    return fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")


def _template_call(node: ast.Call):
    """发模板消息的调用 → (模板名节点, 关键字列表)；不是的话 None。

    bg.add_task(_notify_activity_bg, act.id, "activity_changed", ...) 这种
    后台任务的写法，模板名在函数本身之后，下标整体往后挪一位。
    """
    name, args = _name(node.func), node.args
    if name == "add_task" and args and _name(args[0]) in WRAPPERS:
        name, args = _name(args[0]), args[1:]
    idx = WRAPPERS.get(name)
    if idx is None or len(args) <= idx:
        return None
    return args[idx], node.keywords


def _scan():
    """(文件, 行号, 模板名或 None, 传入的关键字集合, 有没有 **kw 转手)。

    模板名不是字面量的调用只在登记过的包装函数体内允许，yield 时给 None。
    """
    for path in BACKEND.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        in_wrapper: set[int] = set()
        for fn in ast.walk(tree):
            if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and fn.name in WRAPPERS):
                in_wrapper.update(id(n) for n in ast.walk(fn))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            hit = _template_call(node)
            if hit is None or id(node) in in_wrapper:
                continue
            key, keywords = hit
            literal = isinstance(key, ast.Constant) and isinstance(key.value, str)
            yield (path.name, node.lineno, key.value if literal else None,
                   {k.arg for k in keywords if k.arg},
                   any(k.arg is None for k in keywords))


def _call_sites():
    return [(f, l, k, kw) for f, l, k, kw, _ in _scan() if k is not None]


def test_模板名都是字面量():
    """模板名是变量的话静态扫描看不见它，这一整套检查就被绕过去了。"""
    bad = [f"{f}:{l}" + (" 模板名不是字面量" if k is None else " 用 **kw 转手参数")
           for f, l, k, _kw, splat in _scan() if k is None or splat]
    assert not bad, ("\n" + "\n".join(bad)
                     + "\n（真要转手就把这个函数登记进 WRAPPERS）")


def test_扫描认得出包装函数的调用处():
    """扫描本身失灵（比如包装函数改了名）时，下面几条会一起变成空转。"""
    keys = {k for _f, _l, k, _kw in _call_sites()}
    assert "activity_changed" in keys, "notify_enrolled / 后台任务那条路没扫到"
    assert "checkin_reminder" in keys and "custom" in keys


def test_每个模板都有人用():
    """没有调用处的模板要么是死代码，要么是调用处换了写法、扫描漏了。"""
    used = {k for _f, _l, k, _kw in _call_sites()}
    unused = set(TEMPLATES) - used
    assert not unused, f"这些模板没有任何调用处：{sorted(unused)}"


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
    for key in TEMPLATES:
        extra = _placeholders(key) - used.get(key, set())
        assert not extra, f"模板 {key} 的 {sorted(extra)} 没有任何调用处提供"


def test_运行时缺参会记错误日志(caplog):
    """静态扫描之外的兜底：真缺了参，至少日志里有一行能搜到。"""
    with caplog.at_level(logging.ERROR, logger="backend.integrations.msgcenter"):
        _title, body = render("signup_success", activity="校庆晚会")
    assert "{shift}" in body, "缺参时保留占位，通知照样发得出去"
    msgs = [r.getMessage() for r in caplog.records]
    assert any("signup_success" in m and "shift" in m for m in msgs), msgs


def test_签到提醒渲染完整():
    """这条真出过事：正文里留了 {shift} 和 {position} 发给了志愿者。"""
    title, body = render("checkin_reminder", activity="校庆晚会", shift="当日班",
                         position="观众引导", loc="北门")
    assert "{" not in title and "{" not in body, body
    assert "当日班" in body and "观众引导" in body
