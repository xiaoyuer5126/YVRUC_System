"""attachments.stored_name 加索引

内容相同的上传只存一个文件、几条记录共用，删记录时要数「还有没有别的记录
指着这个文件」，按 stored_name 查。

Revision ID: 0026
Revises: 0025
Create Date: 2026-11-27
"""
from __future__ import annotations

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_attachments_stored_name", "attachments", ["stored_name"])


def downgrade() -> None:
    op.drop_index("ix_attachments_stored_name", table_name="attachments")
