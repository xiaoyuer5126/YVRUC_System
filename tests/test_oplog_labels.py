"""操作日志里每个被记下来的接口都要有中文名。

名称没登记时 _label 会回落到处理函数的第一句文档串，再没有就显示英文函数名——
操作日志页上就会冒出 appeal_hours 这样的字。新增接口忘了登记，这里拦住。
"""
from __future__ import annotations

from backend.deps import current_user_download
from backend.main import app
from backend.oplog import LABELS, SKIP_HANDLERS, WRITE_METHODS


def _routes():
    """挂在 /api/ 下的全部接口。"""
    return [r for r in app.routes
            if getattr(r, "methods", None) and r.path.startswith("/api/")]


def _key(route) -> str:
    endpoint = route.endpoint
    module = (getattr(endpoint, "__module__", "") or "").rsplit(".", 1)[-1]
    return f"{module}:{endpoint.__name__}"


def _uses_download_auth(dependant) -> bool:
    """这个接口（含它依赖的下一层）是不是走直链下载那套鉴权。

    导出接口挂的是 require_manager_download 这类包装，真正的标记在
    current_user_download 里，所以要顺着依赖往下找，不能只看第一层。
    """
    for d in dependant.dependencies:
        if d.call is current_user_download or _uses_download_auth(d):
            return True
    return False


def _watch(route) -> bool:
    """这个接口会不会被操作日志记下来：写请求、以及导出/附件的直链下载。"""
    if route.methods & WRITE_METHODS:
        return route.endpoint.__name__ not in SKIP_HANDLERS
    return _uses_download_auth(route.dependant)


def test_每个被记录的接口都登记了中文名():
    routes = _routes()
    assert routes, "路由没取到，这份用例就成了空转"
    missing = [f"{sorted(r.methods)[0]} {r.path} -> {r.endpoint.__name__}"
               for r in routes
               if _watch(r)
               and _key(r) not in LABELS and r.endpoint.__name__ not in LABELS]
    assert missing == [], "操作日志里没有中文名：" + "；".join(sorted(missing))


def test_名称字典里没有失效的条目():
    """处理函数改了名、接口删了，字典里的旧条目要跟着清掉，不然越积越多。"""
    live = set()
    for r in _routes():
        live.add(_key(r))
        live.add(r.endpoint.__name__)
    dead = [k for k in LABELS if k not in live]
    assert dead == [], "名称字典里这些已经对不上任何接口：" + "、".join(sorted(dead))
