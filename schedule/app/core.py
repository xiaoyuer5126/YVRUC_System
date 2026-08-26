"""排班核心:数据模型 + 资格判定 + 求解器 + 变更对比。

零依赖,仅用标准库,可脱离 GUI 单独复用。
优先级(高→低):
  L1 岗位充足(候选按缺口率排序 + 增广重排,近似最小化 Σ缺口²)
  L2 早报名者优先获得"至少一个岗位"(轮内按报名时间排序)
  L3 摊平人均岗位数(第 k+1 个岗位一律排在所有人的第 k 个之后)
  L4 增量稳定(平局时优先保持上一版的 (岗位,人) 组合)
不变式:LOCKED 行绝不改动;已获得岗位的人只会被换岗,绝不会掉岗。

规则的完整清单、哪些固定哪些可配,见 `../RULES.md`——这里只是它的实现。

**求解的时间闸门**(deadline / SolveTimeout):这里不会真的死循环——三个循环都有
硬上限——但 `find_slot` 的回溯增广是指数级的,深度一大就能跑上几个小时,对使用者
来说和卡死没有区别。什么时候用多深,见 solver.py。
"""
from __future__ import annotations

import datetime as dt
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from functools import lru_cache

from app import conditions, rules as rulecfg

AUTO = "AUTO"      # 程序自动排的,重排时可调整
LOCKED = "LOCKED"  # 锁定(人工确认/手动改动过),重排时绝不改动

MEMBER = ""        # 普通槽
LEADER = "leader"  # 组长槽。**含在 quota 之内**——quota 始终是「这个岗位站几个人」

SCHEMA_VERSION = 2

_TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M", "%Y/%m/%d",
    "%Y年%m月%d日 %H:%M:%S", "%Y年%m月%d日 %H:%M", "%Y年%m月%d日",
    "%m-%d %H:%M", "%m/%d %H:%M",
]


@lru_cache(maxsize=16384)
def _parse_time_cached(s):
    return _parse_time(s)


def parse_time(raw):
    """尽力解析报名时间字符串,失败返回 None(此时按导入顺序排序)。

    带缓存:`SignupItem.time_key()` 在求解的热循环里被反复调用(每一轮、每个人、
    每个未满足的岗位各算一次),而每次都要试到 11 种格式。报名时间的取值本来
    就只有报名条目那么多,缓存住是纯赚。
    """
    s = (raw or "").strip()
    if not s:
        return None
    return _parse_time_cached(s)


def _parse_time(s):
    try:
        return dt.datetime.fromisoformat(s)
    except ValueError:
        pass
    for f in _TIME_FORMATS:
        try:
            return dt.datetime.strptime(s, f)
        except ValueError:
            continue
    return None


def split_multi(s):
    """拆分多值字段(分号/逗号/顿号/竖线等)。"""
    return [x.strip() for x in re.split(r"[;；,，、|/\n]+", s or "") if x.strip()]


def parse_kv(s):
    """把「标签=值;标签=值」解析成 {标签: 值}。返回 (字典, 格式错误的条目)。"""
    out, bad = {}, []
    for item in split_multi(s):
        k, sep, v = item.partition("=")
        k, v = k.strip(), v.strip()
        if not sep or not k or not v:
            bad.append(item)
            continue
        out[k] = v
    return out, bad


def format_kv(d):
    """{标签: 值} → 「标签=值;标签=值」,用于显示与导出。"""
    return ";".join(f"{k}={v}" for k, v in (d or {}).items())


def identity_key(name, contact):
    """志愿者唯一标识:姓名 + 联系方式,两者都必填。

    身份证号、微信允许留空,不参与身份判定。同一 (姓名, 联系方式) 视为同一人。
    调用方需保证联系方式非空(导入时会跳过并警告,手动录入时会拦截),
    这里不做「退化成只按姓名」的兜底——那会把漏填的人错误地合并成同一个。
    """
    name = (name or "").strip()
    contact = (contact or "").strip()
    return f"{name}{contact}"


# ======================= 数据模型 =======================

@dataclass
class Leader:
    """小组长:岗位里的一类槽位。

    **含在 quota 之内**——`quota=10, count=2` 就是 8 个组员 + 2 个组长,
    这样 quota 始终是「这个岗位站几个人」这一个意思,导出表里的「已排/名额」
    不用解释。`count=0` = 这个岗位不设组长。

    报名不分角色:谁能当组长由 `conditions` 判定。「愿不愿意当组长」本来就该是
    报名表里的一个标签列(`是否愿任组长=是`),条件机制天然覆盖。
    """
    count: int = 0
    conditions: dict = field(default_factory=lambda: {"all": []})


@dataclass
class Position:
    pid: str
    name: str = ""
    time_note: str = ""          # 仅供人看的时间说明,程序不解析
    quota: int = 0               # 名额上限,含组长
    min_quota: int = 0           # 最低人数,0 = 不设。是排序优先级,不是可行性约束
    # 必选条件(条件树,见 conditions.py)。空 = 不限
    requirements: dict = field(default_factory=lambda: {"all": []})
    # 优先条件,**有序**,靠前的优先级高。字典序比较,不做加权求和——
    # 「他满足第 1 条优先、你不满足」能一句话解释,加权求和解释不了。
    preferences: list = field(default_factory=list)
    leader: Leader = field(default_factory=Leader)
    # Excel「要求」列的原文,只读参照,不参与求解。主办方在表里写的是自然语言
    # (「女生优先,最好有急救证」),本来就不该被解析——人对照着它在网页上点条件。
    req_note: str = ""

    def label(self):
        return f"{self.pid} {self.name}".strip()

    def member_quota(self):
        """普通槽的容量 = 名额 - 组长人数。"""
        return max(0, self.quota - self.leader.count)


@dataclass
class Volunteer:
    key: str                     # 唯一键:姓名 + 联系方式(见 identity_key)
    name: str
    id_card: str = ""
    contact: str = ""
    wechat: str = ""             # 微信号
    # 志愿者标签 {标签: 值},如 {"性别": "女", "年级": "2023"}
    tags: dict = field(default_factory=dict)


@dataclass
class SignupItem:
    """一条报名项 = (志愿者, 岗位, 报名时间)。一人报多个岗位即多条。"""
    vol_key: str
    pid: str
    time_raw: str = ""
    seq: int = 0                 # 导入顺序,时间解析失败/相同时的次级排序键

    def time_key(self):
        t = parse_time(self.time_raw)
        if t is None:
            return (1, dt.datetime.max, self.seq)
        return (0, t, self.seq)


@dataclass
class Cap:
    """岗位组上限:**一个人在这组岗位里最多占几个**。

    比冲突图强一档。冲突是成对的关系,只能说「这两个不能同时占」;而有些要求
    天生是多元的——三个时段 8-12 / 12-14 / 14-18,任意两个都行,三个凑齐就是
    从早干到晚没饭吃。那个约束**成对的冲突图表达不了**:要让三个凑齐时不合法,
    就必须有某一对本身不合法,而那一对恰好是要允许的。

    所以这里做成「组 + 上限」:上面那个例子就是三个岗位一组、上限 2。

    顺带一提,它严格包含冲突图(两个岗位一组、上限 1 就是一条冲突边)。没有合并
    它们:矩阵那套 UI 对成对关系更好用,而且冲突和同批是配对出现的。

    组之间可以重叠,每条各判各的——都满足才放行。
    """
    name: str = ""               # 给人看的名字,如「8月1日全天」
    pids: list = field(default_factory=list)
    limit: int = 0               # 每人在这组里最多占几个


@dataclass
class Assignment:
    pid: str
    vol_key: str
    status: str = AUTO
    forced: bool = False         # 人工强行安排(违反冲突/超额)的标记
    role: str = MEMBER           # MEMBER 普通槽 / LEADER 组长槽


class Project:
    """全部状态:岗位 + 冲突图 + 报名 + 当前排班 + 上次排班快照。"""

    def __init__(self):
        self.positions = {}      # pid -> Position (保持插入顺序)
        self.volunteers = {}     # key -> Volunteer
        self.signups = []        # list[SignupItem]
        self.conflicts = set()   # {frozenset({pid1, pid2})} 时间冲突,同人不能兼
        self.cohorts = set()     # {frozenset({pid1, pid2})} 希望同一批人担任
        self.caps = []           # list[Cap] 岗位组上限,见 Cap
        self.assignments = []    # list[Assignment]
        self.prev_pairs = []     # 上次"应用排班"时的 (pid, vol_key) 快照,用于变更表
        self.rules = rulecfg.default()   # 规则配置,见 rules.py
        self._seq = 0

    # ---------- 冲突图 ----------
    def is_conflict(self, a, b):
        return frozenset((a, b)) in self.conflicts

    def set_conflict(self, a, b, on):
        if a == b:
            return
        p = frozenset((a, b))
        if on:
            self.conflicts.add(p)
        else:
            self.conflicts.discard(p)

    # ---------- 同批图(希望两岗位排同一批人) ----------
    def is_cohort(self, a, b):
        return frozenset((a, b)) in self.cohorts

    def set_cohort(self, a, b, on):
        if a == b:
            return
        p = frozenset((a, b))
        if on:
            self.cohorts.add(p)
        else:
            self.cohorts.discard(p)

    # ---------- 报名 ----------
    def next_seq(self):
        self._seq += 1
        return self._seq

    def merge_volunteer(self, vol: Volunteer):
        """按 key 合并:已存在则用非空字段更新。"""
        old = self.volunteers.get(vol.key)
        if old is None:
            self.volunteers[vol.key] = vol
            return vol
        for f in ("name", "id_card", "contact", "wechat"):
            v = getattr(vol, f)
            if v:
                setattr(old, f, v)
        for k, v in (vol.tags or {}).items():
            if v:
                old.tags[k] = v
        return old

    def add_signup(self, vol_key, pid, time_raw=""):
        """同人同岗位去重(保留最早一条)。返回是否新增了条目。"""
        item = SignupItem(vol_key, pid, time_raw, self.next_seq())
        for old in self.signups:
            if old.vol_key == vol_key and old.pid == pid:
                if item.time_key() < old.time_key():
                    old.time_raw, old.seq = item.time_raw, item.seq
                return False
        self.signups.append(item)
        return True

    def remove_signup(self, vol_key, pid):
        self.signups = [s for s in self.signups
                        if not (s.vol_key == vol_key and s.pid == pid)]

    def desired_map(self):
        """vol_key -> {pid: 最早的报名项},只保留仍存在的岗位/志愿者。"""
        d = {}
        for it in sorted(self.signups, key=lambda x: x.time_key()):
            if it.pid not in self.positions or it.vol_key not in self.volunteers:
                continue
            m = d.setdefault(it.vol_key, {})
            if it.pid not in m:
                m[it.pid] = it
        return d

    # ---------- 排班 ----------
    def assigned_map(self, assignments=None):
        m = {}
        for a in (self.assignments if assignments is None else assignments):
            m.setdefault(a.vol_key, set()).add(a.pid)
        return m

    def filled_map(self, assignments=None):
        m = {pid: 0 for pid in self.positions}
        for a in (self.assignments if assignments is None else assignments):
            if a.pid in m:
                m[a.pid] += 1
        return m

    def current_pairs(self):
        return [(a.pid, a.vol_key) for a in self.assignments]

    # ---------- 岗位增删 ----------
    def remove_position(self, pid):
        self.positions.pop(pid, None)
        self.conflicts = {p for p in self.conflicts if pid not in p}
        self.cohorts = {p for p in self.cohorts if pid not in p}
        self.signups = [s for s in self.signups if s.pid != pid]
        self.assignments = [a for a in self.assignments if a.pid != pid]
        # 组里剔掉它;剔空的组直接丢——留着一个没有成员的上限没有意义,
        # 而且它会在界面上显示成「0 个岗位，每人最多 2 个」这种看不懂的东西。
        for cap in self.caps:
            cap.pids = [p for p in cap.pids if p != pid]
        self.caps = [c for c in self.caps if c.pids]

    # ---------- 序列化 ----------
    def to_dict(self):
        return {
            "version": SCHEMA_VERSION,
            "positions": [asdict(p) for p in self.positions.values()],
            "volunteers": [asdict(v) for v in self.volunteers.values()],
            "signups": [asdict(s) for s in self.signups],
            "conflicts": sorted(sorted(pair) for pair in self.conflicts),
            "cohorts": sorted(sorted(pair) for pair in self.cohorts),
            "caps": [asdict(c) for c in self.caps],
            "assignments": [asdict(a) for a in self.assignments],
            "prev_pairs": [list(t) for t in self.prev_pairs],
            "rules": self.rules,
        }

    @classmethod
    def from_dict(cls, d):
        """把整份状态读回来。**这是唯一的校验关口**。

        `PUT` 进来的整份 JSON 走这里,坏结构在这儿抛,接口层转成 400。
        岗位不再用 `Position(**p)` 直接展开:条件树要归一化、数值要夹住范围,
        展开只能保证「字段名对得上」,保证不了「值说得通」。
        """
        prj = cls()
        for p in d.get("positions", []):
            pos = _position_from_dict(p)
            prj.positions[pos.pid] = pos
        for v in d.get("volunteers", []):
            vol = Volunteer(**v)
            prj.volunteers[vol.key] = vol
        for s in d.get("signups", []):
            prj.signups.append(SignupItem(**s))
        for pair in d.get("conflicts", []):
            if len(pair) == 2:
                prj.conflicts.add(frozenset(pair))
        for pair in d.get("cohorts", []):
            if len(pair) == 2:
                prj.cohorts.add(frozenset(pair))
        prj.caps = [_cap_from_dict(c, prj.positions) for c in d.get("caps", [])]
        prj.caps = [c for c in prj.caps if c.pids]
        # 同一个 (岗位, 人) 只留一行。一个人不可能在一个岗位上站两次,重复行
        # 唯一的来源是界面上的手工操作出了岔子(比如把人调到他已经在的岗位)。
        # 而重复一旦落库就很难发现:名单里多一个名字,导出的表里多一行,
        # 「已排/名额」跟着虚高,谁也想不到去查重。这里是唯一的校验关口,
        # 顺手清掉——已经被弄脏的项目下次保存时自动修好。
        seen = set()
        for a in d.get("assignments", []):
            row = Assignment(**a)
            if (row.pid, row.vol_key) in seen:
                continue
            seen.add((row.pid, row.vol_key))
            prj.assignments.append(row)
        prj.prev_pairs = [tuple(t) for t in d.get("prev_pairs", [])]
        prj.rules = rulecfg.normalize(d.get("rules"))
        prj._seq = max([s.seq for s in prj.signups], default=0)
        return prj


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _cap_from_dict(c, positions):
    if not isinstance(c, dict):
        raise ValueError("岗位组必须是一个对象")
    # 指向已删岗位的成员静默剔掉,和冲突图同一套做法:岗位删了,提到它的关系
    # 就该跟着没,而不是让整份项目读不出来。
    pids, seen = [], set()
    for pid in c.get("pids") or []:
        pid = str(pid).strip()
        if pid in positions and pid not in seen:
            seen.add(pid)
            pids.append(pid)
    limit = max(0, _int(c.get("limit")))
    name = str(c.get("name") or "").strip()
    if pids and limit > len(pids):
        # 上限比成员数还大 = 这条永远拦不住任何人。夹住而不是拒掉:它只是白设,
        # 不是错误,拒掉会让人删不掉一个已经存在的坏配置。体检里会说一声。
        limit = len(pids)
    return Cap(name=name, pids=pids, limit=limit)


def _position_from_dict(p):
    pid = str(p.get("pid") or "").strip()
    if not pid:
        raise ValueError("岗位缺少 pid")
    quota = max(0, _int(p.get("quota")))
    min_quota = max(0, _int(p.get("min_quota")))
    leader_raw = p.get("leader") or {}
    leader_count = max(0, _int(leader_raw.get("count")))

    # 这两条当场拒而不是夹住:夹住的话人以为设成了 10,实际按 5 在排,
    # 而「为什么这个岗位只排了 5 个」是查不出来的。
    if min_quota > quota:
        raise ValueError(f"岗位「{pid}」的最低人数 {min_quota} 超过了名额 {quota}")
    if leader_count > quota:
        raise ValueError(f"岗位「{pid}」的组长人数 {leader_count} 超过了名额 {quota}")

    return Position(
        pid=pid,
        name=p.get("name") or "",
        time_note=p.get("time_note") or "",
        quota=quota,
        min_quota=min_quota,
        requirements=conditions.normalize(p.get("requirements")),
        preferences=[conditions.normalize(x) for x in (p.get("preferences") or [])],
        leader=Leader(count=leader_count,
                      conditions=conditions.normalize(leader_raw.get("conditions"))),
        req_note=p.get("req_note") or "",
    )


# ======================= 资格判定 =======================

def eligible(volunteer, position):
    """满不满足岗位的**必选条件**。不填条件 = 恒通过。见 conditions.py。"""
    return conditions.match(position.requirements, volunteer.tags)


def eligible_leader(volunteer, position):
    """够不够格占这个岗位的**组长槽**。

    组长首先也是这个岗位上的人,所以必选条件照样要过——组长条件是**额外**
    一层,不是替代。`count=0` 的岗位没有组长槽,一律 False。
    """
    if position.leader.count <= 0:
        return False
    return (eligible(volunteer, position)
            and conditions.match(position.leader.conditions, volunteer.tags))


def prefer_rank(volunteer, position):
    """符合到第几条优先条件。**越小越优先**,都不符合排在最后。

    有序列表 + 字典序,不做加权求和:「他满足第 1 条优先、你不满足」能一句话
    解释,加权求和答不了「为什么他进了我没进」。
    """
    for i, cond in enumerate(position.preferences or ()):
        if conditions.match(cond, volunteer.tags):
            return i
    return len(position.preferences or ())


# ======================= 求解器 =======================

REASON_FULL = "岗位名额已满"
REASON_CONFLICT = "与已获岗位冲突"
REASON_REQ = "不符合岗位要求"
REASON_CAP = "超出岗位组上限"

MAX_PASSES = 10000  # 理论上不会触达,仅作保险
RESCUE_DEPTH = 5    # 保底重试的深度,只对「一个岗位都没拿到的人」用


class SolveTimeout(RuntimeError):
    """排班超时。

    **不返回半成品。** 抛出点可能落在 `find_slot` 的回溯中途,那时 assignments
    是被拆了一半的状态——把它当结果交出去,等于给人一份看不出哪里不对的排班。
    调用方应该原样保留上一版排班,只把这个异常说清楚。
    """

    def __init__(self, seconds, hint=""):
        super().__init__(f"排班超过 {seconds:g} 秒仍未算完" + (f"({hint})" if hint else ""))
        self.seconds = seconds
        self.hint = hint


def solve(project, prefer_pairs=None, max_depth=2, timeout=None,
          rescue_depth=RESCUE_DEPTH, rescue_timeout=None, report=None):
    """返回新的排班列表(含原 LOCKED 行 + 新排的 AUTO 行)。

    prefer_pairs: 上一版的 (pid, vol_key) 集合,用于 L4 稳定性平局偏好。
    timeout: 秒。超过就抛 SolveTimeout;None = 不限(供测试和离线使用)。
    rescue_depth / rescue_timeout: 保底重试(P0)的深度和**独立**预算,见 rescue()。
    report: 传个 dict 进来的话,会往里写这一趟的情况(补上几个人、重试有没有超时)。
    """
    # 预算放在可变盒子里:保底重试要临时换一份自己的,换完还得换回来。
    budget = {"at": None if timeout is None else time.monotonic() + timeout,
              "seconds": timeout}
    # 当前正在处理谁、哪个岗位。超时时拿它拼一句人话——"卡住了"和
    # "卡在 P03 检录A组 的增广重排上"对排查的价值差得远。
    at = {"pid": "", "vk": ""}

    def check_time():
        if budget["at"] is not None and time.monotonic() > budget["at"]:
            raise SolveTimeout(budget["seconds"], _where(project, at))

    cfg = rulecfg.normalize(project.rules)
    positions = project.positions
    vols = project.volunteers
    prefer = set(prefer_pairs or ())
    order_index = {pid: i for i, pid in enumerate(positions)}

    # 同批伙伴:pid -> 与之希望"同一批人"的其它岗位
    partners = {pid: set() for pid in positions}
    for pair in project.cohorts:
        a, b = tuple(pair)
        if a in partners and b in partners:
            partners[a].add(b)
            partners[b].add(a)

    # 排班**按岗位分桶存**,求解过程里不维护扁平列表。
    #
    # `find_slot` 每个递归节点都要做两件事:「找出这个岗位里的 AUTO 行」和
    # 「删掉其中一行」。搁在一个扁平列表上,这两件事都是 O(总排班数)——而
    # 这里是整个求解器唯一指数级的地方,节点数上万时就是每节点白扫两遍全表。
    # 分桶之后两件事都只在一个岗位内部动,长度不超过名额(个位数)。
    #
    # 名额也不再单独计数:`len(rows[pid])` 就是已排人数,少一份要同步的状态。
    rows = {pid: [] for pid in positions}
    for a in project.assignments:            # LOCKED 行原样保留并预先占名额
        if a.status == LOCKED and a.pid in positions:
            rows[a.pid].append(Assignment(a.pid, a.vol_key, LOCKED, a.forced, a.role))
    assigned = {}
    for pid, bucket in rows.items():
        for a in bucket:
            assigned.setdefault(a.vol_key, set()).add(pid)

    desired = project.desired_map()

    # ---------- 基础判定 ----------
    def elig(vk, pid):
        return eligible(vols[vk], positions[pid])

    def slot_for(vk, pid):
        """vk 进 pid 该占哪个槽;None = 没位置了。

        **组长槽优先(C7 + S2)。** 组长是稀缺的:第一个来的合格者要是进了普通槽,
        组长槽就可能一直空着,而后面来的人未必够格。所以够格就先占组长槽。

        岗位可能已被 LOCKED 行塞超(人工强排),那时两个分支都进不来,
        新的人就加不进去——和原来「满了就不加」是一个意思。
        """
        pos = positions[pid]
        bucket = rows[pid]
        # 总名额是硬上限,**必须先卡这一道**。两个槽分开算容量的话,LOCKED 行
        # 把普通槽塞超时(人工强排),组长槽那一支还会照放不误,总数就越过 quota 了。
        if len(bucket) >= pos.quota:
            return None
        n_leader = sum(1 for a in bucket if a.role == LEADER)
        if (pos.leader.count and n_leader < pos.leader.count
                and eligible_leader(vols[vk], pos)):
            return LEADER
        if len(bucket) - n_leader < pos.member_quota():
            return MEMBER
        return None

    def frees_slot_for(vk, a):
        """把 a 这一行拆掉之后,vk 用不用得上腾出来的位置。

        **必须在进递归之前算清楚。** 递归成功之后再发现用不上(腾出来的是组长槽
        而 vk 不够格),就得把整条增广链撤回来,而那条链可能已经挪了好几个人——
        撤销逻辑一复杂,「有人凭空掉岗」这种 bug 就藏得住了。
        """
        pos = positions[a.pid]
        bucket = rows[a.pid]
        if len(bucket) - 1 >= pos.quota:       # 拆一个还是满的(LOCKED 塞超了)
            return False
        n_leader = sum(1 for x in bucket if x.role == LEADER) - (a.role == LEADER)
        if (pos.leader.count and n_leader < pos.leader.count
                and eligible_leader(vols[vk], pos)):
            return True
        return (len(bucket) - 1) - n_leader < pos.member_quota()

    # 岗位组上限按 pid 建索引:can_hold 在热循环里,不能每次去遍历所有组。
    # 绝大多数岗位一个组都不在,查表直接返回空。
    caps_of = {}
    for ci, cap in enumerate(project.caps):
        members = frozenset(p for p in cap.pids if p in positions)
        if not members:
            continue
        for pid in members:
            caps_of.setdefault(pid, []).append((members, cap.limit))

    def within_caps(vk, pid, ignore=None):
        """vk 再拿 pid 会不会撑爆某个岗位组的上限(ignore=即将退出的岗位)。"""
        groups = caps_of.get(pid)
        if not groups:
            return True
        held = assigned.get(vk, ())
        for members, limit in groups:
            n = sum(1 for p in held if p != ignore and p in members)
            if n >= limit:
                return False
        return True

    def can_hold(vk, pid, ignore=None):
        """vk 再拿 pid 合不合法:不与已获岗位冲突,也不撑爆岗位组上限。

        (ignore = 即将退出的岗位,增广挪人时用。)
        """
        if not all(p == ignore or not project.is_conflict(p, pid)
                   for p in assigned.get(vk, ())):
            return False
        return within_caps(vk, pid, ignore)

    def place(vk, pid, role=MEMBER):
        rows[pid].append(Assignment(pid, vk, AUTO, False, role))
        assigned.setdefault(vk, set()).add(pid)

    def unplace(a):
        rows[a.pid].remove(a)                # 桶内查找,长度 = 该岗位已排人数
        assigned[a.vol_key].discard(a.pid)

    # ---------- 候选排序(体现 L1 与 L4) ----------
    def below_min(pid):
        """离最低人数还差多少(按比例)。达标了返回 0——这一项只在没到线时说话。

        它排在缺口率之前:名额 10 排了 2 个、和名额 3 最低 3 排了 2 个,后者更急,
        但按缺口率是前者更大。C1。
        """
        m = positions[pid].min_quota
        if m <= 0:
            return 0.0
        return max(0, m - len(rows[pid])) / m

    def gap_ratio(pid):
        q = positions[pid].quota
        return max(0, q - len(rows[pid])) / q if q > 0 else 0.0

    def others_want(pid, exclude_vk):
        """还有多少其他人想要且有资格要 pid(未得到的)。少者说明该岗位更依赖当前人。"""
        n = 0
        for vk, dm in desired.items():
            if vk == exclude_vk or pid not in dm:
                continue
            if pid in assigned.get(vk, ()):
                continue
            if elig(vk, pid):
                n += 1
        return n

    def cohort_gain(vk, pid):
        """vk 已获得 pid 的几个同批伙伴岗位(越多,放这里越能凑齐同一批人)。"""
        if not partners[pid]:
            return 0
        return sum(1 for q in partners[pid] if q in assigned.get(vk, ()))

    # 候选排序按配置**编译一次**,不是每次求值时查字典——这是热循环,
    # 每一轮、每个人、每个候选岗位各走一遍。岗位表顺序永远垫底且不可配:
    # 少了它,同一份数据两次求解可能给出不同结果。
    CAND_PARTS = {
        "min_quota": lambda vk, pid: -below_min(pid),
        "gap": lambda vk, pid: -gap_ratio(pid),
        "cohort": lambda vk, pid: -cohort_gain(vk, pid),
        "stable": lambda vk, pid: 0 if (pid, vk) in prefer else 1,
        "scarce": lambda vk, pid: others_want(pid, vk),
        "preference": lambda vk, pid: prefer_rank(vols[vk], positions[pid]),
    }
    cand_parts = tuple(CAND_PARTS[k] for k in cfg["candidate"])

    def cand_key(vk, pid):
        return tuple(part(vk, pid) for part in cand_parts) + (order_index[pid],)

    # ---------- S1:优先条件逐档放行 ----------
    #
    # 光在 cand_key 里加一项(C6)效果很弱:C 层排的是「这个人挑哪个岗位」,
    # 而优先条件想管的是「这个岗位要谁」,方向不对。真正让它起作用的是这里。
    #
    # **逐档,不是一刀切。** 只分「符合/不符合」两阶段的话,第 1 优先和第 2 优先
    # 会一起放进来,谁先拿到只看报名时间——多档就白设了。所以第 0 轮只放符合
    # 第 1 档的人,第 1 轮放到第 2 档,……最后一轮全开。这也正是「按先后比、
    # 不做加权」在算法上的样子。
    #
    # 不做抢占式(符合的人把不符合的挤走):`find_slot` 天然支持,但那会破坏
    # 「先到先得」的直觉,而且放大唯一那个指数级搜索的空间。
    phase = {"tier": None}          # None = 全开

    def allowed(vk, pid):
        tier = phase["tier"]
        if tier is None:
            return True
        prefs = positions[pid].preferences
        if not prefs:               # 没设优先条件的岗位任何一轮都不受限
            return True
        return prefer_rank(vols[vk], positions[pid]) <= tier

    # ---------- 增广:为 vk 在 pid 腾出一个位置 ----------
    def find_slot(vk, pid, visited, depth):
        # 闸门放在这里是关键:这个函数是整个求解器里唯一指数级的地方,
        # 分支数 = 岗位内已排人数 × 每人的备选岗位数。真要跑飞,一定是飞在
        # 这儿,而不是在外面那两层有硬上限的循环里。
        # time.monotonic() 约 50ns,调上百万次也就几十毫秒,可以忽略。
        check_time()
        slot = slot_for(vk, pid)
        if slot is not None:
            place(vk, pid, slot)
            return True
        if depth <= 0:
            return False
        # 快照:循环体里会把 a 从桶里摘出去又放回来,直接迭代桶会漏人。
        # 同时筛掉「腾了也没用」的行——见 frees_slot_for。
        for a in [x for x in rows[pid] if x.status == AUTO and frees_slot_for(vk, x)]:
            v2 = a.vol_key
            # 被挪走的人去哪儿也受阶段约束——否则第一阶段就能把不符合优先条件的人
            # 从别处塞进一个带优先条件的岗位,那这道闸门等于没有。
            alts = [p2 for p2 in desired.get(v2, {})
                    if p2 != pid and p2 not in visited
                    and p2 not in assigned.get(v2, ())
                    and elig(v2, p2) and can_hold(v2, p2, ignore=pid)
                    and allowed(v2, p2)]
            alts.sort(key=lambda p2: (0 if slot_for(v2, p2) is not None else 1,
                                      0 if (p2, v2) in prefer else 1,
                                      order_index[p2]))
            for p2 in alts:
                unplace(a)
                if find_slot(v2, p2, visited | {p2}, depth - 1):
                    # 腾出来的可能是组长槽也可能是普通槽,重新判一次角色——
                    # 直接沿用 a.role 会让不够格的人顶着组长的名分坐进来。
                    # frees_slot_for 已经保证这里判得出槽,而递归动不到 pid
                    # 这个桶(pid 一直在 visited 里,alts 排除了它)。
                    place(vk, pid, slot_for(vk, pid))
                    return True
                # 撤销:v2 放回原岗位
                rows[pid].append(a)
                assigned[v2].add(pid)
        return False

    # ---------- 主循环:每一轮每人至多新增一个岗位 ----------
    def unmet(vk):
        return [p for p in desired.get(vk, {}) if p not in assigned.get(vk, ())]

    def min_ts(vk):
        return min(desired[vk][p].time_key() for p in unmet(vk))

    # 轮次顺序也按配置编译。**这套键必须和候补队列用的完全一致**——
    # 排位和实际排班要是两套顺序,页面上写着「你是第 3 位候补」就是骗人。
    # 所以两边都从 rules.order_fields 取,不各写一份。见 build_queue。
    ORDER_PARTS = {
        "spread": lambda vk: len(assigned.get(vk, ())),
        "signup_time": min_ts,
        "few_options": lambda vk: len(desired.get(vk, {})),
    }
    order_parts = tuple(ORDER_PARTS[k] for k in rulecfg.order_fields(cfg))
    one_per_round = rulecfg.round_cap(cfg)

    def sweep():
        """反复重排,直到一轮下来没有任何进展。"""
        for _ in range(MAX_PASSES):
            check_time()
            progress = False
            order = sorted((vk for vk in desired if unmet(vk)),
                           key=lambda vk: tuple(part(vk) for part in order_parts))
            for vk in order:
                at["vk"] = vk
                # cand_key 里的 others_want 是 O(人数),被放在排序键里,人多岗位多时
                # 这一句本身就不便宜——所以每个人也查一次时间。
                check_time()
                cands = [p for p in unmet(vk)
                         if elig(vk, p) and can_hold(vk, p) and allowed(vk, p)]
                cands.sort(key=lambda p: cand_key(vk, p))
                for pid in cands:
                    at["pid"] = pid
                    # 关掉「每轮至多一个」时,一个人会在同一轮里连拿几个岗位,
                    # 而 cands 是拿之前算的——每次都要重新看一眼还占不占得住。
                    if pid in assigned.get(vk, ()) or not can_hold(vk, pid):
                        continue
                    if find_slot(vk, pid, {pid}, max_depth):
                        progress = True
                        if one_per_round:
                            break
            if not progress:
                return

    if cfg["toggles"]["preference_phase"]:
        for tier in range(max((len(p.preferences) for p in positions.values()), default=0)):
            phase["tier"] = tier    # 第 tier 档(含之前各档)先进
            sweep()
    phase["tier"] = None
    sweep()                         # 最后开放给所有人

    # ---------- P0 保底重试 ----------
    #
    # 主段的深度顾的是效率(见 solver.py),代价是有一类长腾位链走不通,
    # 于是有人一个岗位都没拿到。这一趟**只针对那些人**用大深度再试一次——
    # 「我报了名却什么都没排上」是运营方最容易挨骂的地方。
    #
    # ⚠️ **实测它一个人都没多救到。** 300 个随机实例(含名额全 1、人数贴着岗位数、
    # 志愿高度重叠这种最容易出长链的形状),depth=5 重试和不重试的零岗位人数
    # 逐个相等,0 个实例受益。原因大概是外层那个「反复重排直到没有进展」的循环
    # 本身就已经收敛到了最大可匹配集合,剩下的人是真的没位置,不是深度不够。
    #
    # 那为什么留着:代价很小(实际项目规模下 +0.01~0.7 秒,见 RULES.md 7.3),
    # 有独立预算烧不动就停,而且有快照兜底不会弄坏结果。它是保险,不是优化——
    # 真实报名数据的形状未必和随机生成的一样。**别把它当成能提升排班率的东西**,
    # 哪天要砍成本,第一个该砍的就是它。
    #
    # 它是锦上添花的一趟,所以有两条特殊规矩:
    #   1. **自己一份独立预算**,烧不动就停,不占主段的时间;
    #   2. **超时不算失败**——保留主段结果照常返回,只在界面上说一句。
    #      但抛出点可能落在增广的中途(那时排班是被拆了一半的),所以
    #      必须先拍个快照,超时了整个还原,而不是把半成品端出去。
    def rescue():
        stranded = sorted((vk for vk in desired if not assigned.get(vk)), key=min_ts)
        if not stranded:
            return 0
        before = sum(len(bucket) for bucket in rows.values())
        for vk in stranded:
            at["vk"] = vk
            check_time()
            cands = [p for p in unmet(vk) if elig(vk, p) and can_hold(vk, p)]
            cands.sort(key=lambda p: cand_key(vk, p))
            for pid in cands:
                at["pid"] = pid
                if find_slot(vk, pid, {pid}, rescue_depth):
                    break
        return sum(len(bucket) for bucket in rows.values()) - before

    rescued, rescue_timed_out = 0, False
    if rescue_depth > max_depth:
        snapshot = ({pid: list(b) for pid, b in rows.items()},
                    {vk: set(p) for vk, p in assigned.items()})
        keep = (budget["at"], budget["seconds"])
        started_rescue = time.monotonic()
        budget["at"] = None if rescue_timeout is None else started_rescue + rescue_timeout
        budget["seconds"] = rescue_timeout
        try:
            rescued = rescue()
        except SolveTimeout:
            rescue_timed_out = True
            rows.clear()
            rows.update(snapshot[0])
            assigned.clear()
            assigned.update(snapshot[1])
        finally:
            # 还预算时要把重试烧掉的墙钟**补回去**。两段预算说好是互不挪用的,
            # 而主段的截止时间是个绝对时刻——不补的话,重试烧满 20 秒回来,
            # 后处理第一次查表就发现主段"超时"了,整趟被判降级。
            budget["seconds"] = keep[1]
            budget["at"] = (None if keep[0] is None
                            else keep[0] + (time.monotonic() - started_rescue))

    if report is not None:
        report["rescued"] = rescued
        report["rescue_timed_out"] = rescue_timed_out
        report["stranded"] = sum(1 for vk in desired if not assigned.get(vk))

    # 摊平成一份列表,按岗位表顺序——**这就是导出/存库看到的顺序**,
    # 所以它必须只依赖岗位顺序和桶内顺序,不能沾上任何 set 的迭代顺序。
    assignments = [a for pid in positions for a in rows[pid]]
    post_process(project, assignments, check_time, cfg)
    return assignments


def _where(project, at):
    """把「卡在哪」拼成一句人话。"""
    pid = at.get("pid") or ""
    pos = project.positions.get(pid)
    if pos is not None:
        return f"卡在岗位「{pos.label()}」上"
    return ""


# ======================= 后处理(P 层) =======================
#
# 贯穿整层的不变式,记住这一条就够了:
#
#     **求解本身决定「谁有岗位」,后处理只决定「谁在哪个岗位」。**
#
# 每个 pass 都不增不减已排人次、不让任何人掉岗、不动 LOCKED 行、不违反
# 冲突/要求。它们结构完全同构,所以共用下面这个 `_Board`。
#
# 执行顺序固定 P1 → P3,不可配:它们会互相破坏(补位可能拆散同批),
# 固定顺序 + 「每个 pass 只做不破坏前面结果的改动」是唯一能推理清楚的做法。
# 同批放最后,因为它是纯偏好,被牺牲掉损失最小。

_GUARD = 10000   # 各 pass 的死循环保险;正常情况远远到不了


class _Board:
    """后处理共用的三张索引表:岗位→人、人→岗位、(岗位,人)→行。

    改动一律通过 `move` / `swap` 走,它们**复用同一个 Assignment 对象**——
    这样 status(AUTO/LOCKED)、forced、role 自动跟着走,不会在某个 pass 里
    悄悄把一行的角色弄丢。
    """

    def __init__(self, project, assignments):
        self.project = project
        self.positions = project.positions
        self.holders = {pid: set() for pid in self.positions}
        self.held = {}
        self.row = {}
        # 岗位组上限,索引方式和 solve 里那份一致
        self.caps_of = {}
        for cap in project.caps:
            members = frozenset(p for p in cap.pids if p in self.positions)
            if not members:
                continue
            for pid in members:
                self.caps_of.setdefault(pid, []).append((members, cap.limit))
        for a in assignments:
            if a.pid in self.positions:
                self.holders[a.pid].add(a.vol_key)
                self.held.setdefault(a.vol_key, set()).add(a.pid)
                self.row[(a.pid, a.vol_key)] = a
        self.desired = project.desired_map()

    def filled(self, pid):
        return len(self.holders[pid])

    def leaders(self, pid, ignore_vk=None):
        return sum(1 for vk in self.holders[pid]
                   if vk != ignore_vk and self.row[(pid, vk)].role == LEADER)

    def movable(self, pid, vk):
        """这一行动不动得。LOCKED 绝不动(H1)。"""
        a = self.row.get((pid, vk))
        return a is not None and a.status == AUTO

    def slot_for(self, vk, pid, ignore_vk=None):
        """vk 进 pid 该占哪个槽;None = 没位置。`ignore_vk` 是即将腾出去的那个人。

        **每个 pass 换人时都要重判角色。** 沿用被换掉那一行的 role,会让不够格的
        人顶着组长的名分坐进来——而那一行在导出表里就写着「组长」。
        """
        pos = self.positions[pid]
        held = self.holders[pid]
        n_total = len(held) - (1 if ignore_vk in held else 0)
        if n_total >= pos.quota:               # 总名额硬上限,先卡(同 solve.slot_for)
            return None
        n_leader = self.leaders(pid, ignore_vk)
        if (pos.leader.count and n_leader < pos.leader.count
                and eligible_leader(self.project.volunteers[vk], pos)):
            return LEADER
        if n_total - n_leader < pos.member_quota():
            return MEMBER
        return None

    def can_take(self, vk, pid, leaving=None, ignore_vk=None):
        """vk 去 pid 合不合法:没占着、报过名、够格、不冲突、还有合适的槽。"""
        if pid in self.held.get(vk, ()):
            return False
        if pid not in self.desired.get(vk, {}):
            return False
        if not eligible(self.project.volunteers[vk], self.positions[pid]):
            return False
        if not all(p == leaving or not self.project.is_conflict(p, pid)
                   for p in self.held.get(vk, ())):
            return False
        held = self.held.get(vk, ())
        for members, limit in self.caps_of.get(pid, ()):
            if sum(1 for p in held if p != leaving and p in members) >= limit:
                return False
        return self.slot_for(vk, pid, ignore_vk) is not None

    def move(self, vk, src, dst):
        """把 vk 从 src 挪到 dst。人次不变,人不掉岗,角色按 dst 重判。"""
        role = self.slot_for(vk, dst)
        a = self.row.pop((src, vk))
        self.holders[src].discard(vk)
        self.held[vk].discard(src)
        a.pid = dst
        a.role = role
        self.holders[dst].add(vk)
        self.held[vk].add(dst)
        self.row[(dst, vk)] = a

    def swap(self, pid, out_vk, in_vk):
        """把 pid 上的 out_vk 换成 in_vk。调用方负责保证 out_vk 不会因此掉岗。"""
        role = self.slot_for(in_vk, pid, ignore_vk=out_vk)
        a = self.row.pop((pid, out_vk))
        self.holders[pid].discard(out_vk)
        self.held[out_vk].discard(pid)
        a.vol_key = in_vk
        a.role = role
        self.holders[pid].add(in_vk)
        self.held.setdefault(in_vk, set()).add(pid)
        self.row[(pid, in_vk)] = a

    def applicants(self, pid):
        """报了 pid 的人,按报名时间从早到晚。顺序必须确定,否则两次求解会不一样。"""
        out = []
        for vk, wishes in self.desired.items():
            item = wishes.get(pid)
            if item is not None:
                out.append((item.time_key(), vk))
        out.sort()
        return [vk for _t, vk in out]


def post_process(project, assignments, check_time=None, cfg=None):
    """P 层:按固定顺序跑各 pass。原地改 assignments,返回它。

    顺序 P1 → P2 → P3 **不可配**:它们会互相破坏(补位可能拆散同批),
    固定顺序 + 「每个 pass 只做不破坏前面结果的改动」是唯一能推理清楚的做法。
    能配的只是某个 pass 开不开——P1 跟着「最低人数」那条候选规则走
    (关掉最低人数排序却还去补最低线,是自相矛盾的),P3 有自己的开关。
    """
    cfg = rulecfg.normalize(cfg if cfg is not None else project.rules)
    board = _Board(project, assignments)
    if "min_quota" in cfg["candidate"]:
        _fill_min_quota(board, check_time)      # P1
    _fill_leader_slots(board, check_time)       # P2:跟着小组长功能,没有开关
    if cfg["toggles"]["cohort_swap"]:
        _improve_cohort(board, check_time)      # P3
    return assignments


def _fill_min_quota(board, check_time=None):
    """P1 最低人数补位:从「抽走一人仍在最低线之上」的岗位调人去补缺口。

    **光有 C1 排序不够。** 先到先得会让早报名的人先把热门岗位占满,轮到冷门
    岗位时已经没人可调了——那时它离最低线还差几个,而排序早就没机会说话了。
    这一趟专门做「劫富济贫」。

    收敛:每成功一次,「低于最低线的总缺口」严格减 1(目标岗位补了一个,
    供给岗位保证不跌破线),而它非负,所以必然停。
    """
    positions = board.positions
    if not any(p.min_quota > 0 for p in positions.values()):
        return

    def short(pid):
        return max(0, positions[pid].min_quota - board.filled(pid))

    for _ in range(_GUARD):
        if check_time:
            check_time()
        moved = False
        for dst in positions:
            while short(dst):
                donor = _find_donor(board, dst, short)
                if donor is None:
                    break
                vk, src = donor
                board.move(vk, src, dst)
                moved = True
        if not moved:
            return


def _find_donor(board, dst, short):
    """找一个能挪到 dst 的人,返回 (vol_key, 他现在待的岗位)。

    候选按**报名 dst 的时间**从早到晚——补位也得讲先到先得,而且顺序确定,
    否则两次求解结果会不一样。
    """
    positions = board.positions
    for vk in board.applicants(dst):
        for src in sorted(board.held.get(vk, ())):
            if src == dst or not board.movable(src, vk):
                continue
            # 抽走之后 src 自己跌破最低线,那是拆东墙补西墙
            if board.filled(src) - 1 < positions[src].min_quota:
                continue
            # 合法性一律带上 leaving=src 判。**不能先来一道不带 leaving 的预过滤**:
            # 那会把「src 和 dst 时间冲突」的人挡在门外,而那恰恰是最该挪的一类——
            # 他一离开 src,冲突就没了。
            if board.can_take(vk, dst, leaving=src):
                return vk, src
    return None


def _fill_leader_slots(board, check_time=None):
    """P2 组长补位:组长槽空着,而岗位里已经有够格的人待在普通槽,就把他升上去。

    求解本身已经让够格的人优先占组长槽(slot_for),所以这一趟主要收拾 P1 的尾巴——
    P1 把人从别的岗位挪过来时,那个岗位的组长槽可能刚好是空的。

    **只改角色,不动人。** 人次不变,谁也没掉岗;升上去腾出的普通槽这一层不管填,
    P 层不增人,那是求解本身的活。
    """
    if check_time:
        check_time()
    for pid, pos in board.positions.items():
        if pos.leader.count <= 0:
            continue
        for vk in board.applicants(pid):          # 报名早的先升
            if board.leaders(pid) >= pos.leader.count:
                break
            row = board.row.get((pid, vk))
            if row is None or row.role == LEADER or row.status == LOCKED:
                continue
            if eligible_leader(board.project.volunteers[vk], pos):
                row.role = LEADER


def _improve_cohort(board, check_time=None):
    """P3 同批换人:在不减少人次、不让人掉岗的前提下,尽量让同批岗位由同一批人担任。

    做法:对某个同批岗位 A,若它某个自动行的持有者 x 并非同批伙伴,而某个在 A 的
    伙伴岗位里、报了 A、够格、无冲突的 y 顶上能提升总同批重叠度,就把这个位置从
    x 换成 y。x 至少还留着另一个岗位(绝不掉岗)。重叠度严格增大才换,保证收敛。
    """
    positions = board.positions
    project = board.project
    partners = {pid: set() for pid in positions}
    for pair in project.cohorts:
        if len(pair) != 2:
            continue
        a, b = tuple(pair)
        if a in partners and b in partners:
            partners[a].add(b)
            partners[b].add(a)
    if not any(partners.values()):
        return

    def swap_delta(A, x, y):
        """把 A 上的 x 换成 y,总同批重叠度的净变化。"""
        d = 0
        for B in partners[A]:
            if x in board.holders[B]:
                d -= 1
            if y in board.holders[B]:
                d += 1
        return d

    for _ in range(_GUARD):
        if check_time:
            check_time()
        changed = False
        for A in positions:
            if not partners[A]:
                continue
            cand_y = set()
            for B in partners[A]:
                cand_y |= board.holders[B]
            for x in sorted(board.holders[A]):
                if not board.movable(A, x):
                    continue
                if len(board.held.get(x, ())) < 2:   # 换出后 x 会掉岗,禁止
                    continue
                best, best_d = None, 0
                for y in sorted(cand_y):
                    if y == x or not board.can_take(y, A):
                        continue
                    d = swap_delta(A, x, y)
                    if d > best_d:
                        best, best_d = y, d
                if best is not None:
                    board.swap(A, x, best)
                    changed = True
        if not changed:
            return


def build_queue(project, assignments=None):
    """候补队列:当前状态下所有未成交的报名项 + 原因 + 岗位内排位。

    纯派生数据,任何手动改动后重算即可。

    ⚠️ **排位必须和求解器的轮次顺序是同一套。** 两边都从 `rules.order_fields`
    取键,不各写一份——否则页面上写着「你是第 3 位候补」,实际排班却按另一套顺序,
    那是骗人。规则配置一改,这里跟着变。
    """
    if assignments is None:
        assignments = project.assignments
    assigned = {}
    for a in assignments:
        assigned.setdefault(a.vol_key, set()).add(a.pid)

    desired = project.desired_map()
    cfg = rulecfg.normalize(project.rules)
    fields = rulecfg.order_fields(cfg)

    caps_of = {}
    for cap in project.caps:
        members = frozenset(p for p in cap.pids if p in project.positions)
        if members:
            for pid in members:
                caps_of.setdefault(pid, []).append((cap, members))

    def capped_by(vk, pid):
        """挡住 (vk, pid) 的那个岗位组;没有就返回 None。

        原因里要能点出**是哪一组**——只说「超出上限」的话,人拿着这句话去查
        还是不知道该松哪一条。
        """
        held = assigned.get(vk, ())
        for cap, members in caps_of.get(pid, ()):
            if sum(1 for p in held if p in members) >= cap.limit:
                return cap
        return None

    rows = []
    for vk, dm in desired.items():
        vol = project.volunteers[vk]
        n_got = len(assigned.get(vk, ()))
        for pid, item in dm.items():
            if pid in assigned.get(vk, ()):
                continue
            pos = project.positions[pid]
            cap = None
            if not eligible(vol, pos):
                reason = REASON_REQ
            elif any(project.is_conflict(pid, p) for p in assigned.get(vk, ())):
                reason = REASON_CONFLICT
            elif (cap := capped_by(vk, pid)) is not None:
                # 点名是哪一组：只说「超出上限」的话，人还是不知道该松哪一条
                reason = f"{REASON_CAP}（{cap.name or '未命名组'}：最多 {cap.limit} 个）"
            else:
                reason = REASON_FULL
            rows.append({
                "pid": pid, "pos_name": pos.name,
                "vol_key": vk, "name": vol.name,
                "id_card": vol.id_card, "contact": vol.contact,
                "wechat": vol.wechat, "tags": vol.tags,
                "time_raw": item.time_raw, "count": n_got, "reason": reason,
                "_sort": tuple({
                    "spread": n_got,
                    "signup_time": item.time_key(),
                    "few_options": len(dm),
                }[f] for f in fields),
            })

    # 排位:与排班同一套键(见函数开头那段),岗位内计数
    rows.sort(key=lambda r: r["_sort"])
    rank = {}
    for r in rows:
        rank[r["pid"]] = rank.get(r["pid"], 0) + 1
        r["rank"] = rank[r["pid"]]
    order_index = {pid: i for i, pid in enumerate(project.positions)}
    rows.sort(key=lambda r: (order_index.get(r["pid"], 999), r["rank"]))
    for r in rows:
        del r["_sort"]
    return rows


def position_stats(project):
    """每岗位:名额/最低人数/已排/缺口/组长槽。"""
    filled = project.filled_map()
    leaders = {pid: 0 for pid in project.positions}
    for a in project.assignments:
        if a.role == LEADER and a.pid in leaders:
            leaders[a.pid] += 1

    out = []
    for pid, pos in project.positions.items():
        f = filled.get(pid, 0)
        gap = pos.quota - f
        # 四档,不是三档:「不足最低」和「达标但没满」在界面上得分得开——
        # 前者是要去催报名的,后者只是没排满,性质差得远。
        if pos.min_quota and f < pos.min_quota:
            state = f"不足最低（差{pos.min_quota - f}人）"
        elif gap > 0:
            state = f"缺{gap}人"
        elif gap < 0:
            state = f"超{-gap}人"
        else:
            state = "满"
        out.append({"pid": pid, "name": pos.name, "time_note": pos.time_note,
                    "quota": pos.quota, "min_quota": pos.min_quota,
                    "filled": f, "gap": max(0, gap), "state": state,
                    "leader_quota": pos.leader.count, "leader_filled": leaders[pid],
                    # 条件在这里拼成人话。**前端不许自己拼**——三处条件（必选/
                    # 优先/组长）共用 conditions.describe，各画各的迟早会在
                    # 「≥ 还是 >」这种地方对不上，那时谁对谁错没人说得清。
                    "requirements": conditions.describe(pos.requirements),
                    "preferences": [conditions.describe(c) for c in pos.preferences],
                    "leader_conditions": conditions.describe(pos.leader.conditions),
                    "req_note": pos.req_note})
    return out


# ======================= 变更对比 =======================

def diff_pairs(old_pairs, new_pairs):
    """old/new 均为 (pid, vol_key) 序列。返回 (rows, summary)。

    rows: [{"type": 新增|移除|换岗, "vol_key", "from", "to"}]
    """
    old_by, new_by = {}, {}
    for pid, vk in old_pairs:
        old_by.setdefault(vk, set()).add(pid)
    for pid, vk in new_pairs:
        new_by.setdefault(vk, set()).add(pid)

    rows = []
    for vk in sorted(set(old_by) | set(new_by)):
        lost = sorted(old_by.get(vk, set()) - new_by.get(vk, set()))
        gained = sorted(new_by.get(vk, set()) - old_by.get(vk, set()))
        if not lost and not gained:
            continue
        if lost and gained:
            t = "换岗"
        elif gained:
            t = "新增"
        else:
            t = "移除"
        rows.append({"type": t, "vol_key": vk,
                     "from": ";".join(lost), "to": ";".join(gained)})
    return rows, Counter(r["type"] for r in rows)
