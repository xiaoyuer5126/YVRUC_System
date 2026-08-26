"""读路径必须归一化。

`_detail` 曾经把数据库里那份原始 JSONB 直接下发（`data=row.data`），出过两次事，
两次都是**前端崩掉或按钮点不动，而后端一切正常**——所以后端测试全绿也发现不了：

1. **缺字段。** `caps` 是后加的，之前建的项目 JSON 里没有这个键，
   前端 `draft.caps.push(...)` 抛 TypeError，请求都发不出去。
2. **键顺序被重排。** PostgreSQL 的 jsonb 不保留对象键顺序（按「先长度、
   再字节」重排）。规则页拿「读回来的那份」和「试算现算的那份」做
   `JSON.stringify` 全等比较，两边顺序不同 → 永远不等 →
   「保存这套规则」从上线起就没能点亮过。

这个文件不碰数据库：`_detail` 只需要一个有 `data` 的行对象，而这两个 bug 恰恰
都只和「那份 dict 长什么样」有关。真跑 Postgres 的接口测试在 test_routes.py，
但它默认是跳过的——正好是这类问题溜过去的原因。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

from app import rules as rulecfg
from app.auth import Identity
from app.core import Project
from app.models import ScheduleProject
from app.routes.projects import _detail

# 前端 ProjectData 声明的全部字段（apps/web/src/api/tools/schedule.ts）。
# 少任何一个，页面上对应的操作就会在 `.push` / `.filter` 上抛 TypeError。
FRONTEND_FIELDS = {
    "version", "positions", "volunteers", "signups",
    "conflicts", "cohorts", "caps", "assignments", "prev_pairs", "rules",
}


def _row(data: dict) -> ScheduleProject:
    """一个不入库的行对象。`_detail` 不需要 session。"""
    return ScheduleProject(
        id=uuid.uuid4(),
        ruc_user_id=uuid.uuid4(),
        owner_nickname="小明",
        name="测试项目",
        data=data,
        version=1,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=180),
    )


def _viewer(user_id: uuid.UUID) -> Identity:
    return Identity(user_id, "小明", "1@ruc.edu.cn", "user")


def test_老项目缺字段也会被补齐():
    """`caps` / `rules` 都是后加的。老项目的 JSON 里没有，读回来必须有。"""
    row = _row({"version": 1, "positions": [], "volunteers": [], "signups": [],
                "conflicts": [], "assignments": []})  # 没有 caps / cohorts / prev_pairs / rules

    data = _detail(row, _viewer(row.ruc_user_id)).data

    assert FRONTEND_FIELDS <= set(data), f"少了：{FRONTEND_FIELDS - set(data)}"
    # 前端会直接 .push / .filter，所以必须是数组而不是 None。
    for key in ("caps", "cohorts", "prev_pairs", "positions", "assignments"):
        assert isinstance(data[key], list), key


def test_空项目也是完整形状():
    data = _detail(_row({}), _viewer(uuid.uuid4())).data
    assert FRONTEND_FIELDS <= set(data)


def test_规则的键顺序和试算现算的那份一致():
    """**这是那个「保存规则点不亮」的回归测试。**

    读回来的 rules 和试算响应里的 rules 必须逐字节相同——包括键顺序。前端把
    这两份拿去做字符串全等比较，顺序不同就等于「你还没试算」。
    """
    # jsonb 存完再读出来是这个顺序：先按键名长度，再按字节。
    jsonb_order = {
        "order": ["round_cap", "spread", "signup_time"],
        "preset": "custom",
        "toggles": {"cohort_swap": True, "preference_phase": True},
        "candidate": ["cohort", "min_quota", "gap", "scarce", "preference"],
    }
    row = _row({**Project().to_dict(), "rules": jsonb_order})

    from_read = _detail(row, _viewer(row.ruc_user_id)).data["rules"]
    # 试算走的就是这一句，见 routes.preview_rules。
    from_preview = rulecfg.normalize(jsonb_order)

    assert json.dumps(from_read) == json.dumps(from_preview), (
        "读路径和试算路径的 rules 形状不一致，规则页的保存按钮会永远点不亮"
    )


def test_坏掉的条件树不会被原样漏出去():
    """读路径顺带做了校验：存进去的脏值读回来是归一化过的。

    这条不是为了防谁，是说明「读也走 from_dict」还有一个附带好处——
    数据库里万一有一份手改坏的 JSON，前端拿到的仍然是它认得的形状。
    """
    row = _row({**Project().to_dict(), "conflicts": [["A", "B"], ["B", "A"]]})
    data = _detail(row, _viewer(row.ruc_user_id)).data
    # 同一对冲突正反各写一次，归一化之后只剩一条。
    assert data["conflicts"] == [["A", "B"]]
