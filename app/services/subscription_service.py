"""
Subscription Service
Core business logic for subscription lifecycle:
  - Activate subscription (idempotent — safe to call from both webhook + app callback)
  - Get active subscription for a student
  - Check entitlement gates (mock test, video call)

Mid-cycle repurchase (carry-over) policy:
  A student may buy a new plan when their remaining video minutes fall to or below
  REPURCHASE_THRESHOLD_MINS. On activation the old plan is marked SUPERSEDED and its
  remaining minutes are added to the new plan. The new plan's validity clock starts
  from the purchase date — old remaining days are not transferred.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.subscription import StudentSubscription, SubscriptionStatus, SubscriptionPlan
from app.models.payment import RazorpayPayment
from app.models.user import User
from app.utils.fcm import notify_student_subscription_activated


# ── Policy constant ────────────────────────────────────────────────────────────
# When a student has ≤ this many minutes left they may purchase a new plan.
REPURCHASE_THRESHOLD_MINS = 30


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_active_subscription(student_id: int, db: Session) -> Optional[StudentSubscription]:
    """Return the student's current active, non-expired subscription, or None."""
    return db.query(StudentSubscription).filter(
        StudentSubscription.student_id == student_id,
        StudentSubscription.status == SubscriptionStatus.ACTIVE.value,
        StudentSubscription.expires_at > datetime.now(timezone.utc),
    ).first()


def can_repurchase(existing: Optional[StudentSubscription]) -> bool:
    """
    True when the student is allowed to buy a new plan right now.
    - No active subscription   → always allowed.
    - Active subscription      → allowed only when minutes_remaining ≤ threshold.
    """
    if existing is None:
        return True
    return existing.video_minutes_remaining <= REPURCHASE_THRESHOLD_MINS


# ── Activation ────────────────────────────────────────────────────────────────

def activate_subscription(
    subscription_id: int,
    payment_id: str,
    db: Session,
) -> Optional[StudentSubscription]:
    """
    Activate a pending subscription after payment is confirmed.

    IDEMPOTENT: Safe to call from both:
      (a) app verify-payment callback
      (b) Razorpay webhook (which may arrive seconds later)

    Carry-over logic:
      If the student had an active subscription with minutes remaining,
      that plan is marked SUPERSEDED and its remaining minutes are added
      to the new plan's total. The new plan's clock starts from NOW.

    Uses SELECT FOR UPDATE to prevent race conditions.
    Returns the activated subscription, or None if already active (no-op).
    """
    sub = (
        db.query(StudentSubscription)
        .filter(
            StudentSubscription.id == subscription_id,
            StudentSubscription.status == SubscriptionStatus.PENDING.value,
        )
        .with_for_update()
        .first()
    )

    if not sub:
        # Already activated or doesn't exist — idempotent no-op
        return None

    plan: SubscriptionPlan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.id == sub.plan_id
    ).first()

    now = datetime.now(timezone.utc)

    # ── Carry-over: supersede any existing active plan ─────────────────────────
    old_sub = get_active_subscription(sub.student_id, db)
    carried_over = 0
    if old_sub and old_sub.id != sub.id:
        carried_over = old_sub.video_minutes_remaining
        old_sub.status = SubscriptionStatus.SUPERSEDED.value
        # old_sub.expires_at left intact for audit purposes

    # ── Activate new plan ──────────────────────────────────────────────────────
    sub.status      = SubscriptionStatus.ACTIVE.value
    sub.started_at  = now
    sub.expires_at  = now + timedelta(days=plan.validity_days)
    sub.amount_paid = plan.price
    sub.razorpay_payment_id = payment_id

    # Snapshot entitlements + carry-over
    sub.mock_tests_allowed       = plan.mock_tests_allowed
    sub.video_call_minutes_total = plan.video_call_minutes_per_month + carried_over
    sub.carried_over_minutes     = carried_over

    # ── Payment audit record ───────────────────────────────────────────────────
    payment = db.query(RazorpayPayment).filter(
        RazorpayPayment.razorpay_order_id == sub.razorpay_order_id
    ).first()
    if payment:
        payment.status = "captured"
        payment.razorpay_payment_id = payment_id

    db.commit()
    db.refresh(sub)

    # ── Push notification — fire-and-forget ────────────────────────────────────
    try:
        student = db.query(User).filter(User.id == sub.student_id).first()
        if student and student.fcm_token:
            expires_str = sub.expires_at.strftime("%-d %b %Y") if sub.expires_at else "—"
            notify_student_subscription_activated(
                fcm_token=student.fcm_token,
                student_name=student.name or "there",
                plan_name=plan.name,
                expires_at=expires_str,
                video_minutes=sub.video_call_minutes_total,
            )
    except Exception:
        pass  # Never let notification failure break activation

    return sub


# ── Failure ───────────────────────────────────────────────────────────────────

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


# ── Entitlement gates ─────────────────────────────────────────────────────────

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
    if sub.video_minutes_remaining <= 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You have used all your video call minutes. Please top up your plan.",
        )
    return sub


# ── Status response ───────────────────────────────────────────────────────────

def build_status_response(student_id: int, db: Session) -> dict:
    """Build the full subscription status dict for the student."""
    sub = get_active_subscription(student_id, db)
    if not sub:
        return {"has_active_subscription": False}

    days_remaining = (sub.expires_at - datetime.now(timezone.utc)).days if sub.expires_at else 0
    mock_remaining = None if sub.mock_tests_allowed == 0 else max(0, sub.mock_tests_allowed - sub.mock_tests_used)
    minutes_remaining = sub.video_minutes_remaining

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
        "video_call_minutes_remaining": minutes_remaining,
        "carried_over_minutes": sub.carried_over_minutes,
        # Signal to the client whether the student is eligible to repurchase now
        "can_repurchase": minutes_remaining <= REPURCHASE_THRESHOLD_MINS,
        "repurchase_threshold": REPURCHASE_THRESHOLD_MINS,
    }
