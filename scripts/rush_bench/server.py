"""压测用：起单进程后端（127.0.0.1:8111），记录每次进场次锁的等锁/持锁时长。

由 run.sh 在后台拉起，client.py 压完后从 /api/_bench_stats 取统计。
持锁时长量的是「拿到锁 → with 块结束」：场次锁随事务走，块里最后那次 commit
就是放锁，所以这段基本就是真实的持锁时间。
"""
import contextlib
import os
import sys
import time

sys.path.insert(0, "/app")
os.environ["RUCVA_DISABLE_SWEEPER"] = "1"

import uvicorn  # noqa: E402

from backend import locks  # noqa: E402
from backend.main import app  # noqa: E402

stats = []      # (等锁秒数, 持锁秒数, 是否持号)
_orig = locks.shift_lock


@contextlib.contextmanager
def timed(db, *shift_ids, **kw):
    t0 = time.perf_counter()
    with _orig(db, *shift_ids, **kw):
        t1 = time.perf_counter()
        try:
            yield
        finally:
            stats.append((t1 - t0, time.perf_counter() - t1, bool(kw.get("wait"))))


# 各路由是 `from ..locks import shift_lock` 拿的名字，得逐个模块换掉
for mod in list(sys.modules.values()):
    if (getattr(mod, "__name__", "").startswith("backend")
            and getattr(mod, "shift_lock", None) is _orig):
        mod.shift_lock = timed


@app.get("/api/_bench_stats")
async def _s():
    return stats


uvicorn.run(app, host="127.0.0.1", port=8111, log_level="warning")
