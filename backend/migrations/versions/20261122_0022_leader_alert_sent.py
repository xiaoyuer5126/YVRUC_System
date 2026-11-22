"""shifts.leader_alert_sent: 组长空缺改在名单锁定时提醒

已有场次补成 False：上线后第一轮定时任务会给已经锁定（或不自动锁定、
报名已截止）却还缺组长的场次补发一次提醒。

Revision ID: 0022
Revises: 0021
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("shifts",
                  sa.Column("leader_alert_sent", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.alter_column("shifts", "leader_alert_sent", server_default=None)


def downgrade() -> None:
    op.drop_column("shifts", "leader_alert_sent")
