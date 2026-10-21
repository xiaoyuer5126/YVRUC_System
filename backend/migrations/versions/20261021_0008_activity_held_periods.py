"""activities: 补记录活动的活动时间改成多段

补记录活动没有场次，原来只有一对 held_start/held_end。线下的活动可能分几段
办，一对时间表示不了。原来的那一对搬成 held_periods 里的一段，两列删掉。

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-21
"""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column(
        "held_periods", sa.JSON(), nullable=False, server_default=sa.text("'[]'")))
    activities = sa.table(
        "activities",
        sa.column("id", sa.String),
        sa.column("held_start", sa.DateTime),
        sa.column("held_end", sa.DateTime),
        sa.column("held_periods", sa.JSON),
    )
    conn = op.get_bind()
    rows = conn.execute(
        sa.select(activities.c.id, activities.c.held_start, activities.c.held_end)
        .where(activities.c.held_start.is_not(None))
        .where(activities.c.held_end.is_not(None))).fetchall()
    for aid, start, end in rows:
        conn.execute(activities.update()
                     .where(activities.c.id == aid)
                     .values(held_periods=[{
                         "start_time": start.isoformat(timespec="minutes"),
                         "end_time": end.isoformat(timespec="minutes")}]))
    op.alter_column("activities", "held_periods", server_default=None)
    op.drop_column("activities", "held_start")
    op.drop_column("activities", "held_end")


def downgrade() -> None:
    op.add_column("activities", sa.Column(
        "held_start", sa.DateTime(), nullable=True))
    op.add_column("activities", sa.Column(
        "held_end", sa.DateTime(), nullable=True))
    activities = sa.table(
        "activities",
        sa.column("id", sa.String),
        sa.column("held_start", sa.DateTime),
        sa.column("held_end", sa.DateTime),
        sa.column("held_periods", sa.JSON),
    )
    conn = op.get_bind()
    rows = conn.execute(sa.select(activities.c.id, activities.c.held_periods)
                        .where(activities.c.held_periods.is_not(None))).fetchall()
    for aid, periods in rows:
        if not periods:
            continue
        starts = [datetime.fromisoformat(p["start_time"]) for p in periods]
        ends = [datetime.fromisoformat(p["end_time"]) for p in periods]
        conn.execute(activities.update()
                     .where(activities.c.id == aid)
                     .values(held_start=min(starts), held_end=max(ends)))
    op.drop_column("activities", "held_periods")
