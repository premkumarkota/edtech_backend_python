"""teacher_instant_availability

Revision ID: d8e9f0a1b2c3
Revises: c7d2e3f4a5b6
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa


revision = "d8e9f0a1b2c3"
down_revision = "c7d2e3f4a5b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "teacher_profiles",
        sa.Column(
            "is_available_now",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "teacher_profiles",
        sa.Column("available_now_started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "teacher_profiles",
        sa.Column("available_now_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "teacher_profiles",
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("teacher_profiles", "last_seen_at")
    op.drop_column("teacher_profiles", "available_now_expires_at")
    op.drop_column("teacher_profiles", "available_now_started_at")
    op.drop_column("teacher_profiles", "is_available_now")
