"""users: 会长团里标一位会长

会长团审批的待办通知只发会长一个人，其他会长团成员照样能批。标记只是一位，
换届时在角色管理里改。

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column(
        "is_president", sa.Boolean(), nullable=False,
        server_default=sa.false()))
    op.alter_column("users", "is_president", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "is_president")
