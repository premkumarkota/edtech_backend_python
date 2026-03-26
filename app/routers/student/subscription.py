"""
Student — Subscription & Payment
GET   /api/student/subscription/plans           Browse active plans (public)
POST  /api/student/subscription/create-order    Step 1: Create Razorpay order
POST  /api/student/subscription/verify-payment  Step 2: Verify & activate subscription
GET   /api/student/subscription/status          Current subscription status
"""
import uuid
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User
from app.models.subscription import (
    SubscriptionPlan, StudentSubscription, SubscriptionStatus
)
from app.models.payment import RazorpayPayment
from app.schemas.subscription import (
    SubscriptionPlanResponse, CreateOrderRequest, CreateOrderResponse,
    VerifyPaymentRequest, SubscriptionStatusResponse, StudentSubscriptionResponse
)
from app.services.razorpay_service import create_razorpay_order, verify_payment_signature
from app.services.subscription_service import (
    get_active_subscription, activate_subscription, build_status_response
)
from app.config import settings

router = APIRouter()


@router.get("/plans", response_model=List[SubscriptionPlanResponse])
def list_active_plans(db: Session = Depends(get_db)):
    """Browse all active subscription plans. No auth required."""
    return (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active == True)
        .order_by(SubscriptionPlan.price.asc())
        .all()
    )


@router.post("/create-order", response_model=CreateOrderResponse)
def create_subscription_order(
    payload: CreateOrderRequest,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Step 1 of Payment.
    Student selects a plan → we create a Razorpay order.
    Returns the order details for the Razorpay SDK in the app.
    """
    # Guard: No already-active subscription
    existing = get_active_subscription(student.id, db)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You already have an active subscription.",
        )

    plan = db.query(SubscriptionPlan).filter(
        SubscriptionPlan.id == payload.plan_id,
        SubscriptionPlan.is_active == True,
    ).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found or inactive.")

    # Create Razorpay order
    receipt = f"sub_{uuid.uuid4().hex[:12]}"
    rp_order = create_razorpay_order(
        amount_inr=plan.price,
        receipt=receipt,
        notes={
            "plan_id": str(plan.id),
            "student_id": str(student.id),
        },
    )

    # Create pending subscription record
    sub = StudentSubscription(
        student_id=student.id,
        plan_id=plan.id,
        status=SubscriptionStatus.PENDING.value,
        razorpay_order_id=rp_order["id"],
    )
    db.add(sub)
    db.flush()  # Get sub.id

    # Create payment audit record
    payment = RazorpayPayment(
        student_id=student.id,
        subscription_id=sub.id,
        razorpay_order_id=rp_order["id"],
        amount=plan.price,
        currency="INR",
        status="created",
    )
    db.add(payment)
    db.commit()

    return CreateOrderResponse(
        razorpay_order_id=rp_order["id"],
        amount=int(plan.price * 100),  # paise
        currency="INR",
        key_id=settings.RAZORPAY_KEY_ID,
        subscription_id=sub.id,
        plan_name=plan.name,
    )


@router.post("/verify-payment", response_model=SubscriptionStatusResponse)
def verify_and_activate(
    payload: VerifyPaymentRequest,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Step 2 of Payment (app-side UX callback).
    Verifies the HMAC signature and activates the subscription.
    NOTE: Razorpay webhook also activates — activation is idempotent.
    """
    # 1. Verify HMAC signature
    if not verify_payment_signature(
        payload.razorpay_order_id,
        payload.razorpay_payment_id,
        payload.razorpay_signature,
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payment signature verification failed. Please contact support.",
        )

    # 2. Find the pending subscription for this order
    sub = db.query(StudentSubscription).filter(
        StudentSubscription.razorpay_order_id == payload.razorpay_order_id,
        StudentSubscription.student_id == student.id,
    ).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription order not found.")

    # 3. Activate (idempotent)
    activate_subscription(sub.id, payload.razorpay_payment_id, db)

    # 4. Return fresh status
    return build_status_response(student.id, db)


@router.get("/status", response_model=SubscriptionStatusResponse)
def get_subscription_status(
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Get the current student's subscription status and remaining entitlements."""
    return build_status_response(student.id, db)
