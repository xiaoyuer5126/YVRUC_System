"""项目历史版本

Revision ID: 0003_project_versions
Revises: 0002_admin_access
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import DB_SCHEMA

revision = "0003_project_versions"
down_revision = "0002_admin_access"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        # ⚠️ CASCADE 不是省事，是必须的：快照里有姓名、身份证号、联系方式。
        # 项目删了（手动删或到期自动删）历史必须跟着一起没，否则「删是真删」
        # 这句话就不成立——留下一份「已删除但还在库里」的名单等于没删。
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        # 空 = 手动微调（按时间窗口合并）；非空 = 重要节点的名字。
        sa.Column("action", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_nickname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("saved_at", sa.DateTime(timezone=True), nullable=False),
        schema=DB_SCHEMA,
    )
    # 每次写都要「查这个项目最新的一版」和「删掉第 50 版之外的」，
    # 两件事走的都是这个复合索引。
    op.create_index(
        "ix_project_versions_project_created",
        "project_versions",
        ["project_id", "created_at"],
        unique=False,
        schema=DB_SCHEMA,
    )


def downgrade() -> None:
    op.drop_index("ix_project_versions_project_created", table_name="project_versions", schema=DB_SCHEMA)
    op.drop_table("project_versions", schema=DB_SCHEMA)
