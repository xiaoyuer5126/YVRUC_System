"""查询用的索引：活动、签到、留痕、通知、用户、场次时段

这些列都在列表类请求的过滤、排序条件里，之前没有索引：活动按状态筛再按
创建时间排、签到按时间数人数、审计日志按时间翻页、消息按人和时间翻页、
名册按类型和学院筛、时段按时间做范围比较。前四张表随办学历史一直长，
没有索引时每翻一页都是整表扫描。

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-30
"""
from __future__ import annotations

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None

INDEXES = [
    ("ix_activities_status_created", "activities", ["status", "created_at"]),
    ("ix_activities_organizer", "activities", ["organizer"]),
    ("ix_checkin_records_checkin_at", "checkin_records", ["checkin_at"]),
    ("ix_audit_logs_created_at", "audit_logs", ["created_at"]),
    ("ix_notifications_user_created", "notifications", ["user_id", "created_at"]),
    ("ix_users_user_type", "users", ["user_type"]),
    ("ix_users_college", "users", ["college"]),
    ("ix_users_qingxie_post", "users", ["qingxie_post"]),
    ("ix_shift_periods_start_time", "shift_periods", ["start_time"]),
    ("ix_shift_periods_end_time", "shift_periods", ["end_time"]),
]


def upgrade() -> None:
    for name, table, columns in INDEXES:
        op.create_index(name, table, columns, unique=False)


def downgrade() -> None:
    for name, table, _columns in reversed(INDEXES):
        op.drop_index(name, table_name=table)
