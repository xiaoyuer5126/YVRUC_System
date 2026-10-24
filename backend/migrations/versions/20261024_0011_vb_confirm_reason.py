"""vb_confirms: reason

活动完成后再次推送志愿北京名称时，管理者写的原因。已有记录一律是空串。

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("vb_confirms",
                  sa.Column("reason", sa.String(512), nullable=False, server_default=""))
    op.alter_column("vb_confirms", "reason", server_default=None)


def downgrade() -> None:
    op.drop_column("vb_confirms", "reason")
