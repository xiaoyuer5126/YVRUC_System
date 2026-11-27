"""普通岗位的场次名对齐到岗位名

0024 只拆了挂着两个以上岗位的服务场次，原来就只挂一个岗位的场次还留着
「10月24日上午场」这类旧场次名；「我的报名」等处显示场次名，就和岗位名对不上。
这里把 kind=service、mode=pick 且只有一个岗位的场次，名字改成那个岗位的名字。

Revision ID: 0025
Revises: 0024
Create Date: 2026-11-27
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(sa.text(
        "UPDATE shifts s SET name = LEFT(p.name, 128) "
        "FROM positions p "
        "WHERE p.shift_id = s.id AND s.kind = 'service' AND s.mode = 'pick' "
        "AND s.name <> LEFT(p.name, 128) "
        "AND (SELECT count(*) FROM positions q WHERE q.shift_id = s.id) = 1"))


def downgrade() -> None:
    # 旧场次名没有留底，降级不还原
    pass
