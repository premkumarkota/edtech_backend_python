"""student_profiles: onboarding consent timestamps (terms + under-18 guardian)

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-10-19
"""
from alembic import op


revision = "l6m7n8o9p0q1"
down_revision = "k5l6m7n8o9p0"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ")
    op.execute("ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS guardian_consent_at TIMESTAMPTZ")
    op.execute("ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS consent_version VARCHAR(20)")


def downgrade():
    op.drop_column("student_profiles", "consent_version")
    op.drop_column("student_profiles", "guardian_consent_at")
    op.drop_column("student_profiles", "terms_accepted_at")
