"""学号 / 工号解析。

人大**学号 10 位**：前 4 位年级 + 中间 2 位学段 + 后 4 位序号。
例 `2025201717` → 2025 年入学、本科、序号 1717。
**教工号 8 位**，例 `20200001`——位数不同，足以和学号区分。

**这是兜底，不是主路径。** 2026-09-21 真机实测发现，OAuth `user/profiles`
其实直接返回 `enterGrade`（年级）与 `childCategoryName`（学段），学校说了算的
那份比按学号推准得多，留学生中间两位对不上对照表的问题也一并没有了。
所以调用方优先用学校的值，只有在学校没给、或给了个 `config.STAGES` 里没有的
取值时，才退到这里推导（见 integrations/sso.py 的 `_identity`）。

规则由用户 2026-09-21 口述。认不出的取值一律留空，**不硬猜**——猜错的学段
会让人被错误的招募范围静默筛掉，且事后查不出原因。
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
