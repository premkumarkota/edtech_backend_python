"""rich teacher profile and platform config

Revision ID: b3f1a2c4d5e6
Revises: f989131358a9
Create Date: 2026-03-28

Changes:
  1. New table: platform_config (key-value settings, seeds max_teacher_rate_per_minute=100)
  2. teacher_profiles: add proposed_rate_per_minute, education, experience,
                       subjects, languages, achievements
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = 'b3f1a2c4d5e6'
down_revision: Union[str, Sequence[str], None] = 'f989131358a9'
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── 1. platform_config table ──────────────────────────────────────────────
    op.create_table(
        'platform_config',
        sa.Column('id',          sa.Integer(),     nullable=False),
        sa.Column('key',         sa.String(100),   nullable=False),
        sa.Column('value',       sa.String(500),   nullable=False),
        sa.Column('description', sa.Text(),        nullable=True),
        sa.Column('updated_by',  sa.Integer(),     nullable=True),
        sa.Column('updated_at',  sa.DateTime(timezone=True),
                  server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['updated_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('key'),
    )
    op.create_index('ix_platform_config_id', 'platform_config', ['id'])

    # Seed default max rate
    op.execute(
        "INSERT INTO platform_config (key, value, description) "
        "VALUES ('max_teacher_rate_per_minute', '100.00', "
        "'Maximum rate (INR/min) a teacher can propose or be assigned')"
    )

    # ── 2. New columns on teacher_profiles ───────────────────────────────────
    op.add_column('teacher_profiles',
        sa.Column('proposed_rate_per_minute', sa.Numeric(8, 2), nullable=True))

    op.add_column('teacher_profiles',
        sa.Column('education', JSONB, nullable=True,
                  server_default=sa.text("'[]'::jsonb")))

    op.add_column('teacher_profiles',
        sa.Column('experience', JSONB, nullable=True,
                  server_default=sa.text("'[]'::jsonb")))

    op.add_column('teacher_profiles',
        sa.Column('subjects', JSONB, nullable=True,
                  server_default=sa.text("'[]'::jsonb")))

    op.add_column('teacher_profiles',
        sa.Column('languages', JSONB, nullable=True,
                  server_default=sa.text("'[]'::jsonb")))

    op.add_column('teacher_profiles',
        sa.Column('achievements', sa.Text(), nullable=True))


def downgrade() -> None:
    for col in ['proposed_rate_per_minute', 'education', 'experience',
                'subjects', 'languages', 'achievements']:
        op.drop_column('teacher_profiles', col)

    op.drop_index('ix_platform_config_id', table_name='platform_config')
    op.drop_table('platform_config')
