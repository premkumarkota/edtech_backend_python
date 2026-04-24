from sqlalchemy import Column, Integer, String, Numeric, DateTime, ForeignKey, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class RazorpayPayment(Base):
    """
    Full audit log of every Razorpay payment event.
    Never deleted. Source of truth for payment disputes.
    """
    __tablename__ = "razorpay_payments"

    id              = Column(Integer, primary_key=True, index=True)
    student_id      = Column(Integer, ForeignKey("users.id"), nullable=True)
    subscription_id = Column(Integer, ForeignKey("student_subscriptions.id"), nullable=True)

    razorpay_order_id   = Column(String(100), nullable=False, unique=True, index=True)
    razorpay_payment_id = Column(String(100), nullable=True, unique=True, index=True)
    razorpay_signature  = Column(String(500), nullable=True)

    amount   = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(10), default="INR")

    # payment.created → payment.authorized → payment.captured / payment.failed
    event_type = Column(String(50), nullable=True)
    status     = Column(String(30), nullable=False, default="created")  # created / captured / failed

    # Full raw Razorpay webhook payload stored for audit
    gateway_response = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    student      = relationship("User", foreign_keys=[student_id])
    subscription = relationship("StudentSubscription", foreign_keys=[subscription_id])

    def __repr__(self):
        return f"<RazorpayPayment(order={self.razorpay_order_id}, status={self.status})>"
