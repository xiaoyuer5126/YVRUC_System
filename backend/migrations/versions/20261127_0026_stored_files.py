"""上传文件按内容去重：文件表 stored_files，attachments.stored_name 加索引

内容相同的上传只存一个文件、几条附件记录共用。stored_files 一个文件一行，
定时清理只删这里登记过、没人用满保留期的文件；stored_name 的索引给数引用用。

已有附件指向的文件都登记进来（按「有人在用」登记）。磁盘上没有附件指向的
文件不登记，清理永远不会碰它们。

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "stored_files",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("orphaned_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_stored_files_orphaned_at", "stored_files", ["orphaned_at"])
    op.create_index("ix_attachments_stored_name", "attachments", ["stored_name"])
    op.get_bind().execute(sa.text(
        "INSERT INTO stored_files (name, size, created_at, orphaned_at) "
        "SELECT stored_name, max(size), min(created_at), NULL FROM attachments "
        "WHERE kind = 'file' AND stored_name <> '' GROUP BY stored_name"))


def downgrade() -> None:
    op.drop_index("ix_attachments_stored_name", table_name="attachments")
    op.drop_index("ix_stored_files_orphaned_at", table_name="stored_files")
    op.drop_table("stored_files")
