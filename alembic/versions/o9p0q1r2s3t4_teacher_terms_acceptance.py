"""teacher_profiles: Teacher Terms acceptance timestamp + legal version

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-10-29
"""
from alembic import op


revision = "o9p0q1r2s3t4"
down_revision = "n8o9p0q1r2s3"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE teacher_profiles ADD COLUMN IF NOT EXISTS teacher_terms_accepted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE teacher_profiles ADD COLUMN IF NOT EXISTS legal_version VARCHAR(20)")


def downgrade():
    op.drop_column("teacher_profiles", "legal_version")
    op.drop_column("teacher_profiles", "teacher_terms_accepted_at")
