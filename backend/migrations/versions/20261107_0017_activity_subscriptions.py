"""activity_subscriptions: 志愿者订阅报名开始提醒

一人一个活动一条（唯一索引兜底）。reminded_for 记按哪个报名开始时间提醒过：
开始时间改了，按新时间再提醒一次。

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-07
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_subscriptions",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("activity_id", sa.String(32), sa.ForeignKey("activities.id"),
                  nullable=False),
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("reminded_for", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_activity_subscriptions_activity_id",
                    "activity_subscriptions", ["activity_id"])
    op.create_index("ix_activity_subscriptions_user_id",
                    "activity_subscriptions", ["user_id"])
    op.create_index("ux_subscription_activity_user", "activity_subscriptions",
                    ["activity_id", "user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_subscription_activity_user",
                  table_name="activity_subscriptions")
    op.drop_index("ix_activity_subscriptions_user_id",
                  table_name="activity_subscriptions")
    op.drop_index("ix_activity_subscriptions_activity_id",
                  table_name="activity_subscriptions")
    op.drop_table("activity_subscriptions")
