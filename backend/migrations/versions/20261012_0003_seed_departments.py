"""铺上校青协的 12 个部门：活动审批按部门归口，库里没有部门就提交不了审批

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-12 15:40:00.000000+08:00
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op


revision = '0003'
down_revision = '0002'
branch_labels = None
depends_on = None

# 迁移里写死一份，不 import 业务代码：业务代码以后怎么改，这一步铺进去的都是这 12 个。
# 对外联络部置顶，二级团体归它。
DEPARTMENTS = [
    ("对外联络部", "【二级志愿团体请选择此部门审批】"),
    ("办公室", ""), ("新媒体技术部", ""), ("组织部", ""),
    ("支教项目部", ""), ("红十字项目部", ""), ("环保项目部", ""),
    ("青春健康项目部", ""), ("社区服务部", ""), ("特殊关爱项目部", ""),
    ("校园服务部", ""), ("首都服务部", ""),
]

departments = sa.table(
    'departments',
    sa.column('id', sa.String),
    sa.column('name', sa.String),
    sa.column('note', sa.String),
    sa.column('sort', sa.Integer),
)


def upgrade() -> None:
    # 库里已经有部门（有人在页面上建过）就一个都不动，免得和手工维护的那份混在一起
    bind = op.get_bind()
    if bind.execute(sa.select(sa.func.count()).select_from(departments)).scalar():
        return
    op.bulk_insert(departments, [
        {"id": f"dep_{uuid.uuid4().hex[:12]}", "name": name, "note": note, "sort": i}
        for i, (name, note) in enumerate(DEPARTMENTS)
    ])


def downgrade() -> None:
    # 部门铺进去以后就挂上了活动、部长和值班，回退版本不删它们
    pass
