"""岗位条件:结构化条件树的校验、求值、体检与说明。

必选条件、优先条件、小组长资格,三处用的都是这一套。

**存储与求值只认这一种格式,Excel 里那一列「要求」不再解析**(见 RULES.md 6.1)。
在表格里写条件的人看不到标签的实际取值,写出来的十有八九对不上真实数据——
写「性别=女」而报名表里填的是「女生」。条件在网页上点,那里能看见值域。

两层封顶:顶层 `all`(与),里面可以放 `any` 组(或)。覆盖 99% 的需求,
UI 不用做成树编辑器。

    {"all": [
      {"tag": "性别",     "op": "eq",  "value": "女"},
      {"any": [{"tag": "年级", "op": "eq", "value": "2023"},
               {"tag": "年级", "op": "eq", "value": "2024"}]},
      {"tag": "服务时长", "op": "gte", "value": 20}
    ]}

## 类型由运算符决定,不猜

标签值全是字符串(导入时 `_cell_str` 就转过了)。`eq/ne/in/not_in/contains` 走
字符串,`gt/gte/lt/lte/between` 走数值。数值那一路两边都试着转 float,
**转不动就判不满足**——数据脏是常态(`20小时`、`20 h`、`二十`)。

但「判不满足」必须能被看见,否则人排完了才发现少了一半人还查不出原因。所以
诊断单独走 `check()`,由求解前的体检调用;`match()` 是热路径,一个诊断都不做。

条件里的**阈值**则相反:填错了当场拒(`normalize` 抛 ValueError),不等到求解。
那是人在网页上填的一个数,没有「脏数据」一说,静默容忍只会让人查不出为什么没人合格。

## 字符串比较一律忽略大小写

不做成开关。中文没有大小写,英文标签(`Female` / `female`)分大小写没有任何
正当用途,而多一个没人看得懂的开关只会多一种配错的方式。
"""
from __future__ import annotations

from functools import lru_cache

# 需要值的运算符 -> 值该长什么样
STRING_OPS = {"eq", "ne", "contains"}
LIST_OPS = {"in", "not_in"}
NUMBER_OPS = {"gt", "gte", "lt", "lte"}
RANGE_OPS = {"between"}
NULLARY_OPS = {"filled", "empty"}
OPS = STRING_OPS | LIST_OPS | NUMBER_OPS | RANGE_OPS | NULLARY_OPS

OP_LABELS = {
    "eq": "=", "ne": "≠", "in": "∈", "not_in": "∉", "contains": "包含",
    "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "between": "介于",
    "filled": "已填写", "empty": "未填写",
}

# 全角 ASCII -> 半角,外加表意空格。问卷平台导出的表里这两样到处都是。
_WIDE = {c: chr(ord(c) - 0xFEE0) for c in map(chr, range(0xFF01, 0xFF5F))}
_WIDE["　"] = " "
_WIDE_TABLE = str.maketrans(_WIDE)


@lru_cache(maxsize=32768)
def norm_text(raw: str) -> str:
    """比较用的归一化:全角转半角、去首尾空格、统一小写。

    带缓存:求解热循环里同一批标签值会被反复归一化,而取值种类本来就只有
    那么几种(「男」「女」「2023」……),缓存住是纯赚。同 `parse_time`。
    """
    return raw.translate(_WIDE_TABLE).strip().lower()


@lru_cache(maxsize=32768)
def to_number(raw: str):
    """尽力转成数字,转不动返回 None(判不满足,并由 check() 报出来)。"""
    try:
        return float(raw.translate(_WIDE_TABLE).strip())
    except (ValueError, AttributeError):
        return None


class ConditionError(ValueError):
    """条件写得不对。**当场拒**,不要留到求解时静默判 false。"""


# ======================= 校验 / 归一化 =======================

def normalize(tree) -> dict:
    """校验并归一化成 {"all": [...]}。空 = 不限,恒通过。

    坏结构一律抛 ConditionError:这份东西是 `PUT` 上来的,存进去之后再想
    弄明白「为什么这个岗位没人合格」就晚了。
    """
    if not tree:
        return {"all": []}
    if not isinstance(tree, dict):
        raise ConditionError("条件必须是一个对象")
    unknown = set(tree) - {"all"}
    if unknown:
        raise ConditionError(f"条件顶层只认 all,多了:{'、'.join(sorted(unknown))}")
    items = tree.get("all") or []
    if not isinstance(items, list):
        raise ConditionError("all 必须是一个数组")
    return {"all": [_norm_item(item) for item in items]}


def _norm_item(item) -> dict:
    if not isinstance(item, dict):
        raise ConditionError("条件项必须是一个对象")
    if "any" in item:
        unknown = set(item) - {"any"}
        if unknown:
            raise ConditionError(f"或组里只认 any,多了:{'、'.join(sorted(unknown))}")
        group = item.get("any") or []
        if not isinstance(group, list) or not group:
            raise ConditionError("或组不能是空的")
        # 两层封顶:或组里只能放叶子,不能再套或组
        for leaf in group:
            if isinstance(leaf, dict) and ("any" in leaf or "all" in leaf):
                raise ConditionError("条件最多两层:或组里不能再套组")
        return {"any": [_norm_leaf(leaf) for leaf in group]}
    return _norm_leaf(item)


def _norm_leaf(leaf) -> dict:
    if not isinstance(leaf, dict):
        raise ConditionError("条件项必须是一个对象")
    tag = str(leaf.get("tag") or "").strip()
    op = str(leaf.get("op") or "").strip()
    if not tag:
        raise ConditionError("条件项没写标签名")
    if op not in OPS:
        raise ConditionError(f"没有「{op or '(空)'}」这个运算符")
    if op in NULLARY_OPS:
        return {"tag": tag, "op": op}

    value = leaf.get("value")
    if op in LIST_OPS:
        if not isinstance(value, (list, tuple)) or not value:
            raise ConditionError(f"「{tag}」的{OP_LABELS[op]}要给一组取值")
        return {"tag": tag, "op": op, "value": [str(v).strip() for v in value]}
    if op in RANGE_OPS:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ConditionError(f"「{tag}」的介于要给两个数:[下限, 上限]")
        lo, hi = (to_number(str(v)) for v in value)
        if lo is None or hi is None:
            raise ConditionError(f"「{tag}」的介于两端必须是数字")
        return {"tag": tag, "op": op, "value": sorted((lo, hi))}
    if op in NUMBER_OPS:
        num = to_number(str(value))
        if num is None:
            raise ConditionError(f"「{tag}」{OP_LABELS[op]} 后面必须是数字,现在是「{value}」")
        return {"tag": tag, "op": op, "value": num}
    if value is None or str(value).strip() == "":
        raise ConditionError(f"「{tag}」{OP_LABELS[op]} 后面没写值")
    return {"tag": tag, "op": op, "value": str(value).strip()}


# ======================= 求值 =======================

def match(tree, tags) -> bool:
    """这份标签满不满足条件。空条件恒真。

    热路径:每一轮、每个人、每个候选岗位都会走一遍,所以这里一个诊断都不做。
    要知道「为什么判不满足」用 `check()`。
    """
    if not tree:
        return True
    tags = tags or {}
    for item in tree.get("all") or ():
        if "any" in item:
            if not any(_leaf(leaf, tags) for leaf in item["any"]):
                return False
        elif not _leaf(item, tags):
            return False
    return True


def _leaf(leaf, tags) -> bool:
    op = leaf["op"]
    raw = tags.get(leaf["tag"])
    raw = "" if raw is None else str(raw)

    if op == "filled":
        return raw.strip() != ""
    if op == "empty":
        return raw.strip() == ""

    if op in NUMBER_OPS or op in RANGE_OPS:
        got = to_number(raw)
        if got is None:                       # 转不动 = 判不满足,check() 会报出来
            return False
        if op == "between":
            lo, hi = leaf["value"]
            return lo <= got <= hi
        want = leaf["value"]
        return {"gt": got > want, "gte": got >= want,
                "lt": got < want, "lte": got <= want}[op]

    got = norm_text(raw)
    if op == "in":
        return got in {norm_text(v) for v in leaf["value"]}
    if op == "not_in":
        return got not in {norm_text(v) for v in leaf["value"]}
    want = norm_text(leaf["value"])
    if op == "contains":
        return want in got
    return got == want if op == "eq" else got != want


# ======================= 体检 =======================

def check(tree, tags) -> list[str]:
    """这份标签在这个条件下有哪些**判不出来**的地方。空 = 都能明确判定。

    只报「无法求值」,不报「不满足」——不满足是正常结果,判不出来才是数据问题。
    """
    if not tree:
        return []
    tags = tags or {}
    out = []
    for item in tree.get("all") or ():
        for leaf in (item["any"] if "any" in item else [item]):
            if leaf["op"] not in NUMBER_OPS and leaf["op"] not in RANGE_OPS:
                continue
            raw = tags.get(leaf["tag"])
            raw = "" if raw is None else str(raw)
            if raw.strip() == "":
                out.append(f"「{leaf['tag']}」没填,{describe_leaf(leaf)} 判不出来")
            elif to_number(raw) is None:
                out.append(f"「{leaf['tag']}」的值「{raw}」不是数字,"
                           f"{describe_leaf(leaf)} 判不出来")
    return out


def tags_used(tree) -> set[str]:
    """条件用到了哪些标签。值域面板和体检都要它。"""
    out = set()
    for item in (tree or {}).get("all") or ():
        for leaf in (item["any"] if "any" in item else [item]):
            out.add(leaf["tag"])
    return out


# ======================= 说明 =======================

def describe_leaf(leaf) -> str:
    op = leaf["op"]
    if op in NULLARY_OPS:
        return f"{leaf['tag']} {OP_LABELS[op]}"
    if op in LIST_OPS:
        return f"{leaf['tag']}{OP_LABELS[op]}{{{'、'.join(leaf['value'])}}}"
    if op == "between":
        lo, hi = leaf["value"]
        return f"{leaf['tag']} 介于 {_num(lo)}~{_num(hi)}"
    value = leaf["value"]
    value = _num(value) if isinstance(value, float) else value
    sep = " " if op == "contains" else ""
    return f"{leaf['tag']}{sep}{OP_LABELS[op]}{sep}{value}"


def _num(x) -> str:
    return f"{x:g}"


def describe(tree) -> str:
    """拼成一句人话,给界面和导出用。空条件返回空串(调用方显示「不限」)。"""
    parts = []
    for item in (tree or {}).get("all") or ():
        if "any" in item:
            parts.append("(" + " 或 ".join(describe_leaf(x) for x in item["any"]) + ")")
        else:
            parts.append(describe_leaf(item))
    return " 且 ".join(parts)


def from_pairs(pairs) -> dict:
    """{标签: 值} -> 全是 eq 且 AND 的条件树。

    给示例数据和测试用的便捷构造。**不是** Excel 解析器——那一条路已经关了。
    """
    return {"all": [{"tag": str(k).strip(), "op": "eq", "value": str(v).strip()}
                    for k, v in (pairs or {}).items() if str(k).strip()]}
