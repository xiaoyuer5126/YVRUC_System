"""违规登记与限制准入建议（《志愿者服务规范·限制准入标准》）。

规范的口径是「违规类型 × 活动类型 × 累计次数 → 不得参与哪几类活动」，
不涉及扣分——信用分是《设计方案》另立的一套，两者互不干涉。

**所有限制时长统一为 RESTRICTION_MONTHS 个月**：规范正文里显式写出的只有
「三个月内不得参与 X 类」，基础限制（「不得参与 CD 类活动」）的时长在原文中
是一张图片，无法提取。取同一个时长后，规范里的复合处罚正好可以合并表达，
例如「不得参与CD + 三个月内不得参与B」= 三个月内不得参与 B/C/D。

系统只给**建议**：规范里大量条款需要人判断（「视造成的不良后果」「特殊情况
可不予追究」「有无证明」），最终生效的类型与截止日由操作者确认。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session as OrmSession

from . import config
from .models import User, ViolationRecord, now

# 违规类型：代码 → 展示名。失联与消极志愿在规范里属同一条款，共用一套阶梯。
KINDS = {
    "late": "迟到早退",
    "leave": "无故请假",
    "contact": "无法取得联系",
    "passive": "消极志愿",
    "other": "其他",
}
SHARED_LADDER = {"contact": "contact", "passive": "contact"}

ALL_TYPES = "ABCD"


def _types(spec: str) -> list[str]:
    return list(spec)


def suggest(db: OrmSession, user: User, activity_type: str, kind: str, *,
            has_proof: bool = False) -> dict:
    """按规范算出建议的限制准入。

    返回 {types, until, basis, notes, history}：
      types   建议限制的活动类型
      until   建议截止日
      basis   依据的条文（给操作者核对，也写进留痕）
      notes   需要人判断、系统不敢替你定的部分
      history 该志愿者此前同类违规次数（含本次）
    """
    ladder_kind = SHARED_LADDER.get(kind, kind)
    bucket = "CD" if activity_type in ("C", "D") else "AB"

    # 累计次数只数同一条款下的历史记录，本次也算进去
    prior = (db.query(ViolationRecord)
             .filter(ViolationRecord.user_id == user.id).all())
    count = 1 + sum(1 for v in prior
                    if SHARED_LADDER.get(v.kind, v.kind) == ladder_kind
                    and ("CD" if v.activity_type in ("C", "D") else "AB") == bucket)

    types, basis, notes = _ladder(ladder_kind, bucket, count, has_proof)

    # 与既有限制合并：类型取并集，截止日取更晚的那个
    until = now() + timedelta(days=30 * config.RESTRICTION_MONTHS)
    merged = sorted(set(types) | set(user.restricted_types or []))
    if user.restricted_until and user.restricted_until > until:
        until = user.restricted_until
    if user.restricted_types and set(user.restricted_types) - set(types):
        notes.append(f"该志愿者原有限制（{'/'.join(user.restricted_types)}）已并入建议，"
                     f"如需撤销请手动去掉")

    return {
        "types": merged, "until": until, "basis": basis, "notes": notes,
        "history_count": count, "bucket": bucket,
        "kind_label": KINDS.get(kind, kind),
    }


def _ladder(kind: str, bucket: str, count: int,
            has_proof: bool) -> tuple[list[str], str, list[str]]:
    """规范阶梯表。返回（限制类型, 依据条文, 需人工判断的提示）。"""
    notes: list[str] = []

    if kind in ("late", "leave"):
        if bucket == "AB":
            if count <= 2:
                return _types("CD"), f"{KINDS[kind]}·A/B类第{count}次：不得参与CD类", notes
            if count <= 4:
                return (_types("BCD"),
                        f"{KINDS[kind]}·A/B类第{count}次：不得参与CD类，"
                        f"且三个月内不得参与B类", notes)
            return (_types("ABCD"),
                    f"{KINDS[kind]}·A/B类第{count}次（5次及以上）：不得参与BCD类，"
                    f"且三个月内不得参与A类", notes)
        # C/D 类
        if kind == "late":
            notes.append("规范：可视造成的不良后果（如关键岗位空缺、活动进程延误）"
                         "追加限制，直至不得参与ABCD类")
            return _types("CD"), f"{KINDS[kind]}·C/D类第{count}次：不得参与CD类", notes
        # 无故请假·C/D
        if not has_proof:
            return (_types("ABCD"),
                    "无故请假·C/D类·无证明的病假或事假第1次：不得参与ABCD类", notes)
        if count <= 1:
            notes.append("规范：仅当「影响活动正常进行」时才记录；提前说明且不影响的不予记录")
            return (_types("D"),
                    "无故请假·C/D类·有证明但影响活动进行第1次：不得参与D类", notes)
        if count <= 3:
            return (_types("BCD"),
                    f"无故请假·C/D类后续第{count}次：不得参与CD类，"
                    f"且三个月内不得参与B类", notes)
        return (_types("ABCD"),
                f"无故请假·C/D类后续第{count}次（3次及以上）：不得参与BCD类，"
                f"且三个月内不得参与A类", notes)

    if kind == "contact":     # 无法取得联系 / 消极志愿
        if bucket == "CD":
            notes.append("规范：存在特殊情况但造成较严重影响的，改为不得参与CD类"
                         "且三个月内不得参与B类；特殊情况经认定合理的可不予追究")
            return (_types("ABCD"),
                    f"失联/消极志愿·C/D类第{count}次：若无特殊情况，不得参与ABCD类",
                    notes)
        if count <= 1:
            notes.append("规范：B类活动「启动前」即失联的，首次即为不得参与CD类"
                         "且三个月内不得参与B类，请按实际情形调整")
            return (_types("CD"),
                    f"失联/消极志愿·A/B类第{count}次：不得参与CD类", notes)
        return (_types("BCD"),
                f"失联/消极志愿·A/B类第{count}次：不得参与CD类，"
                f"且三个月内不得参与B类", notes)

    # 其他不当行为：规范要求参照失联条款处理，但没有独立阶梯
    notes.append("规范未给「其他」单列阶梯，要求参照「无法取得联系/消极志愿」条款；"
                 "请根据情况严重程度自行勾选限制类型")
    return [], "其他不当行为：无自动阶梯，需人工判定", notes


def apply_restriction(db: OrmSession, user: User, types: list[str],
                      until: datetime | None) -> None:
    user.restricted_types = [t for t in types if t in ALL_TYPES]
    user.restricted_until = until
