"""协作：成员、邀请链接、编辑占用

Revision ID: 0004_collaboration
Revises: 0003_project_versions
Create Date: 2026-08-17
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.config import DB_SCHEMA

revision = "0004_collaboration"
down_revision = "0003_project_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "project_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False, index=True),
        sa.Column("nickname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        # 靠链接加人时两个标签页同时点就会撞上。交给数据库拦，比应用层
        # 「查一遍再插」靠得住——后者中间那一瞬是没有保护的。
        sa.UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        schema=DB_SCHEMA,
    )

    op.create_table(
        "project_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "project_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(f"{DB_SCHEMA}.projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("token", sa.String(length=64), nullable=False, unique=True, index=True),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_nickname", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("used_count", sa.Integer(), nullable=False, server_default="0"),
        schema=DB_SCHEMA,
    )

    # 编辑占用。三列都可空：没人在编辑就是全空。
    op.add_column("projects", sa.Column("editing_by", postgresql.UUID(as_uuid=True), nullable=True),
                  schema=DB_SCHEMA)
    op.add_column("projects",
                  sa.Column("editing_name", sa.String(length=64), nullable=False, server_default=""),
                  schema=DB_SCHEMA)
    op.add_column("projects", sa.Column("editing_at", sa.DateTime(timezone=True), nullable=True),
                  schema=DB_SCHEMA)
    op.alter_column("projects", "editing_name", server_default=None, schema=DB_SCHEMA)


def downgrade() -> None:
    op.drop_column("projects", "editing_at", schema=DB_SCHEMA)
    op.drop_column("projects", "editing_name", schema=DB_SCHEMA)
    op.drop_column("projects", "editing_by", schema=DB_SCHEMA)
    op.drop_table("project_invites", schema=DB_SCHEMA)
    op.drop_table("project_members", schema=DB_SCHEMA)
