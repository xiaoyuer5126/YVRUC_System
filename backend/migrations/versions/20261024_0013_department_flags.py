"""departments: duty_enabled / external / vb_liaison

部门表加三个能力开关：启用值班、归口活动算对外活动、志愿北京对接部门。
现有数据里只有「对外联络部」具备这三项，迁移按这个名字把它标上；此后由
部门维护接口开关，代码不再按部门名判断。vb_liaison 全平台至多一个为 True，
用条件唯一索引兜底。

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-24
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("departments",
                  sa.Column("duty_enabled", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.add_column("departments",
                  sa.Column("external", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.add_column("departments",
                  sa.Column("vb_liaison", sa.Boolean(), nullable=False,
                            server_default=sa.false()))
    op.execute("UPDATE departments SET duty_enabled = true, external = true, "
               "vb_liaison = true WHERE name = '对外联络部'")
    op.alter_column("departments", "duty_enabled", server_default=None)
    op.alter_column("departments", "external", server_default=None)
    op.alter_column("departments", "vb_liaison", server_default=None)
    op.create_index("uq_departments_vb_liaison", "departments", ["vb_liaison"],
                    unique=True, postgresql_where=sa.text("vb_liaison"))


def downgrade() -> None:
    op.drop_index("uq_departments_vb_liaison", table_name="departments",
                  postgresql_where=sa.text("vb_liaison"))
    op.drop_column("departments", "vb_liaison")
    op.drop_column("departments", "external")
    op.drop_column("departments", "duty_enabled")
