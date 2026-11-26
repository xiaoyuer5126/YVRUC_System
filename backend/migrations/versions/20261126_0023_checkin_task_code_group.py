"""checkin_tasks.code_group: 一张签到码可以管好几个岗位

多选岗位建出来的几行共用一个 code_group，对外是一张码。已有的签到任务各自
独享一张码，留空即可。扫码解析按这一列成组查，所以给它建索引。

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("checkin_tasks",
                  sa.Column("code_group", sa.String(length=32), nullable=True))
    op.create_index("ix_checkin_tasks_code_group", "checkin_tasks",
                    ["code_group"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_checkin_tasks_code_group", table_name="checkin_tasks")
    op.drop_column("checkin_tasks", "code_group")
