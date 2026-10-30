"""activities: 服务场次的跨度（最早开始 / 最晚结束 / 最晚开始）

广场要按「展示状态」筛和排，而展示状态是按场次时间算出来的。把这三个时间
存到活动上，展示状态就能写进 SQL 的 WHERE / ORDER BY，分页交给数据库；
不存的话每个请求都要把全部公开活动取回内存，算完再切一页。

只算 kind="service" 的场次，口径同 Activity.time_span：培训与彩排常常排在
正式服务前一周，算进来的话广场上的活动时间会从彩排那天开始。

迁移里按现有场次把每一行都算好，不留给应用补：老活动不重新保存就永远
是空的，广场上会显示「待定」。

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-30
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for column in ("shift_first_start", "shift_last_end", "shift_last_start"):
        op.add_column("activities",
                      sa.Column(column, sa.DateTime(), nullable=True))
        op.create_index(f"ix_activities_{column}", "activities", [column],
                        unique=False)
    op.execute("""
        UPDATE activities a SET
          shift_first_start = s.first_start,
          shift_last_end    = s.last_end,
          shift_last_start  = s.last_start
        FROM (
          SELECT sh.activity_id,
                 min(sp.start_time) AS first_start,
                 max(sp.end_time)   AS last_end,
                 max(sp.start_time) AS last_start
          FROM shifts sh
          JOIN shift_periods sp ON sp.shift_id = sh.id
          WHERE sh.kind = 'service'
          GROUP BY sh.activity_id
        ) s
        WHERE a.id = s.activity_id
    """)


def downgrade() -> None:
    for column in ("shift_first_start", "shift_last_end", "shift_last_start"):
        op.drop_index(f"ix_activities_{column}", table_name="activities")
        op.drop_column("activities", column)
