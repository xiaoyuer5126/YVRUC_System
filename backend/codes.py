"""落库的短码（固定签到码、服装码）：生成时避开库里已有的。

码只有 6 位，库里码一多，随手生成的新码就有可能和别的任务撞上；码列上有
唯一约束，撞上了整笔写入会以 500 失败。这里每生成一个就先查库排除，再在
保存点里插入：两个请求同时抽中同一个码时，后插入的那个会撞唯一约束，
回滚保存点换一个码重来，不影响同一事务里已经写好的东西。
"""
from __future__ import annotations

from typing import Callable, TypeVar

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as OrmSession

T = TypeVar("T")

# 码空间有几十亿，连撞这么多次说明出了别的问题（生成函数坏了、库里的码
# 快占满了），再试下去也没用
CODE_TRIES = 8


def insert_with_unique_code(db: OrmSession, column, gen: Callable[[], str],
                            make: Callable[[str], T]) -> T:
    """用 gen() 生成一个 column 上还没人用的码，make(码) 造出对象并 flush 进库。

    返回已经 flush 的对象，由调用方 commit。CODE_TRIES 次都撞上就报 503。
    """
    for _ in range(CODE_TRIES):
        code = gen()
        if db.query(column).filter(column == code).first() is not None:
            continue
        obj = make(code)
        try:
            with db.begin_nested():
                db.add(obj)
        except IntegrityError:
            continue
        return obj
    raise HTTPException(503, "生成码时连续撞上已有的码，请稍后重试")
