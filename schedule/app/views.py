"""派生视图:岗位统计 / 排班表 / 候补队列。

这三张表都是从项目状态算出来的,不存库。**由服务端算而不是前端算**——
候补队列的"落选原因"和"排位"用的是和求解器同一套判据(`core.build_queue`),
前端照抄一遍迟早会漂:那时页面上写着"岗位名额已满",实际原因却是"不符合要求",
而两边谁对谁错没人说得清。

⚠️ 这几张表**不含身份证号**。屏幕上核对姓名/岗位/时段用不到它,少一处显示
就少一次被人瞄到的机会;导出的 xlsx 里有,那是给经办人落地用的。
"""
from __future__ import annotations

from collections import Counter

from app.conditions import check, describe, tags_used
from app.core import (LEADER, Project, build_queue, eligible, format_kv,
                      parse_time, position_stats)


def _vol_brief(project: Project, vol_key: str) -> dict:
    vol = project.volunteers.get(vol_key)
    if vol is None:
        return {"key": vol_key, "name": vol_key, "contact": "", "wechat": "", "tags": ""}
    return {
        "key": vol.key,
        "name": vol.name,
        "contact": vol.contact,
        "wechat": vol.wechat,
        "tags": format_kv(vol.tags),
    }


def schedule_view(project: Project) -> list[dict]:
    """按岗位分组的排班表。顺序与桌面版一致:岗位内按报名时间先后。"""
    desired = project.desired_map()
    out = []
    for pid, pos in project.positions.items():
        items = [a for a in project.assignments if a.pid == pid]

        def _tkey(a, pid=pid):
            it = desired.get(a.vol_key, {}).get(pid)
            return it.time_key() if it else (2, None, 0)

        items.sort(key=_tkey)
        rows = []
        for i, a in enumerate(items, start=1):
            it = desired.get(a.vol_key, {}).get(pid)
            rows.append({
                "seq": i,
                "vol_key": a.vol_key,
                "status": a.status,
                "forced": a.forced,
                "role": a.role,
                "time_raw": it.time_raw if it else "",
                **_vol_brief(project, a.vol_key),
            })
        out.append({
            "pid": pid,
            "name": pos.name,
            "time_note": pos.time_note,
            "quota": pos.quota,
            "min_quota": pos.min_quota,
            "filled": len(items),
            # 条件在这里就拼成人话，前端只管画。三处条件用的是同一个 describe，
            # 各画各的迟早会在「≥ 还是 >」这种地方对不上。
            "requirements": describe(pos.requirements),
            "preferences": [describe(c) for c in pos.preferences],
            "leader_quota": pos.leader.count,
            "leader_filled": sum(1 for a in items if a.role == LEADER),
            "leader_conditions": describe(pos.leader.conditions),
            "req_note": pos.req_note,
            "rows": rows,
        })
    return out


# 候补队列**按岗位截断**后再发给前端。
#
# 一份 6000 条报名的项目会算出五千多行候补(1.4 MB),而人看候补只看一件事:
# 「这个岗位下一个补谁」。第 11 位往后没人会读,却要占掉大半个响应体,
# 还要在浏览器里铺成几万个 DOM 节点。
#
# **截的只是传给前端的那一份。** `core.build_queue` 照旧返回全部——导出的 xlsx
# 要完整名单,不变式测试也要拿它验「每条未兑现的报名都在队列里」。
QUEUE_PER_POSITION = 10
# 零岗位名单按时间排,截前一百个:先报名的人先来问,后面的等补完这批再说。
STRANDED_LIMIT = 100


def queue_view(project: Project) -> dict:
    """候补队列。原因与排位都来自 core.build_queue,和求解器同一套判据。"""
    kept, seen = [], Counter()
    for r in build_queue(project):
        seen[r["pid"]] += 1
        if seen[r["pid"]] > QUEUE_PER_POSITION:
            continue
        kept.append({
            "pid": r["pid"],
            "pos_name": r["pos_name"],
            "rank": r["rank"],
            "vol_key": r["vol_key"],
            "name": r["name"],
            "contact": r["contact"],
            "wechat": r["wechat"],
            "tags": format_kv(r["tags"]),
            "time_raw": r["time_raw"],
            "count": r["count"],
            "reason": r["reason"],
        })
    return {"items": kept, "total": sum(seen.values()), "per_position": QUEUE_PER_POSITION}


def signup_view(project: Project) -> list[dict]:
    """报名记录:一人一行,带他报了哪些岗位、拿到了哪些。

    **同时给原始时间和解析后的时间,并按解析结果排序。** 报名时间是先到先得的
    唯一依据,而它要过 11 种格式的尽力解析(见 `core._parse_time`);解析失败的人
    会被排到所有人后面,却在界面上看不出任何异样——那是最难发现的一类不公平。
    这里把解析结果摆出来,一眼就能核对程序认没认出来。
    """
    desired = project.desired_map()
    got = project.assigned_map()
    out = []
    for vk, vol in project.volunteers.items():
        items = desired.get(vk, {})
        earliest = min(items.values(), key=lambda it: it.time_key()) if items else None
        parsed = parse_time(earliest.time_raw) if earliest else None
        out.append({
            **_vol_brief(project, vk),
            "wants": list(items),
            "assigned": sorted(got.get(vk, ())),
            "time_raw": earliest.time_raw if earliest else "",
            # 解析出来的规范形式;认不出来就是空串,前端要标红
            "time_text": f"{parsed:%Y-%m-%d %H:%M}" if parsed else "",
            "time_ok": parsed is not None,
            "_sort": earliest.time_key() if earliest else (2, None, 0),
        })
    # 默认按报名先后——这是这张表最常被用来核对的顺序
    out.sort(key=lambda r: r.pop("_sort"))
    return out


def stranded_view(project: Project) -> dict:
    """一个岗位都没拿到的人,按报名时间从早到晚。

    **这是运营方最容易挨骂的地方**——「我明明报了名，怎么什么都没排上」。
    原因和候补队列共用 `build_queue` 的判据(也就是求解器那一套),所以页面上
    写的和实际发生的是同一件事。

    排序按报名时间而不是按人名:被问到的时候,人是按「谁先报的」来质疑的。
    """
    assigned = project.assigned_map()
    by_vol: dict[str, list] = {}
    for row in build_queue(project):
        if not assigned.get(row["vol_key"]):
            by_vol.setdefault(row["vol_key"], []).append(row)

    out = []
    for vk, rows in by_vol.items():
        reasons = {r["reason"] for r in rows}
        out.append({
            **_vol_brief(project, vk),
            "time_raw": rows[0]["time_raw"],
            "wants": [{"pid": r["pid"], "pos_name": r["pos_name"], "reason": r["reason"]}
                      for r in rows],
            "summary": _stranded_summary(len(rows), reasons),
        })

    desired = project.desired_map()

    def when(entry):
        items = desired.get(entry["key"], {})
        return min((it.time_key() for it in items.values()), default=(2, None, 0))

    out.sort(key=when)
    return {"items": out[:STRANDED_LIMIT], "total": len(out), "limit": STRANDED_LIMIT}


def caps_view(project: Project) -> list[dict]:
    """岗位组上限:成员的人话名字 + 当前有多少人已经顶到上限。

    「顶到上限」这个数是给人判断松紧的:0 说明这条根本没起作用,
    一大半人都顶着说明它正在大量挡人——而挡掉的那些会散在候补队列里,
    不汇总一下看不出是同一条规则干的。
    """
    assigned = project.assigned_map()
    out = []
    for cap in project.caps:
        members = [p for p in cap.pids if p in project.positions]
        member_set = set(members)
        at_limit = sum(1 for got in assigned.values()
                       if len(member_set & got) >= cap.limit)
        out.append({
            "name": cap.name,
            "limit": cap.limit,
            "pids": members,
            "labels": [project.positions[p].label() for p in members],
            "at_limit": at_limit,
        })
    return out


def cap_checks(project: Project) -> list[dict]:
    """岗位组的体检。两类,都是「排完才发现」代价最高的那种。"""
    desired = project.desired_map()
    out = []
    for cap in project.caps:
        members = [p for p in cap.pids if p in project.positions]
        if not members:
            continue
        label = cap.name or "未命名组"
        if cap.limit >= len(members):
            out.append(_cap_problem(
                "warn", label,
                f"上限 {cap.limit} 不小于组里的岗位数 {len(members)}，"
                f"这条永远拦不住任何人，等于没设。"))
            continue

        # 能供给多少人次 = 报了这组任一岗位的人数 × 上限；需求 = 组内名额之和。
        # 供不应求的话，这个组注定排不满，而原因会散在候补队列里看不出来。
        supply = sum(1 for wishes in desired.values()
                     if any(p in wishes for p in members)) * cap.limit
        demand = sum(project.positions[p].quota for p in members)
        if supply < demand:
            out.append(_cap_problem(
                "error", label,
                f"组内名额共 {demand} 人次，但报了这组的人只有 {supply // cap.limit} 个、"
                f"每人最多 {cap.limit} 个，最多只能填 {supply} 人次——"
                f"这个组注定排不满。要么放宽上限，要么去催报名。"))
    return out


def _cap_problem(level, label, message):
    return {"level": level, "pid": "", "pos_name": label, "kind": "岗位组",
            "message": message, "count": 0, "examples": [], "more": 0}


def ineligible_view(project: Project) -> dict[str, list[str]]:
    """岗位 -> 报了名但**不符合必选条件**的人。只列有条件的岗位。

    手工调岗/加人时前端要提示「这个人不符合条件」,而条件求值只能在服务端做
    (十二个运算符、全角半角归一化、数值转不动判不满足,照抄一份迟早和求解器漂开)。
    从前它是去候补队列里翻那一行的 `reason`——队列一截断就翻不到了。

    这张表只有键,没有姓名联系方式,比队列小一个量级;而且志愿者名单前端本来就有。
    """
    out = {}
    for pid, pos in project.positions.items():
        if not (pos.requirements or {}).get("all"):
            continue
        bad = [vk for vk, wishes in project.desired_map().items()
               if pid in wishes and not eligible(project.volunteers[vk], pos)]
        if bad:
            out[pid] = bad
    return out


def _stranded_summary(n_wishes: int, reasons: set[str]) -> str:
    """一句话结论。原因不止一种时不硬凑,让人自己看明细。"""
    if len(reasons) != 1:
        return f"报了 {n_wishes} 个岗位，落选原因不止一种"
    reason = next(iter(reasons))
    if n_wishes == 1:
        return reason
    return f"报的 {n_wishes} 个岗位都是「{reason}」"


def tag_values_view(project: Project) -> list[dict]:
    """每个标签出现过的所有取值和人数。

    **没有这个，条件写得再强也白搭。** 用户看不到「性别」这一列里同时躺着
    「女」「女生」「Female」，就会写出一条只命中三分之一人的必选条件，
    然后对着排不满的岗位发愁。

    标签按报名表里第一次出现的顺序（volunteers 是有序字典），取值按人数从多到少。
    """
    counters: dict[str, Counter] = {}
    for vol in project.volunteers.values():
        for tag, value in (vol.tags or {}).items():
            counters.setdefault(tag, Counter())[str(value).strip()] += 1
    return [
        {
            "tag": tag,
            "distinct": len(counter),
            "values": [{"value": value, "count": n}
                       for value, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))],
        }
        for tag, counter in counters.items()
    ]


# 一条问题最多举几个人名。列全了没人看，一个不举又不知道去查谁。
_EXAMPLES = 3


def condition_report(project: Project) -> list[dict]:
    """求解前的条件体检。

    三类问题，都是「排完了才发现」代价最高的那种：

    1. **条件用了报名表里没有的标签** —— 打错一个字（「年级」写成「年纪」），
       这个岗位一个人都合格不了，而界面上什么异常都看不出来；
    2. **数值比较遇到判不出来的值** —— `服务时长≥20` 撞上「20小时」判不满足，
       静默的话人数就那么少了，谁也说不出为什么；
    3. **报了名的人里没有一个合格** —— 这个岗位注定空着。

    只扫**报了这个岗位的人**，不是全体志愿者：一来那才是真正会受影响的人，
    二来这样整份体检是 O(报名数 × 条件数)，可以挂在每次派生视图上算。
    """
    known = {tag for vol in project.volunteers.values() for tag in (vol.tags or {})}
    desired = project.desired_map()
    applicants: dict[str, list] = {pid: [] for pid in project.positions}
    for vk, wishes in desired.items():
        for pid in wishes:
            applicants[pid].append(project.volunteers[vk])

    out = []
    for pid, pos in project.positions.items():
        people = applicants[pid]
        kinds = [("必选条件", pos.requirements), ("组长条件", pos.leader.conditions)]
        kinds += [(f"优先条件{i + 1}", c) for i, c in enumerate(pos.preferences)]

        for kind, tree in kinds:
            if not (tree or {}).get("all"):
                continue
            missing = sorted(tags_used(tree) - known)
            if missing:
                out.append(_problem(
                    "error", pos, kind,
                    f"用到的标签「{'、'.join(missing)}」在报名表里没有这一列。"
                    f"是不是写错了？值域面板里能看到有哪些标签。", 0, []))

            hit: dict[str, list] = {}
            for vol in people:
                for problem in check(tree, vol.tags):
                    hit.setdefault(problem, []).append(vol.name)
            for problem, names in hit.items():
                out.append(_problem("warn", pos, kind, problem, len(names), names))

        if (pos.requirements or {}).get("all") and people:
            ok = sum(1 for vol in people if eligible(vol, pos))
            if ok == 0:
                out.append(_problem(
                    "error", pos, "必选条件",
                    f"报名这个岗位的 {len(people)} 个人没有一个满足必选条件，"
                    f"它注定是空的。", len(people), []))
            elif ok < pos.min_quota:
                out.append(_problem(
                    "warn", pos, "必选条件",
                    f"报名的人里只有 {ok} 个满足必选条件，达不到最低人数 "
                    f"{pos.min_quota}。", ok, []))
    return out


def _problem(level, pos, kind, message, count, names):
    return {
        "level": level, "pid": pos.pid, "pos_name": pos.name, "kind": kind,
        "message": message, "count": count,
        "examples": sorted(names)[:_EXAMPLES],
        "more": max(0, len(names) - _EXAMPLES),
    }


def derive(project: Project) -> dict:
    return {
        "stats": position_stats(project),
        "schedule": schedule_view(project),
        "queue": queue_view(project),
        "signups": signup_view(project),
        "stranded": stranded_view(project),
        "ineligible": ineligible_view(project),
        "caps": caps_view(project),
        "tag_values": tag_values_view(project),
        "checks": condition_report(project) + cap_checks(project),
    }
