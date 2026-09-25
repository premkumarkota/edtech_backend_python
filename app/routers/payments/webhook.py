"""
Payment Webhook Handlers
POST /api/payments/webhook/razorpay   — student payments (+ legacy Razorpay X payouts)
POST /api/payments/webhook/cashfree   — Cashfree Payouts V2 (teacher withdrawals)

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
from typing import Optional

from fastapi import APIRouter, Request, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.subscription import StudentSubscription
from app.models.payment import RazorpayPayment
from app.models.withdrawal import WithdrawalRequest
from app.services import cashfree_payout_service as cashfree_payouts
from app.services.razorpay_service import verify_webhook_signature
from app.services.subscription_service import activate_subscription, fail_subscription
from app.services.withdrawal_settlement import (
    mark_withdrawal_completed,
    mark_withdrawal_failed,
    withdrawal_id_from_reference,
)

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


def _find_withdrawal(reference_id: str, event: str, db: Session) -> Optional[WithdrawalRequest]:
    withdrawal_id = withdrawal_id_from_reference(reference_id)
    if withdrawal_id is None:
        logger.warning(f"{event}: unknown reference '{reference_id}' — skipping")
        return None
    w = db.query(WithdrawalRequest).filter(WithdrawalRequest.id == withdrawal_id).first()
    if not w:
        logger.error(f"{event}: WithdrawalRequest {withdrawal_id} not found")
    return w


def _handle_payout_processed(payload: dict, db: Session) -> None:
    """Razorpay confirmed the payout reached the teacher's bank."""
    payout_entity = payload["payload"]["payout"]["entity"]
    w = _find_withdrawal(payout_entity.get("reference_id", ""), "payout.processed", db)
    if w:
        mark_withdrawal_completed(w, payout_entity.get("id", ""), db)


def _handle_payout_failed(payload: dict, event: str, db: Session) -> None:
    """
    Razorpay payout failed or was reversed.
    Mark withdrawal as 'failed' so teacher can re-request.
    Earnings remain 'pending' — they are NOT marked paid.
    """
    payout_entity = payload["payload"]["payout"]["entity"]
    failure_detail = (
        payout_entity.get("status_details", {}).get("description", "")
        or payout_entity.get("status_details", {}).get("reason", "")
        or event
    )
    w = _find_withdrawal(payout_entity.get("reference_id", ""), event, db)
    if w:
        mark_withdrawal_failed(w, failure_detail, db)


# ── Cashfree Payouts (teacher withdrawals) ────────────────────────────────────

_CASHFREE_SUCCESS_EVENTS = {"TRANSFER_SUCCESS", "TRANSFER_ACKNOWLEDGED"}
_CASHFREE_FAILED_EVENTS = {"TRANSFER_FAILED", "TRANSFER_REJECTED", "TRANSFER_REVERSED"}


@router.post("/cashfree")
async def cashfree_payout_webhook(
    request: Request,
    db: Session = Depends(get_db),
):
    """
    Cashfree Payouts V2 webhook (configure as version V2 in the Payouts Dashboard).
    Signature: x-webhook-signature = base64(HMAC_SHA256(x-webhook-timestamp + raw body, client secret)).
    Always returns 200; handlers are idempotent so Cashfree retries are harmless.
    """
    body = await request.body()
    signature = request.headers.get("x-webhook-signature", "")
    timestamp = request.headers.get("x-webhook-timestamp", "")

    if not cashfree_payouts.verify_webhook_signature(body, signature, timestamp):
        logger.warning("Invalid Cashfree payout webhook signature received.")
        return {"status": "ignored"}

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        logger.error("Could not parse Cashfree webhook body as JSON.")
        return {"status": "error"}

    event = payload.get("type", "")
    data = payload.get("data") or {}
    logger.info(f"Cashfree payout webhook received: {event} for {data.get('transfer_id')}")

    if event not in _CASHFREE_SUCCESS_EVENTS | _CASHFREE_FAILED_EVENTS:
        return {"status": "ok"}   # LOW_BALANCE_ALERT, CREDIT_CONFIRMATION, etc.

    try:
        w = _find_withdrawal(data.get("transfer_id", ""), event, db)
        if not w:
            return {"status": "ok"}
        if event in _CASHFREE_SUCCESS_EVENTS:
            mark_withdrawal_completed(w, str(data.get("cf_transfer_id", "")), db)
        else:
            reason = (
                data.get("status_description")
                or data.get("status_code")
                or event
            )
            mark_withdrawal_failed(w, reason, db)
    except Exception as e:
        db.rollback()
        logger.error(f"ERROR processing Cashfree {event} webhook: {e}")

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
