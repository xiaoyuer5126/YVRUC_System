"""学生端活动页「人人都一样」那部分的短缓存。

抢报那一刻几百人同时打开同一个活动页、同时刷广场，每人一遍岗位树、一遍
人数统计，数据库白白把同一份东西算几百次。这里把**与看的人无关**的部分
（活动/场次/岗位的文字、名额与人数、材料数量、问卷题目、变更记录）在 Redis
里存 TTL_MS 毫秒；**因人而异**的部分（我的报名状态、能不能报、培训内容、
组长联系方式……）每次现算，从不进缓存——哪些字段是因人而异的，见
serializers 里「因人而异的字段」那张清单。

· 显示的剩余名额最多比实际晚 3 秒，这是业主认可过的；**谁报得上始终由数据库
  判定**（名额闸门 + 场次锁 + 唯一索引），缓存只影响「页面上显示的数」。
· 和时间有关的字段（开没开始报名、倒计时毫秒数、场次开没开场）也不进缓存：
  报名开始那一刻缓存里的「未开始」要是再挂 3 秒，按钮就会在刷新后又灰回去。
· 管理者看自己的活动不走缓存：刚改完的东西必须立刻看到原样。
· Redis 用不了就直接现算——缓存只是省事，不能变成新的故障点。

失效：TTL 之外，任何事务只要写了跟某个活动相关的行（活动、场次、岗位、报名、
材料、问卷题、留痕），提交之后把那个活动的版本号加一，旧缓存随之作废。靠的是
会话的 after_flush / after_commit，不靠各个接口记得调用——写接口有几十个，
逐个加必然漏。

版本号而不是直接删键：抢报时每落一条报名就作废一次，直接删的话缓存几乎
总是空的，几百个读请求一起扑到数据库上（击穿）。现在作废后只有**一个**请求
去重算，其余先用那份旧的——它仍在 3 秒之内，不违背上面那条承诺。
"""
from __future__ import annotations

import contextlib
import json
import logging

import redis
from sqlalchemy import event, select
from sqlalchemy.orm import util as orm_util

from . import locks
from .database import SessionLocal
from .models import (Activity, Attachment, AuditLog, Enrollment, FormQuestion,
                     Position, Shift, ShiftPeriod)

logger = logging.getLogger(__name__)

TTL_MS = 3_000          # 业主认可的「剩余名额可以晚几秒」
FILL_MS = 2_000         # 作废后「由我来重算」的占位，重算的人崩了也不会一直占着
VER_TTL_SECONDS = 86_400

# 用例里对照「开/关缓存结果一致」时关掉它
ENABLED = True


def _client():
    return locks._client


def _key(kind: str, activity_id: str) -> str:
    return f"yvruc:pub:{kind}:{activity_id}"


def _ver_key(activity_id: str) -> str:
    return f"yvruc:pub:ver:{activity_id}"


def get_many(kind: str, ids: list[str], build) -> dict[str, dict]:
    """取一批活动的公共部分：{活动 id: dict}。

    build(ids) 负责把缓存里没有（或已作废）的那些现算出来，返回同样的形状；
    它最好一次查完这一批，而不是逐个查。返回的 dict 调用方可以随意改——
    命中的是刚从 JSON 解出来的新对象，现算的也已经先写进了缓存。
    """
    ids = list(dict.fromkeys(ids))
    if not ids:
        return {}
    if not ENABLED:
        return build(ids)
    r = _client()
    try:
        pipe = r.pipeline(transaction=False)
        pipe.mget([_key(kind, i) for i in ids])
        pipe.mget([_ver_key(i) for i in ids])
        raws, vers = pipe.execute()
    except redis.RedisError:
        return build(ids)

    out: dict[str, dict] = {}
    ver_of: dict[str, int] = {}
    stale: dict[str, dict] = {}
    todo: list[str] = []
    for i, raw, ver in zip(ids, raws, vers):
        ver_of[i] = int(ver or 0)
        got = json.loads(raw) if raw else None
        if got is not None and got["v"] == ver_of[i]:
            out[i] = got["d"]
        else:
            if got is not None:
                stale[i] = got["d"]
            todo.append(i)

    # 已作废但还没过期的：抢到占位的那一个去重算，其余先用旧的
    if stale:
        try:
            pipe = r.pipeline(transaction=False)
            for i in stale:
                pipe.set(_key(kind, i) + ":fill", 1, nx=True, px=FILL_MS)
            won = pipe.execute()
        except redis.RedisError:
            won = [True] * len(stale)
        for i, ok in zip(stale, won):
            if not ok:
                out[i] = stale[i]
                todo.remove(i)

    if todo:
        built = build(todo)
        # 版本号用的是**算之前**读到的那个：算的过程中又有人提交了改动的话，
        # 版本号已经往前走了，这份结果下一次就会被认作旧的，不会被当成新的留住
        with contextlib.suppress(redis.RedisError):
            pipe = r.pipeline(transaction=False)
            for i in todo:
                if i in built:
                    pipe.set(_key(kind, i),
                             json.dumps({"v": ver_of[i], "d": built[i]},
                                        ensure_ascii=False),
                             px=TTL_MS)
                pipe.delete(_key(kind, i) + ":fill")
            pipe.execute()
        out.update(built)
    return out


def invalidate(activity_ids) -> None:
    """作废这些活动的公共部分缓存（版本号加一）。Redis 用不了就算了——还有 TTL 兜底。"""
    ids = [i for i in dict.fromkeys(activity_ids) if i]
    if not ids:
        return
    try:
        pipe = _client().pipeline(transaction=False)
        for i in ids:
            pipe.incr(_ver_key(i))
            pipe.expire(_ver_key(i), VER_TTL_SECONDS)
        pipe.execute()
    except redis.RedisError:
        logger.warning("Redis 不可用，活动页缓存未能作废，%s 毫秒后自然过期", TTL_MS)


# —— 提交之后按活动作废 ——

_DIRTY = "pubcache_dirty"
# 带 activity_id 列的：直接取。岗位和时段没有，得经场次找过去
_DIRECT = (Shift, Enrollment, Attachment, FormQuestion, AuditLog)


@event.listens_for(SessionLocal, "after_flush")
def _collect(session, _ctx) -> None:
    # after_flush 里 new / dirty / deleted 仍是这次 flush 之前的样子，正好拿来收集
    ids: set = session.info.setdefault(_DIRTY, set())
    shift_ids = set()
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, Activity):
            ids.add(obj.id)
        elif isinstance(obj, _DIRECT):
            ids.add(obj.activity_id)
        elif isinstance(obj, (Position, ShiftPeriod)):
            g = obj.__dict__.get("shift") or session.identity_map.get(
                orm_util.identity_key(Shift, obj.shift_id))
            if g is not None:
                ids.add(g.activity_id)
            elif obj.shift_id:
                shift_ids.add(obj.shift_id)
    if shift_ids:
        # 身份映射里找不到场次的岗位（少见）：在同一事务里补查一次
        ids.update(session.connection().execute(
            select(Shift.activity_id).where(Shift.id.in_(shift_ids))).scalars())


@event.listens_for(SessionLocal, "after_commit")
def _invalidate_after_commit(session) -> None:
    # 回滚时不清：被回滚的那批 id 顶多在下一次提交时多作废一回，而清掉的话
    # 保存点回滚会连带吞掉外层事务真正要作废的那些
    ids = session.info.pop(_DIRTY, None)
    if ids:
        invalidate(ids)
