"""activities.contact_group_qr: 从取图地址改存二维码的文件编号

原来存的是 /api/staff/contact-qr/<编号>。取出编号存回；对不上一张二维码文件的
（外站地址、data: 内联图、填错的）清空，由发起人在基本信息里重传。编辑态的快照
里也存着一份，一并改，不然「放弃改动」会把旧地址写回去。

Revision ID: 0019
Revises: 0018
Create Date: 2026-11-13
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

PREFIX = "/api/staff/contact-qr/"


def upgrade() -> None:
    conn = op.get_bind()
    qr_ids = {r[0] for r in conn.execute(sa.text(
        "SELECT id FROM attachments WHERE owner_type = 'activity_qr'"))}

    def to_id(value):
        value = value or ""
        if not value.startswith(PREFIX):
            return ""
        att = value[len(PREFIX):]
        return att if att in qr_ids else ""

    activities = sa.table("activities", sa.column("id", sa.String),
                          sa.column("contact_group_qr", sa.Text),
                          sa.column("editing_snapshot", sa.JSON))
    rows = conn.execute(sa.select(activities.c.id, activities.c.contact_group_qr,
                                  activities.c.editing_snapshot)).all()
    for aid, qr, snap in rows:
        values = {}
        if qr:
            values["contact_group_qr"] = to_id(qr)
        fields = (snap or {}).get("activity") or {}
        if fields.get("contact_group_qr"):
            fields["contact_group_qr"] = to_id(fields["contact_group_qr"])
            values["editing_snapshot"] = snap
        if values:
            conn.execute(activities.update()
                         .where(activities.c.id == aid).values(**values))


def downgrade() -> None:
    op.execute(sa.text(
        f"UPDATE activities SET contact_group_qr = '{PREFIX}' || contact_group_qr "
        f"WHERE contact_group_qr <> ''"))
