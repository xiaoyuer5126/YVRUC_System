"""排班项目表

Revision ID: 0001_projects
Revises:
Create Date: 2026-08-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import DB_SCHEMA

revision = "0001_projects"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("ruc_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False, server_default=""),
        # 整份项目状态（core.Project.to_dict()）。里面有姓名、身份证号、联系方式，
        # 所以每一次读写都按 ruc_user_id 过滤，见 store.py。
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema=DB_SCHEMA,
    )
    # 列表页按 owner 查、清理任务按 expires_at 查，这两个都要走索引。
    op.create_index("ix_projects_owner", "projects", ["ruc_user_id"], schema=DB_SCHEMA)
    op.create_index("ix_projects_expires_at", "projects", ["expires_at"], schema=DB_SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_projects_expires_at", table_name="projects", schema=DB_SCHEMA)
    op.drop_index("ix_projects_owner", table_name="projects", schema=DB_SCHEMA)
    op.drop_table("projects", schema=DB_SCHEMA)
