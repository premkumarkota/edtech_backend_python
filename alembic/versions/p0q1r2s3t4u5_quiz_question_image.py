"""quiz_questions.image_url: optional diagram shown with a question

Revision ID: p0q1r2s3t4u5
Revises: o9p0q1r2s3t4
Create Date: 2026-11-18
"""
from alembic import op


revision = "p0q1r2s3t4u5"
down_revision = "o9p0q1r2s3t4"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS image_url VARCHAR(1000)")


def downgrade():
    op.drop_column("quiz_questions", "image_url")
