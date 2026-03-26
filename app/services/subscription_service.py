"""
Subscription Service
Core business logic for subscription lifecycle:
  - Activate subscription (idempotent — safe to call from both webhook + app callback)
  - Get active subscription for a student
  - Check entitlement gates (mock test, video call)
"""
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.subscription import StudentSubscription, SubscriptionStatus, SubscriptionPlan
from app.models.payment import RazorpayPayment


def get_active_subscription(student_id: int, db: Session) -> StudentSubscription | None:
    """Return the student's current active, non-expired subscription, or None."""
    return db.query(StudentSubscription).filter(
        StudentSubscription.student_id == student_id,
        StudentSubscription.status == SubscriptionStatus.ACTIVE.value,
        StudentSubscription.expires_at > datetime.now(timezone.utc),
    ).first()


def activate_subscription(
    subscription_id: int,
    payment_id: str,
    db: Session,
) -> StudentSubscription | None:
    """
    Activate a pending subscription after payment is confirmed.

    IDEMPOTENT: Safe to call from both:
      (a) app verify-payment callback
      (b) Razorpay webhook (which may arrive seconds later)

    Uses SELECT FOR UPDATE to prevent race conditions if both arrive simultaneously.
    Returns the subscription if activated, None if already active (no-op).
    """
    sub = (
        db.query(StudentSubscription)
        .filter(
            StudentSubscription.id == subscription_id,
            StudentSubscription.status == SubscriptionStatus.PENDING.value,
        )
        .with_for_update()   # Row-level lock
        .first()
    )

    if not sub:
        # Already activated or doesn't exist — idempotent no-op
        return None

    plan: SubscriptionPlan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.id == sub.plan_id
    ).first()

    now = datetime.now(timezone.utc)
    sub.status      = SubscriptionStatus.ACTIVE.value
    sub.started_at  = now
    sub.expires_at  = now + timedelta(days=plan.validity_days)
    sub.amount_paid = plan.price
    sub.razorpay_payment_id = payment_id

    # Snapshot entitlements from plan
    sub.mock_tests_allowed       = plan.mock_tests_allowed
    sub.video_call_minutes_total = plan.video_call_minutes_per_month

    # Update the payment audit record
    payment = db.query(RazorpayPayment).filter(
        RazorpayPayment.razorpay_order_id == sub.razorpay_order_id
    ).first()
    if payment:
        payment.status = "captured"
        payment.razorpay_payment_id = payment_id

    db.commit()
    db.refresh(sub)
    return sub


def fail_subscription(razorpay_order_id: str, db: Session) -> None:
    """Mark a subscription as failed after a payment failure event."""
    sub = db.query(StudentSubscription).filter(
        StudentSubscription.razorpay_order_id == razorpay_order_id,
        StudentSubscription.status == SubscriptionStatus.PENDING.value,
    ).first()

    if sub:
        sub.status = SubscriptionStatus.FAILED.value
        payment = db.query(RazorpayPayment).filter(
            RazorpayPayment.razorpay_order_id == razorpay_order_id
        ).first()
        if payment:
            payment.status = "failed"
        db.commit()


def require_active_subscription(student_id: int, db: Session) -> StudentSubscription:
    """Raise 403 if student doesn't have an active subscription."""
    sub = get_active_subscription(student_id, db)
    if not sub:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Active subscription required. Please subscribe to access this feature.",
        )
    return sub


def require_video_minutes(student_id: int, db: Session) -> StudentSubscription:
    """Raise 403 if student has no video call minutes remaining."""
    sub = require_active_subscription(student_id, db)
    if sub.video_call_minutes_total == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Your plan does not include video calls. Please upgrade.",
        )
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used
    if remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have used all your video call minutes for this subscription period.",
        )
    return sub


def build_status_response(student_id: int, db: Session) -> dict:
    """Build the full subscription status dict for the student."""
    sub = get_active_subscription(student_id, db)
    if not sub:
        return {"has_active_subscription": False}

    days_remaining = (sub.expires_at - datetime.now(timezone.utc)).days if sub.expires_at else 0
    mock_remaining = None if sub.mock_tests_allowed == 0 else max(0, sub.mock_tests_allowed - sub.mock_tests_used)

    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == sub.plan_id).first()

    return {
        "has_active_subscription": True,
        "subscription_id": sub.id,
        "plan": plan,
        "expires_at": sub.expires_at,
        "days_remaining": max(0, days_remaining),
        "mock_tests_allowed": None if sub.mock_tests_allowed == 0 else sub.mock_tests_allowed,
        "mock_tests_used": sub.mock_tests_used,
        "mock_tests_remaining": mock_remaining,
        "video_call_minutes_total": sub.video_call_minutes_total,
        "video_call_minutes_used": sub.video_call_minutes_used,
        "video_call_minutes_remaining": max(0, sub.video_call_minutes_total - sub.video_call_minutes_used),
    }
