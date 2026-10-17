"""form_questions: applies_to_campuses, applies_to_stages

问卷题目加「适用校区」「适用学段」。已有的题一律是空列表（不限），
照旧对所有人显示。

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("applies_to_campuses", "applies_to_stages"):
        op.add_column(
            "form_questions",
            sa.Column(name, sa.JSON(), nullable=False, server_default="[]"),
        )
        op.alter_column("form_questions", name, server_default=None)


def downgrade() -> None:
    op.drop_column("form_questions", "applies_to_stages")
    op.drop_column("form_questions", "applies_to_campuses")
