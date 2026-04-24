"""
Razorpay Webhook Handler
POST /api/payments/webhook/razorpay

No JWT auth — HMAC signature verification only.
Source of truth for payment status.
Always returns 200 to Razorpay (non-200 causes infinite retries).
"""
import json
from typing import Optional, Union
from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.subscription import StudentSubscription
from app.models.payment import RazorpayPayment
from app.services.razorpay_service import verify_webhook_signature
from app.services.subscription_service import activate_subscription, fail_subscription

router = APIRouter()


@router.post("/razorpay")
async def razorpay_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Handle Razorpay payment events.
    CRITICAL: Always return 200 — errors are logged internally.
    Non-200 causes Razorpay to retry indefinitely.
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # 1. Verify webhook signature
    if not verify_webhook_signature(body, signature):
        # Return 200 even on invalid signature — but don't process
        # Razorpay may retry with forged signatures in edge cases
        print(f"WARNING: Invalid Razorpay webhook signature received.")
        return {"status": "ignored"}

    # 2. Parse event
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        print("ERROR: Could not parse Razorpay webhook body as JSON.")
        return {"status": "error"}

    event = payload.get("event", "")
    print(f"INFO: Razorpay webhook received: {event}")

    # 3. Handle payment.captured → Activate subscription
    if event == "payment.captured":
        try:
            payment_entity = payload["payload"]["payment"]["entity"]
            order_id = payment_entity.get("order_id")
            payment_id = payment_entity.get("id")
            amount_paise = payment_entity.get("amount", 0)

            # Find the pending subscription
            sub = db.query(StudentSubscription).filter(
                StudentSubscription.razorpay_order_id == order_id
            ).first()

            if sub:
                # Validate amount matches what we expect (security check)
                from app.models.subscription import SubscriptionPlan
                plan = db.query(SubscriptionPlan).filter(
                    SubscriptionPlan.id == sub.plan_id
                ).first()
                expected_paise = int(plan.price * 100) if plan else 0

                if expected_paise > 0 and amount_paise != expected_paise:
                    print(
                        f"WARNING: Amount mismatch for order {order_id}. "
                        f"Expected {expected_paise}, got {amount_paise}."
                    )
                    return {"status": "amount_mismatch"}

                # Activate (idempotent — safe if app already activated)
                activate_subscription(sub.id, payment_id, db)
                print(f"INFO: Subscription {sub.id} activated via webhook. Payment: {payment_id}")

            # Log the webhook event regardless
            _log_webhook_event(order_id, payment_id, event, payload, db)

        except Exception as e:
            print(f"ERROR processing payment.captured webhook: {e}")
            # Do NOT raise — return 200 to avoid Razorpay retries

    # 4. Handle payment.failed → Mark subscription failed
    elif event == "payment.failed":
        try:
            payment_entity = payload["payload"]["payment"]["entity"]
            order_id = payment_entity.get("order_id")
            payment_id = payment_entity.get("id")

            fail_subscription(order_id, db)
            _log_webhook_event(order_id, payment_id, event, payload, db)
            print(f"INFO: Subscription for order {order_id} marked as failed.")

        except Exception as e:
            print(f"ERROR processing payment.failed webhook: {e}")

    # Always return 200
    return {"status": "ok"}


def _log_webhook_event(
    order_id: str,
    payment_id: Optional[str],
    event_type: str,
    payload: dict,
    db: Session,
) -> None:
    """Store or update the payment audit record with webhook payload."""
    try:
        payment = db.query(RazorpayPayment).filter(
            RazorpayPayment.razorpay_order_id == order_id
        ).first()
        if payment:
            if payment_id:
                payment.razorpay_payment_id = payment_id
            payment.event_type = event_type
            payment.gateway_response = payload
        else:
            # May arrive before order was created in our DB (edge case)
            new_payment = RazorpayPayment(
                razorpay_order_id=order_id,
                razorpay_payment_id=payment_id,
                amount=0,
                event_type=event_type,
                status=event_type,
                gateway_response=payload,
            )
            db.add(new_payment)
        db.commit()
    except Exception as e:
        print(f"ERROR logging webhook event: {e}")
