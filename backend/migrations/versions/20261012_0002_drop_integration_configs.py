"""删掉 integration_configs：学校对接的开关和参数改由 .env 配置

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-12 14:30:00.000000+08:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table('integration_configs')


def downgrade() -> None:
    op.create_table('integration_configs',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
