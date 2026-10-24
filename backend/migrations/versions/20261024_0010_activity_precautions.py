"""activities: precautions

活动加选填的「注意事项」。已有活动一律是空串。

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities",
                  sa.Column("precautions", sa.Text(), nullable=False, server_default=""))
    op.alter_column("activities", "precautions", server_default=None)


def downgrade() -> None:
    op.drop_column("activities", "precautions")
