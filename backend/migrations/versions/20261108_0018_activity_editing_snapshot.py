"""activities: 编辑态的结构快照

管理者点「调整岗位」进入编辑态时，把当时的场次、岗位与活动基本信息存一份，
点「放弃改动」时照它还原。已有的编辑态没有快照，还原不了，正好以这次迁移
为界：升级时还在编辑态的那些活动，管理者重新进一次编辑态就有了。

Revision ID: 0018
Revises: 0017
Create Date: 2026-11-08
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities",
                  sa.Column("editing_snapshot", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "editing_snapshot")
