"""压测用：起单进程后端（127.0.0.1:8111），记录每次进场次锁的等锁/持锁时长。

由 run.sh 在后台拉起，client.py 压完后从 /api/_bench_stats 取统计。
持锁时长量的是「拿到锁 → with 块结束」：场次锁随事务走，块里最后那次 commit
就是放锁，所以这段基本就是真实的持锁时间。

另外把持锁期间的时间按步骤拆开（/api/_bench_profile）：锁里调的几个函数各花
多久（含它们自己发的 SQL）、每类 SQL 各花多久、flush 与 commit 各花多久。
要缩短持锁时间，先看钱花在哪儿。函数之间有嵌套（比如 commit 里含 flush），
各项加起来会比持锁总时长多，按项看，别求和。
"""
import contextlib
import contextvars
import os
import re
import sys
import time
from collections import defaultdict

sys.path.insert(0, "/app")
os.environ["RUCVA_DISABLE_SWEEPER"] = "1"

import uvicorn  # noqa: E402

from backend import locks  # noqa: E402
from backend.main import app  # noqa: E402

stats = []      # (等锁秒数, 持锁秒数, 是否持号)
prof = []       # 每次持锁一条：{步骤: 秒数}
_orig = locks.shift_lock
_rec = contextvars.ContextVar("bench_rec", default=None)


@contextlib.contextmanager
def timed(db, *shift_ids, **kw):
    t0 = time.perf_counter()
    with _orig(db, *shift_ids, **kw):
        t1 = time.perf_counter()
        rec = defaultdict(float)
        tok = _rec.set(rec)
        try:
            yield
        finally:
            _rec.reset(tok)
            stats.append((t1 - t0, time.perf_counter() - t1, bool(kw.get("wait"))))
            prof.append(dict(rec))


def _add(key, dt):
    rec = _rec.get()
    if rec is not None:
        rec[key] += dt
        rec[key + " ·次数"] += 1


def _wrap(owner, name, label):
    real = getattr(owner, name)

    def inner(*a, **kw):
        t = time.perf_counter()
        try:
            return real(*a, **kw)
        finally:
            _add(label, time.perf_counter() - t)
    setattr(owner, name, inner)


# —— 锁里调的函数（包含它们自己发的 SQL）——
from sqlalchemy import event  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from backend import enroll_logic  # noqa: E402
from backend.database import engine  # noqa: E402
from backend.integrations import msgcenter  # noqa: E402
from backend.routers import activities  # noqa: E402

for fn in ("has_room", "waitlist_rows", "record_change", "sync_attached",
           "attached_shifts", "waitlist_position"):
    _wrap(enroll_logic, fn, f"fn {fn}")
_wrap(msgcenter, "send_user", "fn msgcenter.send_user")
_wrap(activities, "_settle_gates", "fn _settle_gates (Redis)")
_wrap(Session, "flush", "session.flush")
_wrap(Session, "commit", "session.commit（含 flush、落盘、提交后钩子）")


# —— 每类 SQL ——
_TABLE = re.compile(r"\b(?:FROM|INTO|UPDATE)\s+(\w+)", re.I)


@event.listens_for(engine, "before_cursor_execute")
def _sql_start(conn, cursor, statement, params, context, executemany):
    conn.info["bench_t"] = time.perf_counter()


@event.listens_for(engine, "after_cursor_execute")
def _sql_end(conn, cursor, statement, params, context, executemany):
    t = conn.info.pop("bench_t", None)
    if t is None:
        return
    verb = statement.lstrip().split(None, 1)[0].upper()
    m = _TABLE.search(statement)
    _add(f"sql {verb} {m.group(1) if m else ''}".rstrip(), time.perf_counter() - t)


# 各路由是 `from ..locks import shift_lock` 拿的名字，得逐个模块换掉
for mod in list(sys.modules.values()):
    if (getattr(mod, "__name__", "").startswith("backend")
            and getattr(mod, "shift_lock", None) is _orig):
        mod.shift_lock = timed


@app.get("/api/_bench_stats")
async def _s():
    return stats


@app.get("/api/_bench_profile")
async def _p():
    return prof


uvicorn.run(app, host="127.0.0.1", port=8111, log_level="warning")
