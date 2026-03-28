"""teacher_availability_and_fcm_token

Revision ID: c7d2e3f4a5b6
Revises: b3f1a2c4d5e6
Create Date: 2026-03-28

Changes:
  - Add users.fcm_token column
  - Create teacher_availability table
  - Create teacher_availability_overrides table
"""
from alembic import op
import sqlalchemy as sa

revision = 'c7d2e3f4a5b6'
down_revision = 'b3f1a2c4d5e6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── users.fcm_token ───────────────────────────────────────────────────────
    op.add_column(
        'users',
        sa.Column('fcm_token', sa.String(500), nullable=True)
    )

    # ── teacher_availability ──────────────────────────────────────────────────
    op.create_table(
        'teacher_availability',
        sa.Column('id',                 sa.Integer(),   primary_key=True),
        sa.Column('teacher_id',         sa.Integer(),   sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('day_of_week',        sa.Integer(),   nullable=False),
        sa.Column('start_time',         sa.Time(),      nullable=False),
        sa.Column('end_time',           sa.Time(),      nullable=False),
        sa.Column('slot_duration_mins', sa.Integer(),   server_default='60'),
        sa.Column('is_active',          sa.Boolean(),   server_default='true'),
        sa.Column('created_at',         sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        'uq_teacher_day_start',
        'teacher_availability',
        ['teacher_id', 'day_of_week', 'start_time']
    )

    # ── teacher_availability_overrides ────────────────────────────────────────
    op.create_table(
        'teacher_availability_overrides',
        sa.Column('id',         sa.Integer(), primary_key=True),
        sa.Column('teacher_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False, index=True),
        sa.Column('date',       sa.Date(),    nullable=False),
        sa.Column('is_blocked', sa.Boolean(), server_default='true'),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_unique_constraint(
        'uq_teacher_override_date',
        'teacher_availability_overrides',
        ['teacher_id', 'date']
    )


def downgrade() -> None:
    op.drop_table('teacher_availability_overrides')
    op.drop_table('teacher_availability')
    op.drop_column('users', 'fcm_token')
