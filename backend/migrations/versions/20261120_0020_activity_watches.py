"""activity_watches: 志愿者订阅活动上新提醒；activities.new_alert_at

一人一行，存勾选的每周时段和关注的主单位。activities.new_alert_at 记这个活动
发过上新提醒的时刻，已经上过广场的老活动留空：它们不会再经过「上广场」那一步，
留空也不会补发。

Revision ID: 0020
Revises: 0019
Create Date: 2026-11-20
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "activity_watches",
        sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id"),
                  primary_key=True),
        sa.Column("slots", sa.JSON(), nullable=False),
        sa.Column("organizers", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.add_column("activities",
                  sa.Column("new_alert_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "new_alert_at")
    op.drop_table("activity_watches")
