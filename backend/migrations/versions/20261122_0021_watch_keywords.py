"""activity_watches.keywords: 上新提醒按活动名关键词订阅

已有的订阅行补成空列表（没订关键词）。

Revision ID: 0021
Revises: 0020
Create Date: 2026-11-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 先带默认值把已有行补成 []，再去掉默认值，和模型一致（由 ORM 填 default）
    op.add_column("activity_watches",
                  sa.Column("keywords", sa.JSON(), nullable=False,
                            server_default=sa.text("'[]'")))
    op.alter_column("activity_watches", "keywords", server_default=None)


def downgrade() -> None:
    op.drop_column("activity_watches", "keywords")
