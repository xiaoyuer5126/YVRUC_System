"""shifts: 场次拆出多个服务时段

一个场次可以含多个不连续的时段。原有的每个场次按原来的起止时间生成一段，起止两列从 shifts 表搬到 shift_periods；签到任务、签到记录、
时长段的 period_id 回填成所属场次的这一段。

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-21
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "shift_periods",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("shift_id", sa.String(32), sa.ForeignKey("shifts.id"),
                  nullable=False),
        sa.Column("start_time", sa.DateTime(), nullable=False),
        sa.Column("end_time", sa.DateTime(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False),
        sa.Column("reminder_sent_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_shift_periods_shift_id", "shift_periods", ["shift_id"])
    op.execute(
        "INSERT INTO shift_periods (id, shift_id, start_time, end_time, sort) "
        "SELECT 'p_' || substr(md5(random()::text), 1, 12), id, start_time, "
        "end_time, 0 FROM shifts")

    op.add_column("checkin_tasks", sa.Column(
        "period_id", sa.String(32), sa.ForeignKey("shift_periods.id"),
        nullable=True))
    op.add_column("checkin_records", sa.Column(
        "period_id", sa.String(32), nullable=True))
    op.add_column("hours_segments", sa.Column(
        "period_id", sa.String(32), nullable=True))
    op.create_index("ix_checkin_tasks_period_id", "checkin_tasks", ["period_id"])
    op.create_index("ix_checkin_records_period_id", "checkin_records",
                    ["period_id"])
    op.execute(
        "UPDATE checkin_tasks t SET period_id = (SELECT p.id FROM shift_periods p "
        "WHERE p.shift_id = t.shift_id ORDER BY p.start_time LIMIT 1)")
    op.execute(
        "UPDATE checkin_records r SET period_id = (SELECT p.id FROM shift_periods p "
        "WHERE p.shift_id = r.shift_id ORDER BY p.start_time LIMIT 1)")
    op.execute(
        "UPDATE hours_segments s SET period_id = (SELECT p.id FROM shift_periods p "
        "WHERE p.shift_id = s.shift_id ORDER BY p.start_time LIMIT 1) "
        "WHERE s.kind = 'shift'")

    # 签到提醒改成按时段记：一个场次可能有好几段，一段提醒一次
    op.execute("UPDATE shift_periods p SET reminder_sent_at = s.reminder_sent_at "
               "FROM shifts s WHERE s.id = p.shift_id")
    op.drop_column("shifts", "reminder_sent_at")
    op.drop_column("shifts", "start_time")
    op.drop_column("shifts", "end_time")


def downgrade() -> None:
    op.add_column("shifts", sa.Column("start_time", sa.DateTime(), nullable=True))
    op.add_column("shifts", sa.Column("end_time", sa.DateTime(), nullable=True))
    op.add_column("shifts", sa.Column("reminder_sent_at", sa.DateTime(),
                                      nullable=True))
    op.execute(
        "UPDATE shifts s SET start_time = (SELECT min(p.start_time) "
        "FROM shift_periods p WHERE p.shift_id = s.id), "
        "end_time = (SELECT max(p.end_time) FROM shift_periods p "
        "WHERE p.shift_id = s.id)")
    op.alter_column("shifts", "start_time", nullable=False)
    op.alter_column("shifts", "end_time", nullable=False)
    op.execute("UPDATE shifts s SET reminder_sent_at = (SELECT max("
               "p.reminder_sent_at) FROM shift_periods p WHERE p.shift_id = s.id)")

    op.drop_index("ix_checkin_records_period_id", table_name="checkin_records")
    op.drop_index("ix_checkin_tasks_period_id", table_name="checkin_tasks")
    op.drop_column("hours_segments", "period_id")
    op.drop_column("checkin_records", "period_id")
    op.drop_column("checkin_tasks", "period_id")
    op.drop_index("ix_shift_periods_shift_id", table_name="shift_periods")
    op.drop_table("shift_periods")
