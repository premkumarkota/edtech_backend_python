"""
Razorpay Webhook Handler
POST /api/payments/webhook/razorpay

No JWT auth — HMAC signature verification only.
Source of truth for payment AND payout status.
Always returns 200 to Razorpay (non-200 causes infinite retries).

Handles:
  payment.captured  → activate student subscription
  payment.failed    → fail student subscription
  payout.processed  → mark WithdrawalRequest completed + mark earnings paid + FCM teacher
  payout.failed     → mark WithdrawalRequest failed + FCM teacher
  payout.reversed   → mark WithdrawalRequest failed + FCM teacher
"""
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.subscription import StudentSubscription
from app.models.payment import RazorpayPayment
from app.models.payout import TeacherEarning
from app.models.withdrawal import WithdrawalRequest
from app.models.user import User
from app.services.razorpay_service import verify_webhook_signature
from app.services.subscription_service import activate_subscription, fail_subscription
from app.utils.fcm import notify_withdrawal_completed, notify_withdrawal_failed

logger = logging.getLogger(__name__)
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
            logger.info(f"Subscription for order {order_id} marked as failed.")

        except Exception as e:
            logger.error(f"ERROR processing payment.failed webhook: {e}")

    # 5. Handle payout.processed → Withdrawal completed, earnings marked paid
    elif event == "payout.processed":
        try:
            _handle_payout_processed(payload, db)
        except Exception as e:
            logger.error(f"ERROR processing payout.processed webhook: {e}")

    # 6. Handle payout.failed / payout.reversed → Withdrawal failed
    elif event in ("payout.failed", "payout.reversed"):
        try:
            _handle_payout_failed(payload, event, db)
        except Exception as e:
            logger.error(f"ERROR processing {event} webhook: {e}")

    # Always return 200
    return {"status": "ok"}


def _handle_payout_processed(payload: dict, db: Session) -> None:
    """
    Razorpay confirmed the payout reached the teacher's bank.

    Steps (all in one transaction):
      1. Find WithdrawalRequest by reference_id = "withdrawal_{id}".
      2. Verify it's still in 'processing' status (idempotency guard).
      3. Mark it as 'completed'.
      4. Mark enough pending TeacherEarning rows as 'paid' (oldest-first)
         to cover the withdrawal amount.
      5. Send FCM to teacher.
    """
    payout_entity = payload["payload"]["payout"]["entity"]
    reference_id: str = payout_entity.get("reference_id", "")
    razorpay_payout_id: str = payout_entity.get("id", "")

    if not reference_id.startswith("withdrawal_"):
        logger.warning(f"payout.processed: unknown reference_id '{reference_id}' — skipping")
        return

    try:
        withdrawal_id = int(reference_id.split("_", 1)[1])
    except (ValueError, IndexError):
        logger.error(f"payout.processed: cannot parse withdrawal id from '{reference_id}'")
        return

    w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
    if not w:
        logger.error(f"payout.processed: WithdrawalRequest {withdrawal_id} not found")
        return

    if w.status == "completed":
        logger.info(f"payout.processed: withdrawal {withdrawal_id} already completed — idempotent skip")
        return

    if w.status != "processing":
        logger.warning(
            f"payout.processed: withdrawal {withdrawal_id} is '{w.status}', "
            "expected 'processing'. Marking completed anyway."
        )

    # Mark withdrawal completed
    w.status       = "completed"
    w.completed_at = datetime.now(timezone.utc)
    db.flush()

    # Mark pending earnings as paid (oldest-first up to withdrawal amount)
    pending_earnings = (
        db.query(TeacherEarning)
        .filter(
            TeacherEarning.teacher_id == w.teacher_id,
            TeacherEarning.payout_status == "pending",
        )
        .order_by(TeacherEarning.created_at.asc())
        .all()
    )

    settled = Decimal("0.00")
    for earning in pending_earnings:
        if settled >= w.amount:
            break
        earning.payout_status = "paid"
        settled += earning.gross_earning

    db.commit()
    logger.info(
        f"payout.processed: withdrawal {withdrawal_id} completed | "
        f"₹{w.amount} | {razorpay_payout_id} | settled earnings: ₹{settled}"
    )

    # Notify teacher
    teacher = db.query(User).filter(User.id == w.teacher_id).first()
    if teacher and teacher.fcm_token:
        notify_withdrawal_completed(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
        )


def _handle_payout_failed(payload: dict, event: str, db: Session) -> None:
    """
    Razorpay payout failed or was reversed.
    Mark withdrawal as 'failed' so teacher can re-request.
    Earnings remain 'pending' — they are NOT marked paid.
    """
    payout_entity = payload["payload"]["payout"]["entity"]
    reference_id: str = payout_entity.get("reference_id", "")
    failure_detail = (
        payout_entity.get("status_details", {}).get("description", "")
        or payout_entity.get("status_details", {}).get("reason", "")
        or event
    )

    if not reference_id.startswith("withdrawal_"):
        logger.warning(f"{event}: unknown reference_id '{reference_id}' — skipping")
        return

    try:
        withdrawal_id = int(reference_id.split("_", 1)[1])
    except (ValueError, IndexError):
        logger.error(f"{event}: cannot parse withdrawal id from '{reference_id}'")
        return

    w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
    if not w:
        logger.error(f"{event}: WithdrawalRequest {withdrawal_id} not found")
        return

    if w.status in ("completed", "failed"):
        logger.info(f"{event}: withdrawal {withdrawal_id} already in terminal state — skip")
        return

    w.status         = "failed"
    w.failure_reason = failure_detail
    w.completed_at   = datetime.now(timezone.utc)
    db.commit()

    logger.warning(
        f"{event}: withdrawal {withdrawal_id} failed | reason: {failure_detail}"
    )

    teacher = db.query(User).filter(User.id == w.teacher_id).first()
    if teacher and teacher.fcm_token:
        notify_withdrawal_failed(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
            reason=failure_detail,
        )


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
