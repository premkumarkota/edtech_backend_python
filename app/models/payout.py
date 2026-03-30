from sqlalchemy import Column, Integer, String, Text, Numeric, DateTime, Date, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class TeacherRate(Base):
    """
    Per-teacher rate set by admin (INR per hour).
    One row per teacher — admin updates in-place.
    """
    __tablename__ = "teacher_rates"

    id             = Column(Integer, primary_key=True, index=True)
    teacher_id     = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                            unique=True, nullable=False, index=True)
    rate_per_hour  = Column(Numeric(8, 2), nullable=False, default=0.00)
    set_by_admin_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at      = Column(DateTime(timezone=True), server_default=func.now())
    updated_at      = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    teacher = relationship("User", foreign_keys=[teacher_id])
    admin   = relationship("User", foreign_keys=[set_by_admin_id])


class TeacherEarning(Base):
    """
    Auto-created when a video call session ends (status=completed).
    Stores the rate_per_minute snapshot — so admin rate changes
    don't affect past earnings.
    """
    __tablename__ = "teacher_earnings"

    id             = Column(Integer, primary_key=True, index=True)
    teacher_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id     = Column(Integer, ForeignKey("video_call_sessions.id"), nullable=False, unique=True)

    duration_mins  = Column(Integer, nullable=False)
    rate_per_hour  = Column(Numeric(8, 2), nullable=False)
    gross_earning  = Column(Numeric(10, 2), nullable=False)   # (duration_mins / 60) × rate_per_hour

    # Payout tracking
    payout_status  = Column(String(20), nullable=False, default="pending")  # pending / paid
    payout_batch_id = Column(Integer, ForeignKey("payout_batches.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    teacher       = relationship("User", foreign_keys=[teacher_id])
    session       = relationship("VideoCallSession", back_populates="earning")
    payout_batch  = relationship("PayoutBatch", back_populates="earnings")

    def __repr__(self):
        return f"<TeacherEarning(teacher={self.teacher_id}, earning={self.gross_earning}, status={self.payout_status})>"


class PayoutBatch(Base):
    """
    Admin-triggered monthly payout batch.
    One batch covers an entire period.
    """
    __tablename__ = "payout_batches"

    id             = Column(Integer, primary_key=True, index=True)
    initiated_by   = Column(Integer, ForeignKey("users.id"), nullable=False)

    period_from    = Column(Date, nullable=False)
    period_to      = Column(Date, nullable=False)

    total_teachers = Column(Integer, default=0)
    total_amount   = Column(Numeric(12, 2), default=0.00)

    status         = Column(String(20), nullable=False, default="processing")  # processing / completed
    notes          = Column(Text, nullable=True)

    created_at     = Column(DateTime(timezone=True), server_default=func.now())
    completed_at   = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    admin    = relationship("User", foreign_keys=[initiated_by])
    payouts  = relationship("TeacherPayout", back_populates="batch")
    earnings = relationship("TeacherEarning", back_populates="payout_batch")


class TeacherPayout(Base):
    """
    Per-teacher payment record within a payout batch.
    """
    __tablename__ = "teacher_payouts"

    id             = Column(Integer, primary_key=True, index=True)
    batch_id       = Column(Integer, ForeignKey("payout_batches.id"), nullable=False, index=True)
    teacher_id     = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    amount         = Column(Numeric(10, 2), nullable=False)
    sessions_count = Column(Integer, nullable=False, default=0)

    # Bank details snapshot (from teacher's profile at payout time)
    bank_account   = Column(String(200), nullable=True)

    # Razorpay Payout API reference (Phase 2)
    razorpay_payout_id = Column(String(100), nullable=True)

    status         = Column(String(20), nullable=False, default="pending")  # pending / transferred / failed
    transferred_at = Column(DateTime(timezone=True), nullable=True)
    notes          = Column(Text, nullable=True)

    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    batch   = relationship("PayoutBatch", back_populates="payouts")
    teacher = relationship("User", foreign_keys=[teacher_id])

    def __repr__(self):
        return f"<TeacherPayout(teacher={self.teacher_id}, amount={self.amount}, status={self.status})>"
