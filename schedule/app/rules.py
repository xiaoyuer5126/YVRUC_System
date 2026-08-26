"""规则配置:哪些规则开着、按什么顺序比。

完整清单和「哪些固定哪些可配」见 `../RULES.md` 第 2、3 节。这里只放**可配的那些**——
固定的那批(硬约束、腾位排序、后处理顺序、确定性兜底)压根不出现在配置里,
连开关都不给,免得让人以为它们能关。

## 分两层,别混

- `order`  轮到**谁**挑(O 层)。它同时喂给求解器和候补队列——V2 那笔暗账:
           排位和实际排班必须是同一套顺序,不然页面上写着「你是第 3 位候补」,
           实际排班却按另一套来,那是骗人。
- `candidate` 这个人**挑哪个岗位**(C 层)。

## 为什么默认入口是预设

9 项可排序意味着几十万种组合,不可能逐一测试也不可能逐一解释。贪心算法在某些
组合下会给出明显糟糕的结果(比如「增量稳定」置顶 + 「最低人数」关掉,会出现一堆
岗位达不到最低线而算法认为已经没有进展了),而用户不会认为是自己配错了。
所以三套预设是默认入口,自定义收在「高级」里,并且保存时强制跑一次体检。

## signup_time 不给关闭开关

先到先得是这个工具的公平性根基,也是唯一能对志愿者公开交代的规则。允许关掉
等于允许暗箱操作。它的**位置**可以变(排在摊平前面就是纯先到先得),但不能取消。
"""
from __future__ import annotations

# —— O 层(轮到谁挑)。round_cap 不是排序键,是结构开关,单独处理。 ——
ORDER_RULES = {
    "round_cap": "每轮每人至多新增一个岗位",
    "spread": "已获岗位数少者优先(摊平)",
    "signup_time": "报名时间早者优先(先到先得)",
    "few_options": "报名岗位数少者优先",
}
ORDER_SORT_RULES = ("spread", "signup_time", "few_options")   # 参与排序的那几个
LOCKED_ORDER_RULE = "signup_time"                             # 不许关

# —— C 层(挑哪个岗位)。岗位表顺序永远垫底,不在配置里。 ——
CAND_RULES = {
    "min_quota": "未达最低人数的岗位优先",
    "gap": "缺口率大的岗位优先",
    "cohort": "同批:优先与已获伙伴岗位同人",
    "stable": "上一版组合优先(增量稳定)",
    "scarce": "别人补不上的岗位优先",
    "preference": "符合优先条件的靠前",
}

TOGGLES = {
    "cohort_swap": "后处理:同批换人",
    "preference_phase": "优先条件逐档放行",
}

# —— 固定规则:**不给开关,但要给看**。 ——
#
# 从前这些压根不下发,理由是「免得让人以为它们能关」。但那个理由只对「不给开关」
# 成立,对「不给看」不成立:H1(锁定不动)和 H6(不掉岗)恰恰是运营方敢用这个工具
# 的前提,看得见比看不见更让人放心;而「岗位表顺序垫底」这种,不写出来的话
# 用户会以为平局时是随机的。
#
# 界面上置灰只读。文案和 RULES.md 第 2 节同源,那里改了这里要跟。
FIXED_RULES = [
    {"group": "硬约束", "items": [
        ("锁定的行绝不改动", "人工确认过的排班，重排时原样端着——包括它违反名额或冲突的时候"),
        ("只排到本人报过名的岗位", "自动排班不会把人放进他没报的岗位；人工调岗可以，会标红"),
        ("必选条件必须满足", "不满足的人绝不会被自动排进来"),
        ("时间冲突的岗位不会兼", "冲突矩阵上勾了的两个岗位，同一个人不会同时被排进去"),
        ("不超名额上限", "组长槽也算在名额里"),
        ("已获岗位的人不会掉岗", "重排时只会给他换岗位，不会把他变成没岗位"),
        ("组长必须满足组长条件", "而且组长条件之外，岗位的必选条件照样要过"),
    ]},
    {"group": "顺序与兜底", "items": [
        ("先到先得永远生效", "位置可以调（排在摊平前面就是纯先到先得），但不能关掉——"
                             "它是唯一能对志愿者公开交代的规则"),
        ("平局时按岗位表顺序", "所有规则都比平了才轮到它。有它才能保证同一份数据两次"
                               "排出来逐字一样，否则预览和应用会不一致"),
        ("腾位置时被挪的人去哪儿", "优先去当场就有空位的岗位，其次是上一版待过的。"
                                   "这是实现细节，暴露出来也没法判断好坏"),
    ]},
    {"group": "后处理（顺序固定 1→2→3）", "items": [
        ("1. 最低人数补位", "从「抽走一人仍在最低线之上」的岗位调人来补缺口"),
        ("2. 组长补位", "组长槽空着而岗位里已有够格的人，就把他升上去"),
        ("3. 同批换人", "让同批岗位尽量由同一批人担任"),
    ], "note": "顺序不可调：它们会互相破坏（补位可能拆散同批）。同批放最后，"
               "因为它是纯偏好，被牺牲掉损失最小。这三步都只换人不改人次，也不让任何人掉岗。"},
]

PRESETS = {
    "balanced": {
        "label": "均衡",
        "hint": "大多数活动用这个。所有人拿到第 1 个岗位之前，没人拿第 2 个。",
        "order": ["round_cap", "spread", "signup_time"],
        "candidate": ["min_quota", "gap", "cohort", "stable", "scarce", "preference"],
        "toggles": {"cohort_swap": True, "preference_phase": True},
    },
    "first_come": {
        "label": "先到先得优先",
        "hint": "报名即确认的活动。早报名的人一次把他报的岗位拿满，晚的可能一个都没有。",
        "order": ["signup_time", "spread"],
        "candidate": ["gap", "preference", "stable", "cohort", "scarce"],
        "toggles": {"cohort_swap": True, "preference_phase": True},
    },
    "floor_first": {
        "label": "保底优先",
        "hint": "死保每个岗位的最低人数和组长，代价是和上一版相比换岗的人会多。",
        "order": ["round_cap", "spread", "signup_time", "few_options"],
        "candidate": ["min_quota", "preference", "scarce", "gap", "cohort", "stable"],
        "toggles": {"cohort_swap": True, "preference_phase": True},
    },
}
DEFAULT_PRESET = "balanced"


class RuleError(ValueError):
    """规则配置写得不对。当场拒——存进去之后排出来的东西没人解释得清。"""


def default() -> dict:
    return from_preset(DEFAULT_PRESET)


def from_preset(name: str) -> dict:
    spec = PRESETS.get(name)
    if spec is None:
        raise RuleError(f"没有「{name}」这套预设")
    return {
        "preset": name,
        "order": list(spec["order"]),
        "candidate": list(spec["candidate"]),
        "toggles": dict(spec["toggles"]),
    }


def normalize(rules) -> dict:
    """校验并补全。空 = 用默认预设。

    存的是「启用哪些、按什么顺序」的**有序列表**,没列出来的就是关掉的——
    比「全列出来各带一个 enabled 布尔」少一种自相矛盾的状态(列表里没有它、
    enabled 却是 true,该听谁的?)。
    """
    if not rules:
        return default()
    if not isinstance(rules, dict):
        raise RuleError("规则配置必须是一个对象")

    preset = rules.get("preset") or "custom"
    if preset != "custom" and preset not in PRESETS:
        raise RuleError(f"没有「{preset}」这套预设")

    order = _norm_list(rules.get("order"), ORDER_RULES, "轮次顺序")
    candidate = _norm_list(rules.get("candidate"), CAND_RULES, "候选排序")
    if LOCKED_ORDER_RULE not in order:
        raise RuleError("「先到先得」不能关掉——它是唯一能对志愿者公开交代的规则")

    raw_toggles = rules.get("toggles") or {}
    if not isinstance(raw_toggles, dict):
        raise RuleError("toggles 必须是一个对象")
    unknown = set(raw_toggles) - set(TOGGLES)
    if unknown:
        raise RuleError(f"没有这些开关:{'、'.join(sorted(unknown))}")
    toggles = {key: bool(raw_toggles.get(key, True)) for key in TOGGLES}

    return {"preset": preset, "order": order, "candidate": candidate, "toggles": toggles}


def _norm_list(value, known, what) -> list[str]:
    if value is None:
        raise RuleError(f"规则配置缺少{what}")
    if not isinstance(value, list):
        raise RuleError(f"{what}必须是一个数组")
    out = []
    for key in value:
        if key not in known:
            raise RuleError(f"{what}里没有「{key}」这条规则")
        if key in out:
            raise RuleError(f"{what}里「{key}」出现了两次")
        out.append(key)
    return out


# ======================= 供求解器/候补队列读取 =======================

def order_fields(cfg) -> tuple:
    """轮次排序用哪几个键、什么顺序。求解器和候补队列**都读这一个**。"""
    return tuple(k for k in cfg["order"] if k in ORDER_SORT_RULES)


def round_cap(cfg) -> bool:
    return "round_cap" in cfg["order"]


def enabled(cfg, key) -> bool:
    return key in cfg["candidate"] or bool(cfg["toggles"].get(key))


def describe(cfg) -> list[str]:
    """把配置拼成给人看的几行。自定义之后仍然说得出「基于哪套改的」。"""
    lines = []
    preset = cfg.get("preset")
    if preset in PRESETS:
        lines.append(f"预设：{PRESETS[preset]['label']}")
    else:
        base = "自定义"
        lines.append(f"预设：{base}")
    lines.append("轮到谁挑：" + " → ".join(ORDER_RULES[k] for k in cfg["order"]))
    lines.append("挑哪个岗位：" + (" → ".join(CAND_RULES[k] for k in cfg["candidate"])
                                or "只按岗位表顺序"))
    off = [TOGGLES[k] for k, on in cfg["toggles"].items() if not on]
    if off:
        lines.append("已关闭：" + "、".join(off))
    return lines
