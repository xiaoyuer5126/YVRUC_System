"""信用分服务。

规则（《设计方案》阶段二.3 + 《志愿者服务规范》）：
  · 活动开始前 24 小时内退选 → 扣 5 分，名额不释放给候补；
  · 放弃候补（转正确认超时/主动放弃）→ 记一次，连续 3 次暂停本学期候补；
  · 信用分低于 60 分 → 冻结报名权限 30 天（自动触发）。
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session as OrmSession

from . import config
from .integrations import wecom
from .models import CreditLog, User, now, uid


def apply_delta(db: OrmSession, user: User, delta: int, reason: str, *,
                activity_id: str | None = None,
                operator_id: str | None = None) -> int:
    """加减信用分；低于阈值自动冻结并通知。返回变动后分数。"""
    user.credit_score = max(0, min(200, user.credit_score + delta))
    after = user.credit_score
    db.add(CreditLog(id=uid("crd"), user_id=user.id, delta=delta, after_score=after,
                     reason=reason, activity_id=activity_id, operator_id=operator_id))
    extra_note = ""
    if after < config.CREDIT_FREEZE_BELOW and not (
            user.credit_frozen_until and user.credit_frozen_until > now()):
        until = now() + timedelta(days=config.CREDIT_FREEZE_DAYS)
        user.credit_frozen_until = until
        extra_note = f"\n⚠️ 信用分已低于{config.CREDIT_FREEZE_BELOW}分，" \
                     f"报名权限冻结至 {until:%Y-%m-%d}。"
    wecom.send_user(db, user, "credit_penalty", delta=delta, score=after,
                    reason=reason + ("（系统自动）" if operator_id is None else ""),
                    extra=extra_note)
    return after


def register_abandon(db: OrmSession, user: User) -> None:
    """记一次“放弃候补”。连续 3 次 → 本学期候补资格暂停。"""
    user.abandon_count += 1
    if user.abandon_count >= config.MAX_ABANDON_TIMES:
        user.waitlist_banned_until = now() + timedelta(days=config.ABANDON_BAN_DAYS)


def reset_abandon_if_confirmed(user: User) -> None:
    """成功确认候补转正后，连续计数清零。"""
    user.abandon_count = 0


def can_join_waitlist(user: User) -> tuple[bool, str]:
    if user.waitlist_banned_until and user.waitlist_banned_until > now():
        return False, f"因连续放弃候补，您的候补资格暂停至 " \
                      f"{user.waitlist_banned_until:%Y-%m-%d}"
    return True, ""
