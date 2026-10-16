"""multi heads, council remind_dept_ids, duty_remind_on, shift flexible_time

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-15
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("remind_dept_ids", sa.JSON(), nullable=False, server_default="[]"),
    )
    op.alter_column("users", "remind_dept_ids", server_default=None)
    op.add_column(
        "departments",
        sa.Column("duty_remind_on", sa.Date(), nullable=True),
    )
    op.add_column(
        "shifts",
        sa.Column("flexible_time", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column("shifts", "flexible_time", server_default=None)


def downgrade() -> None:
    op.drop_column("shifts", "flexible_time")
    op.drop_column("departments", "duty_remind_on")
    op.drop_column("users", "remind_dept_ids")
