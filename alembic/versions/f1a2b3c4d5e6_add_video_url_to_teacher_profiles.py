"""add video_url to teacher_profiles

Revision ID: f1a2b3c4d5e6
Revises: e1f2a3b4c5d6
Create Date: 2026-04-26

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'f1a2b3c4d5e6'
down_revision = 'e1f2a3b4c5d6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        'teacher_profiles',
        sa.Column('video_url', sa.String(500), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('teacher_profiles', 'video_url')
