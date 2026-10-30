"""users.roles 改成 JSONB 并建 GIN 索引

按身份找人（管理员、青协、会长团）要用包含运算符 @>，只有 JSONB 有它。
users 只增不减，按序列化文本 LIKE 匹配的话每次都要扫完整张用户表。

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-30
"""
from __future__ import annotations

from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ALTER COLUMN roles TYPE jsonb USING roles::jsonb")
    op.create_index("ix_users_roles_gin", "users", ["roles"],
                    unique=False, postgresql_using="gin")


def downgrade() -> None:
    op.drop_index("ix_users_roles_gin", table_name="users")
    op.execute("ALTER TABLE users ALTER COLUMN roles TYPE json USING roles::json")
