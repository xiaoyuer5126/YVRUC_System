"""feedbacks 收志愿反馈：类别、意见类型、关联活动、匿名、联系方式、催办时间

已有的反馈都是平台问题，category 补成 platform，其余列取空值。

Revision ID: 0027
Revises: 0026
Create Date: 2026-11-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("feedbacks", sa.Column("category", sa.String(length=12),
                                         nullable=False, server_default="platform"))
    op.add_column("feedbacks", sa.Column("kind", sa.String(length=16),
                                         nullable=False, server_default=""))
    op.add_column("feedbacks", sa.Column("activity_id", sa.String(length=32),
                                         nullable=True))
    op.add_column("feedbacks", sa.Column("anonymous", sa.Boolean(),
                                         nullable=False, server_default=sa.false()))
    op.add_column("feedbacks", sa.Column("contact", sa.String(length=64),
                                         nullable=False, server_default=""))
    op.add_column("feedbacks", sa.Column("reminded_at", sa.DateTime(), nullable=True))
    for col in ("category", "kind", "anonymous", "contact"):
        op.alter_column("feedbacks", col, server_default=None)
    op.create_foreign_key("feedbacks_activity_id_fkey", "feedbacks", "activities",
                          ["activity_id"], ["id"], ondelete="SET NULL")
    op.create_index(op.f("ix_feedbacks_category"), "feedbacks", ["category"])
    op.create_index(op.f("ix_feedbacks_activity_id"), "feedbacks", ["activity_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_feedbacks_activity_id"), table_name="feedbacks")
    op.drop_index(op.f("ix_feedbacks_category"), table_name="feedbacks")
    op.drop_constraint("feedbacks_activity_id_fkey", "feedbacks", type_="foreignkey")
    for col in ("reminded_at", "contact", "anonymous", "activity_id", "kind", "category"):
        op.drop_column("feedbacks", col)
