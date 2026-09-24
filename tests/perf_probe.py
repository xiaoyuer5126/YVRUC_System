"""量一量：管理端进一个活动，那一串请求各自发了多少条 SQL、多大、多慢。

不是断言用例，是诊断用的——`pytest tests/perf_probe.py -s` 才会打印。
借 conftest 的夹具：它会建库、种数据、给好登录态，比在外面另搭一套省事。

用法（要看表就得带 -s，否则 pytest 把 stdout 吞了）：
    docker compose --profile test run --rm --build tests \
        tests/perf_probe.py -s
"""
from __future__ import annotations

import time

from sqlalchemy import event

from backend.database import engine
from conftest import ADMIN

hits: dict = {"n": 0, "sql": []}


@event.listens_for(engine, "before_cursor_execute")
def _count(conn, cursor, statement, params, context, executemany):
    hits["n"] += 1
    hits["sql"].append(" ".join(statement.split())[:78])


def _measure(client, headers, label, path):
    hits["n"], hits["sql"] = 0, []
    t0 = time.perf_counter()
    r = client.get(path, headers=headers)
    dt = (time.perf_counter() - t0) * 1000
    return (label, r.status_code, hits["n"], len(r.content), dt, list(hits["sql"]))


def test_量各接口的_SQL_条数(client, admin, db):
    from backend.models import Activity, ActivityManager, User

    me = db.query(User).filter(User.student_id == ADMIN).one()
    acts = (db.query(Activity)
            .join(ActivityManager, ActivityManager.activity_id == Activity.id)
            .filter(ActivityManager.user_id == me.id).all())
    assert acts, "演示数据里这个管理员名下没有活动"
    act = max(acts, key=lambda a: len(a.main_shifts()))
    shifts = act.main_shifts()

    rows = [_measure(client, admin, "活动详情", f"/api/activities/{act.id}")]
    for i, g in enumerate(shifts, 1):
        rows.append(_measure(client, admin, f"场次名单 {i}/{len(shifts)}",
                             f"/api/staff/shifts/{g.id}/roster"))
    for label, path in [
        ("审计日志 50 条", f"/api/staff/audit-logs?activity_id={act.id}&size=50"),
        ("报名问卷（只要题数）", f"/api/staff/activities/{act.id}/form"),
        ("管理者列表", f"/api/staff/activities/{act.id}/managers"),
        ("时长申诉", f"/api/staff/activities/{act.id}/hours-appeals"),
        ("审批状态", f"/api/staff/activities/{act.id}/approval"),
        ("签到看板", f"/api/staff/shifts/{shifts[0].id}/board"),
        ("服装台账", f"/api/staff/activities/{act.id}/attire"),
        ("时长预览", f"/api/staff/activities/{act.id}/hours"),
        ("通知列表", "/api/me/notifications"),
        ("通知未读数（每 30 秒轮询）", "/api/me/notifications/unread"),
    ]:
        rows.append(_measure(client, admin, label, path))

    print(f"\n活动：{act.title[:26]}（{len(shifts)} 个主场次）")
    print(f"{'接口':24} {'码':>4} {'SQL':>5} {'响应':>9} {'耗时':>8}")
    print("-" * 56)
    for label, code, n, size, dt, _ in rows:
        print(f"{label:24} {code:>4} {n:>5} {size / 1024:>7.1f}KB {dt:>6.0f}ms")
    print("-" * 56)
    print(f"{'合计':24} {'':>4} {sum(r[2] for r in rows):>5} "
          f"{sum(r[3] for r in rows) / 1024:>7.1f}KB")

    # 响应里各字段占多少字节：谁在撑大 payload，不看一眼是猜的
    import json as _json
    d = client.get(f"/api/activities/{act.id}", headers=admin).json()
    whole = len(_json.dumps(d, ensure_ascii=False).encode())
    att = cnt = 0
    n_pos = n_with_mat = 0
    for g in d.get("shifts", []):
        for p in g.get("positions", []):
            n_pos += 1
            att += len(_json.dumps(p.get("attachments") or [], ensure_ascii=False).encode())
            if p.get("material_count"):
                n_with_mat += 1
    print(f"\n活动详情响应 {whole/1024:.1f}KB，其中：")
    print(f"  各岗位 attachments 合计 {att/1024:.1f}KB"
          f"（{n_pos} 个岗位，{n_with_mat} 个有材料）")
    print(f"  其余 {whole/1024 - att/1024:.1f}KB（岗位树、场次、问卷、日志…）")

    worst = max(rows, key=lambda x: x[2])
    print(f"\n最费 SQL 的：{worst[0]}（{worst[2]} 条），它在重复查什么：")
    seen: dict[str, int] = {}
    for s in worst[5]:
        seen[s] = seen.get(s, 0) + 1
    for s, c in sorted(seen.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  ×{c:<4} {s}")
