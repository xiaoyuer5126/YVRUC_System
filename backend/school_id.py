"""学号 / 工号解析。

人大**学号 10 位**：前 4 位年级 + 中间 2 位学段 + 后 4 位序号。
例 `2025201717` → 2025 年入学、本科、序号 1717。
**教工号 8 位**，例 `20200001`——位数不同，足以和学号区分。

开放平台的 OAuth `user/profiles` 不返回年级和学段，只能从账号本身推导，
这里是这两个字段的唯一来源。规则由用户 2026-09-21 口述；留学生中间两位
可能与现有对照表不同（用户说「到时候再说」），所以认不出的取值一律留空，
**不硬猜**——猜错的学段会让人被错误的招募范围静默筛掉，且查不出原因。
"""
from __future__ import annotations

from . import config

STUDENT_ID_LEN = 10
STAFF_ID_LEN = 8


def parse(sid: str) -> tuple[str, str, str]:
    """→ (user_type, grade, stage)。推不出来的部分留空。"""
    sid = (sid or "").strip()
    if not sid.isdigit():
        return "student", "", ""
    if len(sid) == STAFF_ID_LEN:
        return "teacher", "", ""
    if len(sid) == STUDENT_ID_LEN:
        return "student", sid[:4], config.STAGE_BY_CODE.get(sid[4:6], "")
    # 位数既不是 8 也不是 10：仍按学生放进来，年级学段留空待人工核对
    return "student", "", ""


def user_type_of(sid: str) -> str:
    return parse(sid)[0]
