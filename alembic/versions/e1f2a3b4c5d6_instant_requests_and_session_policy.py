"""instant_requests_and_session_policy

Revision ID: e1f2a3b4c5d6
Revises: d8e9f0a1b2c3
Create Date: 2026-04-25
"""
from alembic import op
import sqlalchemy as sa


revision = "e1f2a3b4c5d6"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "video_call_sessions",
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "video_call_sessions",
        sa.Column(
            "is_late_cancel",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
    )
    op.add_column(
        "video_call_sessions",
        sa.Column(
            "penalty_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "video_call_sessions",
        sa.Column(
            "refund_minutes",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "video_call_sessions",
        sa.Column("no_show_marked_by", sa.Integer(), nullable=True),
    )
    op.add_column(
        "video_call_sessions",
        sa.Column("no_show_reason", sa.Text(), nullable=True),
    )
    op.create_foreign_key(
        "fk_video_call_sessions_no_show_marked_by_users",
        "video_call_sessions",
        "users",
        ["no_show_marked_by"],
        ["id"],
    )

    request_status = sa.Enum(
        "requested",
        "accepted",
        "declined",
        "expired",
        "cancelled",
        name="instant_session_request_status",
    )
    request_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "instant_session_requests",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("student_id", sa.Integer(), nullable=False),
        sa.Column("teacher_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.Integer(), nullable=True),
        sa.Column("status", request_status, nullable=False, server_default="requested"),
        sa.Column("duration_mins", sa.Integer(), nullable=False),
        sa.Column("subject_context", sa.Text(), nullable=True),
        sa.Column("decline_reason", sa.Text(), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("requested_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), onupdate=sa.func.now()),
        sa.ForeignKeyConstraint(["student_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["teacher_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["subscription_id"], ["student_subscriptions.id"]),
        sa.ForeignKeyConstraint(["session_id"], ["video_call_sessions.id"]),
    )
    op.create_index("ix_instant_session_requests_student_id", "instant_session_requests", ["student_id"])
    op.create_index("ix_instant_session_requests_teacher_id", "instant_session_requests", ["teacher_id"])
    op.create_index("ix_instant_session_requests_subscription_id", "instant_session_requests", ["subscription_id"])
    op.create_index("ix_instant_session_requests_session_id", "instant_session_requests", ["session_id"])
    op.create_index("ix_instant_session_requests_status", "instant_session_requests", ["status"])
    op.create_index("ix_instant_session_requests_expires_at", "instant_session_requests", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_instant_session_requests_expires_at", table_name="instant_session_requests")
    op.drop_index("ix_instant_session_requests_status", table_name="instant_session_requests")
    op.drop_index("ix_instant_session_requests_session_id", table_name="instant_session_requests")
    op.drop_index("ix_instant_session_requests_subscription_id", table_name="instant_session_requests")
    op.drop_index("ix_instant_session_requests_teacher_id", table_name="instant_session_requests")
    op.drop_index("ix_instant_session_requests_student_id", table_name="instant_session_requests")
    op.drop_table("instant_session_requests")
    sa.Enum(name="instant_session_request_status").drop(op.get_bind(), checkfirst=True)

    op.drop_constraint(
        "fk_video_call_sessions_no_show_marked_by_users",
        "video_call_sessions",
        type_="foreignkey",
    )
    op.drop_column("video_call_sessions", "no_show_reason")
    op.drop_column("video_call_sessions", "no_show_marked_by")
    op.drop_column("video_call_sessions", "refund_minutes")
    op.drop_column("video_call_sessions", "penalty_minutes")
    op.drop_column("video_call_sessions", "is_late_cancel")
    op.drop_column("video_call_sessions", "cancelled_at")
