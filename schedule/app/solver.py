"""什么时候用多深的增广——求解的**调用策略**，和算法本身分开。

## 深度是什么

「腾位置时最多连锁挪几个人」。想把小明排进已经满员的检录A组：深度 1 = 把里面
某个人挪到一个**当场就有空位**的岗位；深度 2 = 那个人想去的岗位也满了，就再把
那个岗位里的人挪走……深度 k 就是最多连锁挪 k 个人。

**它只影响一件事：能不能把人塞进已经满员的岗位。** 岗位有空位时 `find_slot`
第一句就放进去了，深度根本用不上。

每一层要试「岗位里的每个人 × 他报过的每个其他岗位」，名额 2~3、人均备选 3~5
的话每层 6~15 条岔路，而且同一片子树会被不同岔路反复重走。深度一大就爆炸：

    深度 2  →  10² 量级的叶子路径
    深度 6  →  10⁶ 量级

## 为什么主段是 2 而不是 1 或 6

量过：14 岗位 / 30 人 / 名额 1~3 / 人均报 2~4 / 三成岗位有性别要求 / 带冲突图，
120 个随机实例，depth=6 和 depth=1 排进去的人次**完全相同**（3189 对 3189），
耗时 0.48 秒对 16.38 秒。原因大概是外层那个「反复重排直到没有进展」的循环本身
就把长链拆成了多轮短挪。所以 6 是白花钱。

但也不能直接压到 1。深度 2 多覆盖的是这类链，它在**名额小、接近饱和**时并不罕见：

    P1、P2 名额各 1，甲在 P1、乙在 P2，P3 空着。
    甲报了 P1/P2，乙报了 P2/P3，丙只报了 P1。
    深度 1：甲想挪去 P2，P2 满，失败 → 丙空手。
    深度 2：甲→P2，乙→P3，丙进 P1 → 三个人都有岗位。

而「小组长」正好制造大量容量 1~2 的槽位，所以 2 不只是折中，是针对新规则的对冲。

## 时间预算

两段各有各的上限，互不挪用（见 config.py）：主段 60 秒，超时了降级段再给 60 秒。
最坏情况一次排班占一个核 120 秒。**这是保护服务器的闸门，不是照顾等待的人**
——所以降级段不是「用剩下的时间」，而是重新给一份足额的预算：它是保底出结果的
那一段，饿着它没有意义。

深度 2 之下这两道闸门正常项目根本碰不到；留着是为了兜住病态的岗位表。
"""
from __future__ import annotations

import time
from dataclasses import dataclass

from app.config import get_settings
from app.core import SolveTimeout, solve

MAIN_DEPTH = 2      # 正常走这一段
FALLBACK_DEPTH = 1  # 只挪一层：把人挪到一个"当场就有空位"的岗位。多项式，不会爆炸


@dataclass(frozen=True)
class SolveOutcome:
    assignments: list
    # 用了哪一段。前端要把这件事说出来——结果可能和"从容算完"的那版不同。
    degraded: bool
    seconds: float
    # 保底重试补上了几个人；重试自己有没有超时（超时不影响主结果，但要说一声）。
    rescued: int = 0
    rescue_timed_out: bool = False


def run_solve(project, prefer_pairs=None, main_seconds=None, fallback_seconds=None,
              rescue_seconds=None) -> SolveOutcome:
    """排一次班。

    两段都超时才抛 SolveTimeout——那说明项目大到连最浅的一层都算不完，
    这时候该让人去拆分活动，而不是继续等。

    **保底重试跟着两段一起跑**，无论主段还是降级段：它只针对一个岗位都没拿到的
    人，那批人在降级路径下只会更多，正是更需要它的时候。它有自己的预算，
    超时也只是少补几个人，不影响这一趟的成败。
    """
    settings = get_settings()
    main = settings.solve_main_seconds if main_seconds is None else main_seconds
    fallback = settings.solve_fallback_seconds if fallback_seconds is None else fallback_seconds
    rescue = settings.solve_rescue_seconds if rescue_seconds is None else rescue_seconds

    started = time.monotonic()
    report: dict = {}
    try:
        assignments = solve(project, prefer_pairs, max_depth=MAIN_DEPTH, timeout=main,
                            rescue_timeout=rescue, report=report)
        degraded = False
    except SolveTimeout:
        report = {}
        assignments = solve(project, prefer_pairs, max_depth=FALLBACK_DEPTH, timeout=fallback,
                            rescue_timeout=rescue, report=report)
        degraded = True

    return SolveOutcome(
        assignments, degraded, time.monotonic() - started,
        rescued=report.get("rescued", 0),
        rescue_timed_out=report.get("rescue_timed_out", False),
    )
