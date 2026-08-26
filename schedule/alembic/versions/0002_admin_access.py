"""dev 跨用户访问的审计表，以及项目上的昵称快照

Revision ID: 0002_admin_access
Revises: 0001_projects
Create Date: 2026-08-12
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import DB_SCHEMA

revision = "0002_admin_access"
down_revision = "0001_projects"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 项目归属人的昵称快照。dev 看全站列表时只有 UUID 的话没法用。
    # server_default 让老行也有值，加完就撤掉——默认值只为这一次回填服务，
    # 留着会让「忘了传 owner_nickname」变成静默写空串。
    op.add_column(
        "projects",
        sa.Column("owner_nickname", sa.String(length=64), nullable=False, server_default=""),
        schema=DB_SCHEMA,
    )
    op.alter_column("projects", "owner_nickname", server_default=None, schema=DB_SCHEMA)

    # dev 碰别人项目的流水。**不做删除接口**：一张能被删的审计表不是审计表。
    op.create_table(
        "admin_access_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_nickname", sa.String(length=64), nullable=False, server_default=""),
        # 刻意不建到 projects 的外键：项目删掉之后这条记录必须留着，
        # 而外键要么把它一起 CASCADE 掉，要么让删除项目直接失败。
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_name", sa.String(length=128), nullable=False, server_default=""),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("action", sa.String(length=16), nullable=False),
        schema=DB_SCHEMA,
    )
    op.create_index("ix_admin_access_at", "admin_access_log", ["at"], schema=DB_SCHEMA)
    op.create_index("ix_admin_access_actor", "admin_access_log", ["actor_id"], schema=DB_SCHEMA)
    op.create_index("ix_admin_access_project", "admin_access_log", ["project_id"], schema=DB_SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_admin_access_project", table_name="admin_access_log", schema=DB_SCHEMA)
    op.drop_index("ix_admin_access_actor", table_name="admin_access_log", schema=DB_SCHEMA)
    op.drop_index("ix_admin_access_at", table_name="admin_access_log", schema=DB_SCHEMA)
    op.drop_table("admin_access_log", schema=DB_SCHEMA)
    op.drop_column("projects", "owner_nickname", schema=DB_SCHEMA)
