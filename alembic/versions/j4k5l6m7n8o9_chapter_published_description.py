"""chapters.published_description: live copy separate from the admin draft

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-10-12
"""
from alembic import op
import sqlalchemy as sa


revision = "j4k5l6m7n8o9"
down_revision = "i3j4k5l6m7n8"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("ALTER TABLE chapters ADD COLUMN IF NOT EXISTS published_description TEXT")
    op.execute(
        "UPDATE chapters SET published_description = description "
        "WHERE content_published = true AND published_description IS NULL"
    )


def downgrade():
    op.drop_column("chapters", "published_description")
