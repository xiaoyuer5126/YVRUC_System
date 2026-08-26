"""不变式测试:随机造项目,断言那几条「无论怎么排都必须成立」的性质。

**为什么是这种测法。** 规则要从四条涨到二十多条,其中 9 项可开关可排序——
几十万种组合,逐条写用例是挡不住的。能挡住的是这个:不管配置怎么变、不管
求解器内部怎么改,下面这些都得成立。规则加一条,这里就多一条断言,
而不是多一批用例。

生成器带种子。用例失败时报的是种子,`make_random_project(seed)` 原样复现。

有意造进去的脏东西(它们都是真实会出现的):
- 人工强排的 LOCKED 行,可以违反名额/冲突/要求,甚至排到没报名的岗位;
- 报名时间解析不出来的条目(按导入顺序兜底);
- 岗位要求卡掉一部分人,冲突图拆散一部分组合。
"""
from __future__ import annotations

import random

import pytest

from app import rules as rulecfg
from app.conditions import from_pairs, normalize
from app.core import (AUTO, LEADER, LOCKED, MEMBER, Assignment, Cap, Leader,
                      Position, Project, Volunteer, build_queue, eligible,
                      eligible_leader, post_process, solve)

# 标签取值故意留窄:要求命中率高一些,才压得到「资格卡人」的分支。
TAG_VALUES = {"性别": ["男", "女"], "年级": ["2023", "2024", "2025"]}

SEEDS = list(range(30))


def make_random_project(
    seed: int,
    *,
    n_positions: int = 8,
    n_people: int = 25,
    max_quota: int = 3,
    max_wishes: int = 4,
    req_ratio: float = 0.3,
    conflict_ratio: float = 0.15,
    cohort_ratio: float = 0.10,
    locked_ratio: float = 0.10,
) -> Project:
    rng = random.Random(seed)
    prj = Project()

    for i in range(n_positions):
        pid = f"P{i:02d}"
        # 三种形状轮着造:单条 eq、或组、数值比较（数值那一路还要压到脏数据）。
        reqs = {"all": []}
        if rng.random() < req_ratio:
            tag = rng.choice(list(TAG_VALUES))
            shape = rng.random()
            if shape < 0.55:
                reqs = from_pairs({tag: rng.choice(TAG_VALUES[tag])})
            elif shape < 0.85:
                picks = rng.sample(TAG_VALUES[tag], min(2, len(TAG_VALUES[tag])))
                reqs = normalize({"all": [{"any": [
                    {"tag": tag, "op": "eq", "value": v} for v in picks]}]})
            else:
                reqs = normalize({"all": [
                    {"tag": "服务时长", "op": "gte", "value": rng.choice([5, 20, 40])}]})
        # 三成岗位设优先条件,其中一半是两档——逐档放行(S1)得被压到
        prefs = []
        if rng.random() < 0.3:
            tags = rng.sample(list(TAG_VALUES), 2)
            prefs = [from_pairs({t: rng.choice(TAG_VALUES[t])})
                     for t in tags[: 2 if rng.random() < 0.5 else 1]]

        quota = rng.randint(1, max_quota)
        # 三成岗位设组长,一半带条件——组长条件比必选条件更容易把人卡光,
        # 正好压到「组长槽空着但没人够格」那条路
        leader = Leader()
        if rng.random() < 0.3:
            leader = Leader(count=rng.randint(1, quota))
            if rng.random() < 0.5:
                tag = rng.choice(list(TAG_VALUES))
                leader.conditions = from_pairs({tag: rng.choice(TAG_VALUES[tag])})

        prj.positions[pid] = Position(
            pid=pid, name=f"岗位{i}", quota=quota, requirements=reqs,
            # 一半岗位设最低人数,压到 C1 排序和 P1 补位
            min_quota=rng.randint(1, quota) if rng.random() < 0.5 else 0,
            preferences=prefs, leader=leader,
        )
    pids = list(prj.positions)

    for pair_kind, ratio in (("conflicts", conflict_ratio), ("cohorts", cohort_ratio)):
        for a in range(n_positions):
            for b in range(a + 1, n_positions):
                if rng.random() < ratio:
                    getattr(prj, pair_kind).add(frozenset((pids[a], pids[b])))

    for j in range(n_people):
        contact = f"13800{j:06d}"
        key = f"人{j:03d}{contact}"
        tags = {tag: rng.choice(values) for tag, values in TAG_VALUES.items()}
        # 服务时长故意掺脏值和缺失:数值比较那一路必须能扛住,判不满足而不是崩。
        tags["服务时长"] = rng.choice(["0", "8", "20", "35", "60", "20小时", "二十", ""])
        prj.volunteers[key] = Volunteer(key=key, name=f"人{j:03d}", contact=contact, tags=tags)
        for pid in rng.sample(pids, rng.randint(1, min(max_wishes, n_positions))):
            # 一成的报名时间是填不出来的格式——求解要能按导入顺序兜底。
            raw = "" if rng.random() < 0.1 else f"2026-07-20 {9 + j // 60:02d}:{j % 60:02d}"
            prj.add_signup(key, pid, raw)

    # 岗位组上限。造成重叠的几组,压到「多条同时生效」那条路。
    for g in range(rng.randint(0, 2)):
        members = rng.sample(pids, rng.randint(2, min(4, n_positions)))
        prj.caps.append(Cap(name=f"组{g}", pids=members,
                            limit=rng.randint(1, len(members) - 1)))

    # 人工锁定的行。**故意允许它违规**:界面上就能强排,求解器必须原样端着。
    for item in list(prj.signups):
        if rng.random() < locked_ratio:
            prj.assignments.append(Assignment(item.pid, item.vol_key, LOCKED, False))
    if rng.random() < 0.3:
        vk = rng.choice(list(prj.volunteers))
        prj.assignments.append(Assignment(rng.choice(pids), vk, LOCKED, True))

    # 同一 (岗位, 人) 只留一行——界面不会造出重复,生成器也不该。
    seen, kept = set(), []
    for a in prj.assignments:
        if (a.pid, a.vol_key) not in seen:
            seen.add((a.pid, a.vol_key))
            kept.append(a)
    prj.assignments = kept
    return prj


# ————————————————————————————————————————————————— 断言集

def assert_hard_invariants(prj: Project, assignments: list[Assignment]) -> None:
    """H 层十条里能从外部观察到的那些。

    注意口径:**只对 AUTO 行断言合法性**。LOCKED 是人工确认过的,允许违反名额、
    冲突、要求,甚至排到没报名的岗位——H1 优先于一切,求解器的活是原样端着它,
    不是把它改对。
    """
    desired = prj.desired_map()
    held: dict[str, set[str]] = {}
    filled: dict[str, int] = {}
    locked_filled: dict[str, int] = {}
    for a in assignments:
        held.setdefault(a.vol_key, set()).add(a.pid)
        filled[a.pid] = filled.get(a.pid, 0) + 1
        if a.status == LOCKED:
            locked_filled[a.pid] = locked_filled.get(a.pid, 0) + 1

    # 增广撤销漏了一次 place/unplace 就会在这里现形
    pairs = [(a.pid, a.vol_key) for a in assignments]
    assert len(pairs) == len(set(pairs)), "同一个人被排进同一个岗位两次"

    # H1:LOCKED 逐字保留(求解器会丢掉指向已删岗位的,所以按岗位还在的那些比)
    def locked_set(rows):
        return {(a.pid, a.vol_key, a.forced) for a in rows
                if a.status == LOCKED and a.pid in prj.positions}

    assert locked_set(assignments) == locked_set(prj.assignments), "LOCKED 行被动过"

    for a in assignments:
        if a.status != AUTO:
            continue
        where = f"{a.vol_key} @ {a.pid}"
        # H2
        assert a.pid in desired.get(a.vol_key, {}), f"H2 排到了没报名的岗位:{where}"
        # H3
        assert eligible(prj.volunteers[a.vol_key], prj.positions[a.pid]), \
            f"H3 不符合岗位要求:{where}"
        # H4:和这个人手上任何一个岗位都不能冲突(含 LOCKED 那些)
        for other in held[a.vol_key]:
            assert other == a.pid or not prj.is_conflict(a.pid, other), \
                f"H4 与已获岗位冲突:{where} vs {other}"

    # H5:LOCKED 可能已经把岗位塞超了,求解器不许在此之上再加
    for pid, pos in prj.positions.items():
        ceiling = max(pos.quota, locked_filled.get(pid, 0))
        assert filled.get(pid, 0) <= ceiling, f"H5 岗位 {pid} 超额"

    # H11:岗位组上限。同样只管纯 AUTO 的人——某个组里只要掺了 LOCKED 行,
    # 那个人整组就不该被断言(人工强排本来就能撑爆上限)。
    for cap in prj.caps:
        members = {p for p in cap.pids if p in prj.positions}
        if not members:
            continue
        locked_in = {a.vol_key for a in assignments
                     if a.status == LOCKED and a.pid in members}
        for vk, got in held.items():
            if vk in locked_in:
                continue
            assert len(got & members) <= cap.limit, \
                f"H11 {vk} 在组「{cap.name}」里占了 {len(got & members)} 个,上限 {cap.limit}"

    # H9/H10:组长槽。同样只管 AUTO 行——人工可以把任何人钉成组长。
    for pid, pos in prj.positions.items():
        rows = [a for a in assignments if a.pid == pid]
        auto_leaders = [a for a in rows if a.role == LEADER and a.status == AUTO]
        locked_leaders = sum(1 for a in rows if a.role == LEADER and a.status == LOCKED)
        assert len(auto_leaders) + locked_leaders <= max(pos.leader.count, locked_leaders), \
            f"H10 岗位 {pid} 的组长槽超了"
        for a in auto_leaders:
            assert eligible_leader(prj.volunteers[a.vol_key], pos), \
                f"H9 {a.vol_key} 不够格当 {pid} 的组长"
        # 普通槽也不能超:名额 - 组长人数
        auto_members = sum(1 for a in rows if a.role == MEMBER and a.status == AUTO)
        locked_members = sum(1 for a in rows if a.role == MEMBER and a.status == LOCKED)
        assert auto_members + locked_members <= max(pos.member_quota(), locked_members), \
            f"岗位 {pid} 的普通槽超了"


def assert_queue_truthful(prj: Project) -> None:
    """候补队列说的话得是真的。

    这张表是使用者唯一能看到的「为什么没排上」。原因写错比不写更糟——
    人会照着错的原因去改岗位表。
    """
    assigned = prj.assigned_map()
    rows = build_queue(prj)

    for r in rows:
        vk, pid = r["vol_key"], r["pid"]
        assert pid not in assigned.get(vk, ()), "已经排上的人不该出现在候补里"
        vol, pos = prj.volunteers[vk], prj.positions[pid]
        if r["reason"] == "不符合岗位要求":
            assert not eligible(vol, pos)
        elif r["reason"] == "与已获岗位冲突":
            assert eligible(vol, pos)
            assert any(prj.is_conflict(pid, p) for p in assigned.get(vk, ()))
        elif r["reason"].startswith("超出岗位组上限"):
            assert eligible(vol, pos)
            assert not any(prj.is_conflict(pid, p) for p in assigned.get(vk, ()))
            # 说了是被组挡的,那就真得有那么一个组正卡着他
            assert any(
                cap.limit <= len({p for p in cap.pids if p in assigned.get(vk, ())})
                and pid in cap.pids
                for cap in prj.caps), f"说超了组上限,可没有一个组卡着 {vk}@{pid}"
            # 原因里点了组名,人才知道该松哪一条
            assert any(cap.name in r["reason"] for cap in prj.caps if cap.name)
        elif r["reason"] == "岗位名额已满":
            assert eligible(vol, pos)
            assert not any(prj.is_conflict(pid, p) for p in assigned.get(vk, ()))
        else:
            pytest.fail(f"没见过的落选原因:{r['reason']}")

    # 完整性:每一条没兑现的报名都得在队列里,不多不少
    unmet = {(vk, pid) for vk, wishes in prj.desired_map().items()
             for pid in wishes if pid not in assigned.get(vk, ())}
    assert {(r["vol_key"], r["pid"]) for r in rows} == unmet
    assert len(rows) == len(unmet), "同一条报名在候补里出现了多次"


# ————————————————————————————————————————————————— 用例

def random_rules(rng):
    """随机造一份合法配置:打乱顺序、随机开关。

    `signup_time` 必留——它不给关闭开关(见 rules.py),所以随机也不该造出没有它的。
    """
    order = [k for k in ("round_cap", "spread", "few_options") if rng.random() < 0.6]
    order.append("signup_time")
    rng.shuffle(order)
    candidate = [k for k in rulecfg.CAND_RULES if rng.random() < 0.7]
    rng.shuffle(candidate)
    return rulecfg.normalize({
        "preset": "custom", "order": order, "candidate": candidate,
        "toggles": {k: rng.random() < 0.7 for k in rulecfg.TOGGLES},
    })


@pytest.mark.parametrize("seed", SEEDS)
def test_排出来的结果满足全部硬约束(seed):
    prj = make_random_project(seed)
    assert_hard_invariants(prj, solve(prj))


@pytest.mark.parametrize("seed", SEEDS)
def test_无论规则怎么配硬约束都成立(seed):
    """**这条是规则可配置化的全部安全网。**

    9 项可开关可排序 = 几十万种组合,逐一写用例是不可能的。能验的是这个:
    不管用户把顺序拖成什么样、关掉哪几条,H 层那十条一条都不能塌。

    每个种子配一份自己的随机规则,30 个种子就是 30 种不同的组合。
    """
    rng = random.Random(9000 + seed)
    prj = make_random_project(seed)
    prj.rules = random_rules(rng)
    assert_hard_invariants(prj, solve(prj))


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_规则怎么配都是确定性的(seed):
    """同一份配置算两次必须逐字相同。

    编译候选排序键时要是漏了岗位表顺序那个兜底,或者哪一项拿 set 的迭代顺序
    当键,这里会红——而线上的表现是「预览和应用排出来不一样」，等于骗人签字。
    """
    rng = random.Random(9000 + seed)
    cfg = random_rules(rng)

    def shot():
        prj = make_random_project(seed)
        prj.rules = cfg
        return [(a.pid, a.vol_key, a.status, a.role) for a in solve(prj)]

    assert shot() == shot()


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_候补排位跟着轮次配置走(seed):
    """V2 那笔暗账:排位和实际排班必须是同一套顺序。

    这里验的是「配置真的传到了候补队列」——把轮次顺序换掉之后，排位应该跟着变
    (至少在有多人候补同一岗位时)。两边各写一份的话，这条迟早会红。
    """
    prj = make_random_project(seed)
    prj.rules = rulecfg.from_preset("balanced")
    prj.assignments = solve(prj)
    assert_queue_truthful(prj)

    prj.rules = rulecfg.from_preset("first_come")
    assert_queue_truthful(prj)      # 换套顺序，队列照样得说真话


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_同样的输入排两次结果逐字相同(seed):
    """求解器必须是确定性的:预览和应用是分两次算的,不一样就等于骗人签字。

    `cand_key` 末位那个岗位表顺序就是为这条留的——任何一处拿 set 迭代顺序
    当排序键,这里都会红。
    """
    prj = make_random_project(seed)

    def shot(rows):
        return [(a.pid, a.vol_key, a.status, a.forced) for a in rows]

    assert shot(solve(prj)) == shot(solve(prj))


@pytest.mark.parametrize("seed", SEEDS)
def test_候补队列的原因和排位说的是真话(seed):
    prj = make_random_project(seed)
    prj.assignments = solve(prj)
    assert_queue_truthful(prj)


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_后处理只换人不改人次也不让人掉岗(seed):
    """P 层的贯穿不变式:决定「谁有岗位」的是求解本身,后处理只决定「谁在哪」。

    最低人数补位、同批换人、往后的组长补位都挂在这一条上——它们是同一种东西,
    多一个 pass 就在这里多跑一遍,不用多写一条用例。
    """
    prj = make_random_project(seed)
    assignments = solve(prj)
    before_n = len(assignments)
    before_who = {a.vol_key for a in assignments}
    before_locked = sorted((a.pid, a.vol_key) for a in assignments if a.status == LOCKED)

    post_process(prj, assignments)

    assert len(assignments) == before_n, "后处理改变了已排人次"
    assert {a.vol_key for a in assignments} == before_who, "有人在后处理里掉岗了"
    assert sorted((a.pid, a.vol_key) for a in assignments
                  if a.status == LOCKED) == before_locked
    # 换完还得是一份合法排班——后处理最容易犯的错就是把人换进了不该去的岗位
    assert_hard_invariants(prj, assignments)


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_人工强排的锁定行不会把求解器带崩(seed):
    """故意把违规的 LOCKED 行调多:超额、冲突、不合要求、没报名也能排进去。

    这是界面上真能点出来的状态(调岗允许强制),求解器遇上它不能崩,
    也不能顺手把它「修正」掉。
    """
    prj = make_random_project(seed, locked_ratio=0.45)
    assert_hard_invariants(prj, solve(prj))


@pytest.mark.parametrize("seed", SEEDS[:15])
def test_保底重试只可能多排上人_不可能少(seed):
    """P0 是加保险的，不是调结果的。

    ⚠️ 实测它一个人都没多救到（300 个随机实例、含最容易出长链的紧形状，
    0 个受益，见 core.py 里那段注释）。所以这条用例守的是**它别帮倒忙**：
    深度重试之后排上的人只能是原来那批的超集。哪天它真开始救人，
    这条依然成立；哪天它开始弄丢人，这条会红。
    """
    plain = solve(make_random_project(seed), rescue_depth=0)
    deep = solve(make_random_project(seed), rescue_depth=5)
    assert {a.vol_key for a in plain} <= {a.vol_key for a in deep}
    assert len(plain) <= len(deep)


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_存回来再读一遍是同一份东西(seed):
    """`PUT` 每次都把整份状态重新解析一遍再存（见 routes.save_project）。

    这条路上任何一处「读得进去但写出来不一样」，表现都是项目每保存一次就漂一点，
    而漂到什么程度没人盯得住。
    """
    prj = make_random_project(seed)
    prj.assignments = solve(prj)
    once = prj.to_dict()
    twice = Project.from_dict(once).to_dict()
    assert once == twice


@pytest.mark.parametrize("seed", SEEDS[:10])
def test_在自己的结果上再排一次是稳定的(seed):
    """把上一版结果喂回去当 prefer_pairs,已排上的人不该莫名少掉。

    这就是使用者实际的用法:导一批人、排一次、再导一批、再排一次。
    每次都掉几个人的话,这个工具没法用。
    """
    prj = make_random_project(seed)
    first = solve(prj)
    prj.assignments = first
    second = solve(prj, prefer_pairs=[(a.pid, a.vol_key) for a in first])

    assert_hard_invariants(prj, second)
    got_first = {a.vol_key for a in first}
    got_second = {a.vol_key for a in second}
    assert got_first <= got_second, f"重排之后掉岗:{sorted(got_first - got_second)}"


def test_读回来时同一个岗位同一个人只留一行():
    """一个人不可能在一个岗位上站两次。重复行唯一的来源是界面上的手工操作出岔子
    （比如把人调到他已经在的岗位），而重复一旦落库很难发现：名单里多一个名字、
    导出多一行、「已排/名额」虚高，谁也想不到去查重。

    from_dict 是唯一的校验关口，在这里清掉——已经被弄脏的项目下次保存时自动修好。
    """
    prj = make_random_project(0)
    prj.assignments = solve(prj)
    dirty = prj.to_dict()
    dirty["assignments"] = dirty["assignments"] + dirty["assignments"][:3]

    rows = Project.from_dict(dirty).assignments
    pairs = [(a.pid, a.vol_key) for a in rows]
    assert len(pairs) == len(set(pairs))
    assert len(rows) == len(prj.assignments), "去重不该顺手删掉别的行"
