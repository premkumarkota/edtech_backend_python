import enum
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, Numeric,
    DateTime, Date, ForeignKey, Enum, Index, UniqueConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class SubscriptionStatus(str, enum.Enum):
    PENDING    = "pending"
    ACTIVE     = "active"
    SUPERSEDED = "superseded"   # replaced mid-cycle; remaining minutes carried forward
    EXPIRED    = "expired"
    CANCELLED  = "cancelled"
    FAILED     = "failed"


class SubscriptionPlan(Base):
    """
    Admin-defined subscription plan.
    Students purchase one of these plans.
    """
    __tablename__ = "subscription_plans"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), nullable=False)          # "Basic", "Pro", "Elite"
    description = Column(Text, nullable=True)
    price       = Column(Numeric(10, 2), nullable=False)       # INR
    validity_days = Column(Integer, nullable=False, default=30)

    # Entitlements (0 = unlimited for mock_tests, 0 = no video for video_minutes)
    mock_tests_allowed           = Column(Integer, nullable=False, default=0)
    video_call_minutes_per_month = Column(Integer, nullable=False, default=0)

    is_active  = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    subscriptions = relationship("StudentSubscription", back_populates="plan")

    def __repr__(self):
        return f"<SubscriptionPlan(id={self.id}, name={self.name}, price={self.price})>"


class StudentSubscription(Base):
    """
    A student's purchased subscription.
    ENFORCED: only ONE 'active' subscription per student (partial unique index).
    Entitlements are SNAPSHOTTED from the plan at purchase time.
    """
    __tablename__ = "student_subscriptions"

    id          = Column(Integer, primary_key=True, index=True)
    student_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    plan_id     = Column(Integer, ForeignKey("subscription_plans.id"), nullable=False)

    status      = Column(
        Enum(SubscriptionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SubscriptionStatus.PENDING.value,
    )

    # Timestamps
    started_at  = Column(DateTime(timezone=True), nullable=True)
    expires_at  = Column(DateTime(timezone=True), nullable=True)

    # --- Entitlement Snapshot (copied from plan at purchase time) ---
    mock_tests_allowed         = Column(Integer, nullable=False, default=0)
    mock_tests_used            = Column(Integer, nullable=False, default=0)
    video_call_minutes_total   = Column(Integer, nullable=False, default=0)
    video_call_minutes_used    = Column(Integer, nullable=False, default=0)
    carried_over_minutes       = Column(Integer, nullable=False, default=0)  # audit: minutes rolled in from superseded plan

    # --- Razorpay References ---
    razorpay_order_id   = Column(String(100), unique=True, nullable=True)
    razorpay_payment_id = Column(String(100), unique=True, nullable=True)
    amount_paid         = Column(Numeric(10, 2), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    plan    = relationship("SubscriptionPlan", back_populates="subscriptions")
    student = relationship("User", foreign_keys=[student_id])
    sessions = relationship("VideoCallSession", back_populates="subscription")

    @property
    def mock_tests_remaining(self):
        if self.mock_tests_allowed == 0:
            return None  # Unlimited
        return max(0, self.mock_tests_allowed - self.mock_tests_used)

    @property
    def video_minutes_remaining(self):
        return max(0, self.video_call_minutes_total - self.video_call_minutes_used)

    def __repr__(self):
        return f"<StudentSubscription(id={self.id}, student={self.student_id}, status={self.status})>"
