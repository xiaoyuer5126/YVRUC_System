"""shifts: 时间灵活的场次时长改按签到签退计

时间灵活的场次一人可以按签到码签好几轮到，「按场次时长固定计」（fixed）会把
每一轮都算成整场，所以这类场次只能按签到签退计（checkin）或不计时长（none）。
已有的「时间灵活 + 固定计」场次改成按签到签退计；表结构不变。

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-17
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE shifts SET hours_mode = 'checkin' "
               "WHERE flexible_time AND hours_mode = 'fixed'")


def downgrade() -> None:
    # 改之前哪些是 fixed 已经无从分辨，降级不动数据
    pass
