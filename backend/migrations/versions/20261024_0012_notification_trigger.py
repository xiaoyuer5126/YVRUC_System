"""notifications: triggered_by, created_at index

管理端「通知记录」要显示每条通知是谁发出的，并常用时间范围筛选。
旧通知没有触发人信息，不回填，保持 NULL。

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("notifications",
                  sa.Column("triggered_by", sa.String(32), nullable=True))
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_column("notifications", "triggered_by")
