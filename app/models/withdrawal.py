"""
Teacher Withdrawal Models

TeacherBankDetails  — one row per teacher, holds bank account info.
                      razorpay_contact_id / razorpay_fund_account_id are cached
                      after the first successful payout so we don't recreate them.
                      When the teacher changes bank details, fund_account_id is nulled
                      so a new one is created on the next payout.

WithdrawalRequest   — one row per teacher withdrawal request.
                      Teacher submits → admin reviews → admin processes via Razorpay X
                      → webhook updates status to completed / failed.

Status machine:
  pending   → processing (admin triggers Razorpay payout)
  pending   → rejected   (admin rejects with reason)
  processing → completed  (Razorpay webhook: payout.processed)
  processing → failed     (Razorpay webhook: payout.failed / payout.reversed)
"""
from sqlalchemy import (
    Column, Integer, String, Text, Numeric, Boolean,
    DateTime, ForeignKey, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class TeacherBankDetails(Base):
    """
    One row per teacher (unique on teacher_id).
    Stores bank details + cached Razorpay IDs.
    """
    __tablename__ = "teacher_bank_details"

    id          = Column(Integer, primary_key=True, index=True)
    teacher_id  = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
        index=True,
    )

    # Bank account fields (required for NEFT/IMPS payout)
    account_holder_name = Column(String(200), nullable=False)
    account_number      = Column(String(50),  nullable=False)
    ifsc_code           = Column(String(20),  nullable=False)
    bank_name           = Column(String(100), nullable=True)   # display only

    # Optional: UPI for instant payouts
    upi_id = Column(String(100), nullable=True)

    # Razorpay cached IDs — avoid re-creating on every payout
    razorpay_contact_id     = Column(String(100), nullable=True)
    razorpay_fund_account_id = Column(String(100), nullable=True)
    # Nulled when teacher changes bank details → new fund account created next payout

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    teacher = relationship("User", foreign_keys=[teacher_id])

    def __repr__(self):
        return (
            f"<TeacherBankDetails(teacher={self.teacher_id}, "
            f"account=****{self.account_number[-4:] if self.account_number else ''})>"
        )


class WithdrawalRequest(Base):
    """
    One row per withdrawal request.

    Security invariants (enforced in router):
    - teacher can only request for their own earnings
    - amount <= sum of all pending TeacherEarning.gross_earning for that teacher
    - amount >= MIN_WITHDRAWAL_AMOUNT (₹100)
    - only one active (pending/processing) request allowed per teacher at a time
    - bank_snapshot is written at request time and never mutated
    - only admin can transition to processing/rejected
    - only webhook (verified HMAC) can transition to completed/failed
    """
    __tablename__ = "withdrawal_requests"

    id         = Column(Integer, primary_key=True, index=True)
    teacher_id = Column(
        Integer,
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    amount = Column(Numeric(10, 2), nullable=False)

    # Status: pending | processing | completed | failed | rejected
    status = Column(String(20), nullable=False, default="pending", index=True)

    # Snapshot of bank details at request time — immutable after creation
    bank_snapshot = Column(Text, nullable=True)  # JSON string

    # Razorpay references
    razorpay_fund_account_id = Column(String(100), nullable=True)  # snapshot at request time
    razorpay_payout_id       = Column(String(100), nullable=True, unique=True)

    # Admin fields
    processed_by     = Column(Integer, ForeignKey("users.id"), nullable=True)
    admin_notes      = Column(Text, nullable=True)
    rejection_reason = Column(Text, nullable=True)

    # Failure info from Razorpay
    failure_reason = Column(Text, nullable=True)

    # Timestamps
    requested_at  = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    processed_at  = Column(DateTime(timezone=True), nullable=True)  # admin triggered
    completed_at  = Column(DateTime(timezone=True), nullable=True)  # webhook confirmed

    # Relationships
    teacher   = relationship("User", foreign_keys=[teacher_id])
    processor = relationship("User", foreign_keys=[processed_by])

    def __repr__(self):
        return (
            f"<WithdrawalRequest(id={self.id}, teacher={self.teacher_id}, "
            f"amount={self.amount}, status={self.status})>"
        )
