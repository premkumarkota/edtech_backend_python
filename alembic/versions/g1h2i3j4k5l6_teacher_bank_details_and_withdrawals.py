"""teacher_bank_details_and_withdrawals

Revision ID: g1h2i3j4k5l6
Revises: e1f2a3b4c5d6
Create Date: 2026-05-13

Creates:
  - teacher_bank_details  (one row per teacher, stores bank account + cached Razorpay IDs)
  - withdrawal_requests   (one row per withdrawal request, full lifecycle)

Run in Neon console SQL Editor or via:
  alembic upgrade head
"""
from alembic import op
import sqlalchemy as sa

revision = "g1h2i3j4k5l6"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── teacher_bank_details ──────────────────────────────────────────────────
    op.create_table(
        "teacher_bank_details",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "teacher_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("account_holder_name", sa.String(200), nullable=False),
        sa.Column("account_number",      sa.String(50),  nullable=False),
        sa.Column("ifsc_code",           sa.String(20),  nullable=False),
        sa.Column("bank_name",           sa.String(100), nullable=True),
        sa.Column("upi_id",              sa.String(100), nullable=True),
        # Razorpay cached IDs — avoid re-creating on every payout
        sa.Column("razorpay_contact_id",      sa.String(100), nullable=True),
        sa.Column("razorpay_fund_account_id", sa.String(100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            onupdate=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_teacher_bank_details_teacher_id",
        "teacher_bank_details",
        ["teacher_id"],
        unique=True,
    )

    # ── withdrawal_requests ───────────────────────────────────────────────────
    op.create_table(
        "withdrawal_requests",
        sa.Column("id",         sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "teacher_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        # Status: pending | processing | completed | failed | rejected
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="pending",
            index=True,
        ),
        # Bank details snapshot — JSON string, written once at request time
        sa.Column("bank_snapshot", sa.Text(), nullable=True),
        # Razorpay references
        sa.Column("razorpay_fund_account_id", sa.String(100), nullable=True),
        sa.Column(
            "razorpay_payout_id",
            sa.String(100),
            nullable=True,
            unique=True,   # prevent duplicate payout entries
        ),
        # Admin fields
        sa.Column(
            "processed_by",
            sa.Integer(),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("admin_notes",      sa.Text(), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("failure_reason",   sa.Text(), nullable=True),
        # Timestamps
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("processed_at",  sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at",  sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_withdrawal_requests_teacher_id",
        "withdrawal_requests",
        ["teacher_id"],
    )
    op.create_index(
        "ix_withdrawal_requests_status",
        "withdrawal_requests",
        ["status"],
    )
    op.create_index(
        "ix_withdrawal_requests_requested_at",
        "withdrawal_requests",
        ["requested_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_withdrawal_requests_requested_at",
                  table_name="withdrawal_requests")
    op.drop_index("ix_withdrawal_requests_status",
                  table_name="withdrawal_requests")
    op.drop_index("ix_withdrawal_requests_teacher_id",
                  table_name="withdrawal_requests")
    op.drop_table("withdrawal_requests")

    op.drop_index("ix_teacher_bank_details_teacher_id",
                  table_name="teacher_bank_details")
    op.drop_table("teacher_bank_details")
