"""
Withdrawal settlement — provider-agnostic final state transitions.

Used by the Razorpay and Cashfree webhooks and by the admin "sync status"
endpoint, so every path applies the same rules:

  mark_withdrawal_completed → withdrawal 'completed', FCM teacher.
  mark_withdrawal_failed    → withdrawal 'failed', amount returns to the
                              teacher's balance automatically, FCM teacher.

The balance itself is a ledger (see teacher_wallet.py), so settling never
touches more money than the withdrawal amount. Per-session paid/pending
labels are recomputed after every transition.

Both are idempotent: re-delivered webhooks are no-ops.
"""
import logging
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.withdrawal import WithdrawalRequest
from app.services.teacher_wallet import sync_earning_labels
from app.utils.fcm import notify_withdrawal_completed, notify_withdrawal_failed

logger = logging.getLogger(__name__)


def mark_withdrawal_completed(w: WithdrawalRequest, payout_ref: str, db: Session) -> bool:
    """Returns True if the state changed."""
    if w.status == "completed":
        logger.info(f"withdrawal {w.id} already completed — idempotent skip")
        return False

    if w.status != "processing":
        logger.warning(
            f"withdrawal {w.id} is '{w.status}', expected 'processing'. Marking completed anyway."
        )

    w.status       = "completed"
    w.completed_at = datetime.now(timezone.utc)
    db.flush()

    sync_earning_labels(w.teacher_id, db)
    db.commit()
    logger.info(f"withdrawal {w.id} completed | ₹{w.amount} | {payout_ref}")

    teacher = db.query(User).filter(User.id == w.teacher_id).first()
    if teacher and teacher.fcm_token:
        notify_withdrawal_completed(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
        )
    return True


def mark_withdrawal_failed(w: WithdrawalRequest, reason: str, db: Session) -> bool:
    """Returns True if the state changed."""
    if w.status == "failed":
        logger.info(f"withdrawal {w.id} already failed — skip")
        return False

    if w.status == "completed":
        # Bank sent the money back after success — it returns to the balance.
        logger.critical(f"withdrawal {w.id} REVERSED after completion: {reason}")
        reason = f"Reversed by bank: {reason}"

    w.status         = "failed"
    w.failure_reason = reason
    w.completed_at   = datetime.now(timezone.utc)
    db.flush()   # sessions use autoflush=False — labels must see the new status
    sync_earning_labels(w.teacher_id, db)
    db.commit()

    logger.warning(f"withdrawal {w.id} failed | reason: {reason}")

    teacher = db.query(User).filter(User.id == w.teacher_id).first()
    if teacher and teacher.fcm_token:
        notify_withdrawal_failed(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
            reason=reason,
        )
    return True


def withdrawal_id_from_reference(reference_id: str):
    """'withdrawal_42' → 42, anything else → None."""
    if not reference_id or not reference_id.startswith("withdrawal_"):
        return None
    try:
        return int(reference_id.split("_", 1)[1])
    except (ValueError, IndexError):
        return None
