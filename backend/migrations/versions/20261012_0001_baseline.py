"""基线：内测开始时的完整表结构

Revision ID: 0001
Revises: 
Create Date: 2026-10-12 12:34:20.065072+08:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('announcements',
    sa.Column('id', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=60), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('updated_by', sa.String(length=32), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('attachments',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('owner_type', sa.String(length=12), nullable=False),
    sa.Column('owner_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('title', sa.String(length=160), nullable=False),
    sa.Column('stored_name', sa.String(length=160), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('url', sa.String(length=512), nullable=False),
    sa.Column('uploaded_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_attachments_activity_id'), 'attachments', ['activity_id'], unique=False)
    op.create_index(op.f('ix_attachments_owner_id'), 'attachments', ['owner_id'], unique=False)
    op.create_table('audit_logs',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=True),
    sa.Column('actor_name', sa.String(length=64), nullable=False),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('target_type', sa.String(length=24), nullable=False),
    sa.Column('target_id', sa.String(length=48), nullable=False),
    sa.Column('target_label', sa.String(length=160), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=True),
    sa.Column('summary', sa.String(length=512), nullable=False),
    sa.Column('changes', sa.JSON(), nullable=False),
    sa.Column('visible', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_activity_id'), 'audit_logs', ['activity_id'], unique=False)
    op.create_table('departments',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('note', sa.String(length=128), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('duty_user_id', sa.String(length=32), nullable=True),
    sa.Column('duty_digest_on', sa.Date(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )
    op.create_table('hours_claims',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('service_date', sa.Date(), nullable=False),
    sa.Column('hours', sa.Float(), nullable=False),
    sa.Column('approved_hours', sa.Float(), nullable=True),
    sa.Column('service_desc', sa.String(length=1000), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=False),
    sa.Column('reply', sa.String(length=512), nullable=False),
    sa.Column('handled_by', sa.String(length=32), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('reminded_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_hours_claims_status'), 'hours_claims', ['status'], unique=False)
    op.create_index(op.f('ix_hours_claims_user_id'), 'hours_claims', ['user_id'], unique=False)
    op.create_table('hours_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('hours', sa.Float(), nullable=False),
    sa.Column('source', sa.String(length=12), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=False),
    sa.Column('confirmed_by', sa.String(length=32), nullable=False),
    sa.Column('confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('approved_hours', sa.Float(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('activity_id', 'user_id', name='ux_hours_record_user_activity')
    )
    op.create_index(op.f('ix_hours_records_activity_id'), 'hours_records', ['activity_id'], unique=False)
    op.create_index(op.f('ix_hours_records_user_id'), 'hours_records', ['user_id'], unique=False)
    op.create_table('hours_requests',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('reason', sa.String(length=512), nullable=False),
    sa.Column('requested_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reply', sa.String(length=512), nullable=False),
    sa.Column('handled_by', sa.String(length=32), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('reminded_at', sa.DateTime(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_hours_requests_activity_id'), 'hours_requests', ['activity_id'], unique=False)
    op.create_index(op.f('ix_hours_requests_kind'), 'hours_requests', ['kind'], unique=False)
    op.create_index(op.f('ix_hours_requests_status'), 'hours_requests', ['status'], unique=False)
    op.create_table('integration_configs',
    sa.Column('key', sa.String(length=32), nullable=False),
    sa.Column('value', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('key')
    )
    op.create_table('notifications',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('template_key', sa.String(length=32), nullable=False),
    sa.Column('channel', sa.String(length=16), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('delivered', sa.Boolean(), nullable=False),
    sa.Column('read', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('need_ack', sa.Boolean(), nullable=False),
    sa.Column('acked_at', sa.DateTime(), nullable=True),
    sa.Column('batch_id', sa.String(length=32), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_notifications_batch_id'), 'notifications', ['batch_id'], unique=False)
    op.create_index(op.f('ix_notifications_need_ack'), 'notifications', ['need_ack'], unique=False)
    op.create_index(op.f('ix_notifications_user_id'), 'notifications', ['user_id'], unique=False)
    op.create_table('violation_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('activity_title', sa.String(length=128), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('detail', sa.String(length=512), nullable=False),
    sa.Column('operator_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_violation_records_activity_id'), 'violation_records', ['activity_id'], unique=False)
    op.create_index(op.f('ix_violation_records_user_id'), 'violation_records', ['user_id'], unique=False)
    op.create_table('duty_handovers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('dept_id', sa.String(length=32), nullable=False),
    sa.Column('from_user_id', sa.String(length=32), nullable=True),
    sa.Column('to_user_id', sa.String(length=32), nullable=True),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('note', sa.String(length=256), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['dept_id'], ['departments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_duty_handovers_created_at'), 'duty_handovers', ['created_at'], unique=False)
    op.create_index(op.f('ix_duty_handovers_dept_id'), 'duty_handovers', ['dept_id'], unique=False)
    op.create_table('hours_appeals',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('old_hours', sa.Float(), nullable=False),
    sa.Column('claimed_hours', sa.Float(), nullable=False),
    sa.Column('reason', sa.String(length=512), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reply', sa.String(length=512), nullable=False),
    sa.Column('final_hours', sa.Float(), nullable=True),
    sa.Column('handled_by', sa.String(length=32), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['record_id'], ['hours_records.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_hours_appeals_activity_id'), 'hours_appeals', ['activity_id'], unique=False)
    op.create_index(op.f('ix_hours_appeals_record_id'), 'hours_appeals', ['record_id'], unique=False)
    op.create_index(op.f('ix_hours_appeals_status'), 'hours_appeals', ['status'], unique=False)
    op.create_index(op.f('ix_hours_appeals_user_id'), 'hours_appeals', ['user_id'], unique=False)
    op.create_table('users',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('student_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=64), nullable=False),
    sa.Column('gender', sa.String(length=8), nullable=False),
    sa.Column('college', sa.String(length=64), nullable=False),
    sa.Column('grade', sa.String(length=16), nullable=False),
    sa.Column('campus', sa.String(length=32), nullable=False),
    sa.Column('stage', sa.String(length=16), nullable=False),
    sa.Column('phone', sa.String(length=32), nullable=False),
    sa.Column('email', sa.String(length=128), nullable=False),
    sa.Column('wechat', sa.String(length=64), nullable=False),
    sa.Column('id_card', sa.String(length=255), nullable=False),
    sa.Column('no_mainland_id', sa.Boolean(), nullable=False),
    sa.Column('user_type', sa.String(length=12), nullable=False),
    sa.Column('roles', sa.JSON(), nullable=False),
    sa.Column('department_id', sa.String(length=32), nullable=True),
    sa.Column('qingxie_post', sa.String(length=12), nullable=False),
    sa.Column('supervisor_id', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['department_id'], ['departments.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_department_id'), 'users', ['department_id'], unique=False)
    op.create_index(op.f('ix_users_student_id'), 'users', ['student_id'], unique=True)
    op.create_index(op.f('ix_users_supervisor_id'), 'users', ['supervisor_id'], unique=False)
    op.create_table('activities',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('title', sa.String(length=128), nullable=False),
    sa.Column('recruit_mode', sa.String(length=12), nullable=False),
    sa.Column('intro', sa.Text(), nullable=False),
    sa.Column('location', sa.String(length=128), nullable=False),
    sa.Column('location_point', sa.JSON(), nullable=True),
    sa.Column('contacts', sa.JSON(), nullable=False),
    sa.Column('contact_group_qr', sa.Text(), nullable=False),
    sa.Column('cover_hue', sa.Integer(), nullable=False),
    sa.Column('organizer', sa.String(length=64), nullable=False),
    sa.Column('co_organizer', sa.String(length=64), nullable=False),
    sa.Column('volunteer_requirement', sa.Text(), nullable=False),
    sa.Column('volunteer_benefit', sa.Text(), nullable=False),
    sa.Column('recruit_campuses', sa.JSON(), nullable=False),
    sa.Column('recruit_stages', sa.JSON(), nullable=False),
    sa.Column('recruit_colleges', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('approval_dept_id', sa.String(length=32), nullable=True),
    sa.Column('approval_assignee_id', sa.String(length=32), nullable=True),
    sa.Column('approval_comment', sa.String(length=512), nullable=False),
    sa.Column('auto_publish', sa.Boolean(), nullable=False),
    sa.Column('pinned_at', sa.DateTime(), nullable=True),
    sa.Column('submitted_at', sa.DateTime(), nullable=True),
    sa.Column('approved_at', sa.DateTime(), nullable=True),
    sa.Column('approval_reminded_at', sa.DateTime(), nullable=True),
    sa.Column('reg_start', sa.DateTime(), nullable=True),
    sa.Column('reg_end', sa.DateTime(), nullable=True),
    sa.Column('held_start', sa.DateTime(), nullable=True),
    sa.Column('held_end', sa.DateTime(), nullable=True),
    sa.Column('hours_approved_at', sa.DateTime(), nullable=True),
    sa.Column('appeal_deadline', sa.DateTime(), nullable=True),
    sa.Column('hours_locked_at', sa.DateTime(), nullable=True),
    sa.Column('stamped_reminded_at', sa.DateTime(), nullable=True),
    sa.Column('vb_names', sa.JSON(), nullable=False),
    sa.Column('vb_names_reminded_at', sa.DateTime(), nullable=True),
    sa.Column('vb_reminded_on', sa.Date(), nullable=True),
    sa.Column('created_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('editing_since', sa.DateTime(), nullable=True),
    sa.Column('editing_by', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['approval_dept_id'], ['departments.id'], ),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_activities_approval_assignee_id'), 'activities', ['approval_assignee_id'], unique=False)
    op.create_index(op.f('ix_activities_approval_dept_id'), 'activities', ['approval_dept_id'], unique=False)
    op.create_table('feedbacks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reply', sa.Text(), nullable=False),
    sa.Column('handled_by', sa.String(length=32), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_feedbacks_created_at'), 'feedbacks', ['created_at'], unique=False)
    op.create_index(op.f('ix_feedbacks_status'), 'feedbacks', ['status'], unique=False)
    op.create_index(op.f('ix_feedbacks_user_id'), 'feedbacks', ['user_id'], unique=False)
    op.create_table('sessions',
    sa.Column('token', sa.String(length=64), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('token')
    )
    op.create_index(op.f('ix_sessions_expires_at'), 'sessions', ['expires_at'], unique=False)
    op.create_index(op.f('ix_sessions_user_id'), 'sessions', ['user_id'], unique=False)
    op.create_table('activity_managers',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('added_by', sa.String(length=32), nullable=True),
    sa.Column('duties', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_activity_managers_activity_id'), 'activity_managers', ['activity_id'], unique=False)
    op.create_index(op.f('ix_activity_managers_user_id'), 'activity_managers', ['user_id'], unique=False)
    op.create_table('applications',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('answers', sa.JSON(), nullable=False),
    sa.Column('wanted_positions', sa.JSON(), nullable=False),
    sa.Column('leader_willing', sa.Boolean(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('decided_by', sa.String(length=32), nullable=True),
    sa.Column('decided_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_applications_activity_id'), 'applications', ['activity_id'], unique=False)
    op.create_index(op.f('ix_applications_status'), 'applications', ['status'], unique=False)
    op.create_index(op.f('ix_applications_user_id'), 'applications', ['user_id'], unique=False)
    op.create_index('uq_application', 'applications', ['activity_id', 'user_id'], unique=True)
    op.create_table('approval_events',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('actor_id', sa.String(length=32), nullable=False),
    sa.Column('dept_id', sa.String(length=32), nullable=True),
    sa.Column('to_user_id', sa.String(length=32), nullable=True),
    sa.Column('comment', sa.String(length=512), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_approval_events_activity_id'), 'approval_events', ['activity_id'], unique=False)
    op.create_table('attire_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('taken_at', sa.DateTime(), nullable=False),
    sa.Column('taken_by', sa.String(length=32), nullable=True),
    sa.Column('taken_method', sa.String(length=12), nullable=False),
    sa.Column('returned_at', sa.DateTime(), nullable=True),
    sa.Column('returned_by', sa.String(length=32), nullable=True),
    sa.Column('returned_method', sa.String(length=12), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_attire_records_activity_id'), 'attire_records', ['activity_id'], unique=False)
    op.create_index(op.f('ix_attire_records_user_id'), 'attire_records', ['user_id'], unique=False)
    op.create_index('ux_attire_user_activity', 'attire_records', ['activity_id', 'user_id'], unique=True)
    op.create_table('attire_tasks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('action', sa.String(length=8), nullable=False),
    sa.Column('token', sa.String(length=16), nullable=False),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_attire_tasks_activity_id'), 'attire_tasks', ['activity_id'], unique=False)
    op.create_index(op.f('ix_attire_tasks_token'), 'attire_tasks', ['token'], unique=True)
    op.create_table('form_questions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=8), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('options', sa.JSON(), nullable=False),
    sa.Column('required', sa.Boolean(), nullable=False),
    sa.Column('show_if', sa.JSON(), nullable=False),
    sa.Column('applies_to_positions', sa.JSON(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_form_questions_activity_id'), 'form_questions', ['activity_id'], unique=False)
    op.create_table('hours_segments',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('record_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('seq', sa.Integer(), nullable=False),
    sa.Column('date', sa.DateTime(), nullable=False),
    sa.Column('hours', sa.Float(), nullable=False),
    sa.Column('reward', sa.String(length=8), nullable=False),
    sa.Column('label', sa.String(length=128), nullable=False),
    sa.Column('position_name', sa.String(length=64), nullable=False),
    sa.Column('time_text', sa.String(length=64), nullable=False),
    sa.Column('shift_id', sa.String(length=32), nullable=True),
    sa.Column('position_id', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['record_id'], ['hours_records.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_hours_segments_activity_id'), 'hours_segments', ['activity_id'], unique=False)
    op.create_index(op.f('ix_hours_segments_record_id'), 'hours_segments', ['record_id'], unique=False)
    op.create_index(op.f('ix_hours_segments_user_id'), 'hours_segments', ['user_id'], unique=False)
    op.create_table('shifts',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('start_time', sa.DateTime(), nullable=False),
    sa.Column('end_time', sa.DateTime(), nullable=False),
    sa.Column('location', sa.String(length=128), nullable=False),
    sa.Column('mode', sa.String(length=12), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('applies_to_positions', sa.JSON(), nullable=False),
    sa.Column('hours_mode', sa.String(length=12), nullable=False),
    sa.Column('requirement_tags', sa.JSON(), nullable=False),
    sa.Column('note', sa.String(length=512), nullable=False),
    sa.Column('enroll_paused', sa.Boolean(), nullable=False),
    sa.Column('lock_hours', sa.Integer(), nullable=False),
    sa.Column('list_locked', sa.Boolean(), nullable=False),
    sa.Column('auto_lock_spent', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.Column('reminder_sent_at', sa.DateTime(), nullable=True),
    sa.Column('digest_sent_on', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_shifts_activity_id'), 'shifts', ['activity_id'], unique=False)
    op.create_table('vb_confirms',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('names', sa.JSON(), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('submitted_by', sa.String(length=32), nullable=False),
    sa.Column('submitted_at', sa.DateTime(), nullable=False),
    sa.Column('handled_by', sa.String(length=32), nullable=True),
    sa.Column('handled_at', sa.DateTime(), nullable=True),
    sa.Column('reply', sa.String(length=512), nullable=False),
    sa.Column('reminded_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_vb_confirms_activity_id'), 'vb_confirms', ['activity_id'], unique=False)
    op.create_index(op.f('ix_vb_confirms_status'), 'vb_confirms', ['status'], unique=False)
    op.create_table('positions',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('shift_id', sa.String(length=32), nullable=False),
    sa.Column('name', sa.String(length=128), nullable=False),
    sa.Column('location', sa.String(length=128), nullable=False),
    sa.Column('quota', sa.Integer(), nullable=False),
    sa.Column('waitlist_quota', sa.Integer(), nullable=False),
    sa.Column('requirement_tags', sa.JSON(), nullable=False),
    sa.Column('service_task', sa.Text(), nullable=False),
    sa.Column('position_requirement', sa.Text(), nullable=False),
    sa.Column('training_note', sa.Text(), nullable=False),
    sa.Column('needs_leader', sa.Boolean(), nullable=False),
    sa.Column('leader_task', sa.String(length=256), nullable=False),
    sa.Column('attire', sa.String(length=8), nullable=False),
    sa.Column('attire_note', sa.String(length=256), nullable=False),
    sa.Column('pay_optional', sa.Boolean(), nullable=False),
    sa.Column('pay_note', sa.String(length=256), nullable=False),
    sa.Column('group_name', sa.String(length=64), nullable=False),
    sa.Column('is_placeholder', sa.Boolean(), nullable=False),
    sa.Column('enroll_paused', sa.Boolean(), nullable=False),
    sa.Column('list_locked', sa.Boolean(), nullable=False),
    sa.Column('sort', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_positions_shift_id'), 'positions', ['shift_id'], unique=False)
    op.create_table('checkin_tasks',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('shift_id', sa.String(length=32), nullable=False),
    sa.Column('position_id', sa.String(length=32), nullable=True),
    sa.Column('mode', sa.String(length=12), nullable=False),
    sa.Column('token', sa.String(length=16), nullable=False),
    sa.Column('valid_from', sa.DateTime(), nullable=True),
    sa.Column('valid_to', sa.DateTime(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('created_by', sa.String(length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['position_id'], ['positions.id'], ),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('token')
    )
    op.create_index(op.f('ix_checkin_tasks_activity_id'), 'checkin_tasks', ['activity_id'], unique=False)
    op.create_index(op.f('ix_checkin_tasks_shift_id'), 'checkin_tasks', ['shift_id'], unique=False)
    op.create_table('enrollments',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('shift_id', sa.String(length=32), nullable=False),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('cancel_type', sa.String(length=16), nullable=False),
    sa.Column('allow_reassign', sa.Boolean(), nullable=False),
    sa.Column('leader_willing', sa.Boolean(), nullable=False),
    sa.Column('answers', sa.JSON(), nullable=False),
    sa.Column('is_leader', sa.Boolean(), nullable=False),
    sa.Column('position_id', sa.String(length=32), nullable=True),
    sa.Column('assign_forced', sa.Boolean(), nullable=False),
    sa.Column('assigned_at', sa.DateTime(), nullable=True),
    sa.Column('assigned_by', sa.String(length=32), nullable=True),
    sa.Column('excused', sa.Boolean(), nullable=False),
    sa.Column('excuse_reason', sa.String(length=256), nullable=False),
    sa.Column('excused_at', sa.DateTime(), nullable=True),
    sa.Column('source', sa.String(length=16), nullable=False),
    sa.Column('joined_at', sa.DateTime(), nullable=False),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('promoted_at', sa.DateTime(), nullable=True),
    sa.Column('confirm_deadline', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['activity_id'], ['activities.id'], ),
    sa.ForeignKeyConstraint(['position_id'], ['positions.id'], ),
    sa.ForeignKeyConstraint(['shift_id'], ['shifts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_enrollments_activity_id'), 'enrollments', ['activity_id'], unique=False)
    op.create_index(op.f('ix_enrollments_excused'), 'enrollments', ['excused'], unique=False)
    op.create_index(op.f('ix_enrollments_joined_at'), 'enrollments', ['joined_at'], unique=False)
    op.create_index(op.f('ix_enrollments_position_id'), 'enrollments', ['position_id'], unique=False)
    op.create_index(op.f('ix_enrollments_shift_id'), 'enrollments', ['shift_id'], unique=False)
    op.create_index(op.f('ix_enrollments_status'), 'enrollments', ['status'], unique=False)
    op.create_index(op.f('ix_enrollments_user_id'), 'enrollments', ['user_id'], unique=False)
    op.create_index('uq_enrollment_live', 'enrollments', ['shift_id', 'user_id'], unique=True, postgresql_where=sa.text("status IN ('applied', 'enrolled', 'waitlisted', 'pending_confirm')"))
    op.create_table('checkin_geos',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('task_id', sa.String(length=32), nullable=False),
    sa.Column('lat', sa.Float(), nullable=False),
    sa.Column('lng', sa.Float(), nullable=False),
    sa.Column('radius_m', sa.Integer(), nullable=False),
    sa.Column('accuracy_m', sa.Float(), nullable=False),
    sa.Column('set_by', sa.String(length=32), nullable=False),
    sa.Column('set_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['task_id'], ['checkin_tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_checkin_geos_task_id'), 'checkin_geos', ['task_id'], unique=True)
    op.create_table('checkin_records',
    sa.Column('id', sa.String(length=32), nullable=False),
    sa.Column('task_id', sa.String(length=32), nullable=False),
    sa.Column('activity_id', sa.String(length=32), nullable=False),
    sa.Column('shift_id', sa.String(length=32), nullable=False),
    sa.Column('position_id', sa.String(length=32), nullable=True),
    sa.Column('user_id', sa.String(length=32), nullable=False),
    sa.Column('method', sa.String(length=12), nullable=False),
    sa.Column('by_user', sa.String(length=32), nullable=True),
    sa.Column('checkin_at', sa.DateTime(), nullable=False),
    sa.Column('checkout_at', sa.DateTime(), nullable=True),
    sa.Column('checkout_method', sa.String(length=12), nullable=False),
    sa.Column('checkout_by', sa.String(length=32), nullable=True),
    sa.ForeignKeyConstraint(['task_id'], ['checkin_tasks.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_checkin_records_activity_id'), 'checkin_records', ['activity_id'], unique=False)
    op.create_index(op.f('ix_checkin_records_shift_id'), 'checkin_records', ['shift_id'], unique=False)
    op.create_index(op.f('ix_checkin_records_task_id'), 'checkin_records', ['task_id'], unique=False)
    op.create_index(op.f('ix_checkin_records_user_id'), 'checkin_records', ['user_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_checkin_records_user_id'), table_name='checkin_records')
    op.drop_index(op.f('ix_checkin_records_task_id'), table_name='checkin_records')
    op.drop_index(op.f('ix_checkin_records_shift_id'), table_name='checkin_records')
    op.drop_index(op.f('ix_checkin_records_activity_id'), table_name='checkin_records')
    op.drop_table('checkin_records')
    op.drop_index(op.f('ix_checkin_geos_task_id'), table_name='checkin_geos')
    op.drop_table('checkin_geos')
    op.drop_index('uq_enrollment_live', table_name='enrollments', postgresql_where=sa.text("status IN ('applied', 'enrolled', 'waitlisted', 'pending_confirm')"))
    op.drop_index(op.f('ix_enrollments_user_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_status'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_shift_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_position_id'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_joined_at'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_excused'), table_name='enrollments')
    op.drop_index(op.f('ix_enrollments_activity_id'), table_name='enrollments')
    op.drop_table('enrollments')
    op.drop_index(op.f('ix_checkin_tasks_shift_id'), table_name='checkin_tasks')
    op.drop_index(op.f('ix_checkin_tasks_activity_id'), table_name='checkin_tasks')
    op.drop_table('checkin_tasks')
    op.drop_index(op.f('ix_positions_shift_id'), table_name='positions')
    op.drop_table('positions')
    op.drop_index(op.f('ix_vb_confirms_status'), table_name='vb_confirms')
    op.drop_index(op.f('ix_vb_confirms_activity_id'), table_name='vb_confirms')
    op.drop_table('vb_confirms')
    op.drop_index(op.f('ix_shifts_activity_id'), table_name='shifts')
    op.drop_table('shifts')
    op.drop_index(op.f('ix_hours_segments_user_id'), table_name='hours_segments')
    op.drop_index(op.f('ix_hours_segments_record_id'), table_name='hours_segments')
    op.drop_index(op.f('ix_hours_segments_activity_id'), table_name='hours_segments')
    op.drop_table('hours_segments')
    op.drop_index(op.f('ix_form_questions_activity_id'), table_name='form_questions')
    op.drop_table('form_questions')
    op.drop_index(op.f('ix_attire_tasks_token'), table_name='attire_tasks')
    op.drop_index(op.f('ix_attire_tasks_activity_id'), table_name='attire_tasks')
    op.drop_table('attire_tasks')
    op.drop_index('ux_attire_user_activity', table_name='attire_records')
    op.drop_index(op.f('ix_attire_records_user_id'), table_name='attire_records')
    op.drop_index(op.f('ix_attire_records_activity_id'), table_name='attire_records')
    op.drop_table('attire_records')
    op.drop_index(op.f('ix_approval_events_activity_id'), table_name='approval_events')
    op.drop_table('approval_events')
    op.drop_index('uq_application', table_name='applications')
    op.drop_index(op.f('ix_applications_user_id'), table_name='applications')
    op.drop_index(op.f('ix_applications_status'), table_name='applications')
    op.drop_index(op.f('ix_applications_activity_id'), table_name='applications')
    op.drop_table('applications')
    op.drop_index(op.f('ix_activity_managers_user_id'), table_name='activity_managers')
    op.drop_index(op.f('ix_activity_managers_activity_id'), table_name='activity_managers')
    op.drop_table('activity_managers')
    op.drop_index(op.f('ix_sessions_user_id'), table_name='sessions')
    op.drop_index(op.f('ix_sessions_expires_at'), table_name='sessions')
    op.drop_table('sessions')
    op.drop_index(op.f('ix_feedbacks_user_id'), table_name='feedbacks')
    op.drop_index(op.f('ix_feedbacks_status'), table_name='feedbacks')
    op.drop_index(op.f('ix_feedbacks_created_at'), table_name='feedbacks')
    op.drop_table('feedbacks')
    op.drop_index(op.f('ix_activities_approval_dept_id'), table_name='activities')
    op.drop_index(op.f('ix_activities_approval_assignee_id'), table_name='activities')
    op.drop_table('activities')
    op.drop_index(op.f('ix_users_supervisor_id'), table_name='users')
    op.drop_index(op.f('ix_users_student_id'), table_name='users')
    op.drop_index(op.f('ix_users_department_id'), table_name='users')
    op.drop_table('users')
    op.drop_index(op.f('ix_hours_appeals_user_id'), table_name='hours_appeals')
    op.drop_index(op.f('ix_hours_appeals_status'), table_name='hours_appeals')
    op.drop_index(op.f('ix_hours_appeals_record_id'), table_name='hours_appeals')
    op.drop_index(op.f('ix_hours_appeals_activity_id'), table_name='hours_appeals')
    op.drop_table('hours_appeals')
    op.drop_index(op.f('ix_duty_handovers_dept_id'), table_name='duty_handovers')
    op.drop_index(op.f('ix_duty_handovers_created_at'), table_name='duty_handovers')
    op.drop_table('duty_handovers')
    op.drop_index(op.f('ix_violation_records_user_id'), table_name='violation_records')
    op.drop_index(op.f('ix_violation_records_activity_id'), table_name='violation_records')
    op.drop_table('violation_records')
    op.drop_index(op.f('ix_notifications_user_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_need_ack'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_batch_id'), table_name='notifications')
    op.drop_table('notifications')
    op.drop_table('integration_configs')
    op.drop_index(op.f('ix_hours_requests_status'), table_name='hours_requests')
    op.drop_index(op.f('ix_hours_requests_kind'), table_name='hours_requests')
    op.drop_index(op.f('ix_hours_requests_activity_id'), table_name='hours_requests')
    op.drop_table('hours_requests')
    op.drop_index(op.f('ix_hours_records_user_id'), table_name='hours_records')
    op.drop_index(op.f('ix_hours_records_activity_id'), table_name='hours_records')
    op.drop_table('hours_records')
    op.drop_index(op.f('ix_hours_claims_user_id'), table_name='hours_claims')
    op.drop_index(op.f('ix_hours_claims_status'), table_name='hours_claims')
    op.drop_table('hours_claims')
    op.drop_table('departments')
    op.drop_index(op.f('ix_audit_logs_activity_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
    op.drop_index(op.f('ix_attachments_owner_id'), table_name='attachments')
    op.drop_index(op.f('ix_attachments_activity_id'), table_name='attachments')
    op.drop_table('attachments')
    op.drop_table('announcements')
