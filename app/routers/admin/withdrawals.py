"""
Admin Withdrawal API
====================
GET  /api/admin/withdrawals?status=pending    List withdrawal requests
GET  /api/admin/withdrawals/{id}              Withdrawal detail
POST /api/admin/withdrawals/{id}/process      Trigger payout (Cashfree or Razorpay X)
POST /api/admin/withdrawals/{id}/sync         Re-check Cashfree status (missed webhook)
POST /api/admin/withdrawals/{id}/reject       Reject with reason

Security:
- All endpoints require admin JWT (require_admin).
- process endpoint is idempotent-guarded: only processes pending requests.
  Cashfree transfer_id = "withdrawal_{id}" also prevents double payment.
- Provider errors are surfaced as 502 — admin can retry after fixing the issue.
- Webhook (or sync) finalises status to completed/failed.
"""
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.payout import TeacherEarning
from app.models.withdrawal import TeacherBankDetails, WithdrawalRequest
from app.schemas.withdrawal import (
    AdminWithdrawalResponse,
    AdminProcessWithdrawalRequest,
    AdminRejectWithdrawalRequest,
)
from app.config import settings
from app.services import cashfree_payout_service as cashfree
from app.services import razorpay_payout_service as rp
from app.services.withdrawal_settlement import (
    mark_withdrawal_completed,
    mark_withdrawal_failed,
)
from app.utils.fcm import notify_withdrawal_rejected, notify_withdrawal_processing

router = APIRouter()
logger = logging.getLogger(__name__)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_response(w: WithdrawalRequest) -> AdminWithdrawalResponse:
    teacher = w.teacher
    return AdminWithdrawalResponse(
        id=w.id,
        teacher_id=w.teacher_id,
        teacher_name=teacher.name if teacher else None,
        teacher_phone=teacher.phone_number if teacher else None,
        amount=w.amount,
        status=w.status,
        bank_snapshot=w.bank_snapshot,
        razorpay_payout_id=w.razorpay_payout_id,
        payout_reference=w.razorpay_payout_id,
        admin_notes=w.admin_notes,
        rejection_reason=w.rejection_reason,
        failure_reason=w.failure_reason,
        requested_at=w.requested_at,
        processed_at=w.processed_at,
        completed_at=w.completed_at,
    )


# ── List ──────────────────────────────────────────────────────────────────────

@router.get("", response_model=List[AdminWithdrawalResponse])
def list_withdrawals(
    status_filter: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    List withdrawal requests.
    ?status=pending|processing|completed|failed|rejected|all
    Default: all (newest first).
    """
    q = db.query(WithdrawalRequest).order_by(
        WithdrawalRequest.requested_at.desc()
    )
    if status_filter and status_filter != "all":
        q = q.filter(WithdrawalRequest.status == status_filter)
    return [_build_response(w) for w in q.all()]


# ── Detail ────────────────────────────────────────────────────────────────────

@router.get("/{withdrawal_id}", response_model=AdminWithdrawalResponse)
def get_withdrawal(
    withdrawal_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    w = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.id == withdrawal_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")
    return _build_response(w)


# ── Process (Trigger Payout) ─────────────────────────────────────────────────

@router.post("/{withdrawal_id}/process")
def process_withdrawal(
    withdrawal_id: int,
    payload: AdminProcessWithdrawalRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Trigger the payout for a pending withdrawal via PAYOUT_PROVIDER.

    Cashfree (default): one transfer call with bank details inline.

    Razorpay X (legacy) steps:
      1. Validate withdrawal is in 'pending' status.
      2. Load teacher's bank details (from DB, not the snapshot — need live IDs).
      3. get_or_create_contact → cache contact_id.
      4. get_or_create_fund_account → cache fund_account_id.
      5. create_payout → store payout_id, set status='processing'.
      6. Send FCM to teacher.

    The webhook handler (payout.processed / payout.failed) will set the final status.
    """
    w = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.id == withdrawal_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")

    if w.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Withdrawal is '{w.status}', not 'pending'. Cannot process.",
        )

    # Load live bank details (need actual account number for Razorpay)
    bank = (
        db.query(TeacherBankDetails)
        .filter(TeacherBankDetails.teacher_id == w.teacher_id)
        .first()
    )
    if not bank:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Teacher has no bank details on file. Ask them to add bank details.",
        )

    teacher = w.teacher
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher user not found.")

    if settings.PAYOUT_PROVIDER.lower() == "cashfree":
        return _process_via_cashfree(w, bank, teacher, admin, payload, db)

    # ── Legacy: Razorpay X ──
    # Step 3: Contact
    contact_id = rp.get_or_create_contact(
        teacher_id=w.teacher_id,
        name=teacher.name,
        phone=teacher.phone_number,
        email=teacher.email,
        existing_contact_id=bank.razorpay_contact_id,
    )
    if contact_id != bank.razorpay_contact_id:
        bank.razorpay_contact_id = contact_id
        db.flush()

    # Step 4: Fund Account
    fund_account_id = rp.get_or_create_fund_account(
        teacher_id=w.teacher_id,
        contact_id=contact_id,
        account_holder_name=bank.account_holder_name,
        account_number=bank.account_number,
        ifsc_code=bank.ifsc_code,
        existing_fund_account_id=bank.razorpay_fund_account_id,
    )
    if fund_account_id != bank.razorpay_fund_account_id:
        bank.razorpay_fund_account_id = fund_account_id
        # Update snapshot on withdrawal row too (for webhook reconciliation)
        w.razorpay_fund_account_id = fund_account_id
        db.flush()

    # Step 5: Payout (raises HTTPException on Razorpay error)
    payout_id = rp.create_payout(
        withdrawal_request_id=w.id,
        fund_account_id=fund_account_id,
        amount_inr=w.amount,
    )

    # Update withdrawal status atomically
    w.status           = "processing"
    w.razorpay_payout_id = payout_id
    w.processed_by     = admin.id
    w.admin_notes      = payload.notes
    w.processed_at     = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        f"Admin {admin.id} processed withdrawal {w.id} → "
        f"Razorpay payout {payout_id} | ₹{w.amount}"
    )

    # Step 6: Notify teacher
    if teacher.fcm_token:
        notify_withdrawal_processing(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
        )

    return {
        "message": "Payout initiated via Razorpay.",
        "withdrawal_id": w.id,
        "razorpay_payout_id": payout_id,
        "status": "processing",
    }


def _process_via_cashfree(
    w: WithdrawalRequest,
    bank: TeacherBankDetails,
    teacher: User,
    admin: User,
    payload: AdminProcessWithdrawalRequest,
    db: Session,
):
    """
    Create the Cashfree transfer. Cashfree may reject synchronously (HTTP 200,
    status REJECTED/FAILED — e.g. insufficient balance, invalid account); the
    transfer_id is then burnt, so the withdrawal is marked failed and the
    teacher re-requests. Unknown outcomes raise 502 and leave it pending.
    """
    result = cashfree.create_transfer(
        withdrawal_request_id=w.id,
        amount_inr=w.amount,
        account_holder_name=bank.account_holder_name,
        account_number=bank.account_number,
        ifsc_code=bank.ifsc_code,
        phone=teacher.phone_number,
        email=teacher.email,
    )

    w.status             = "processing"
    w.razorpay_payout_id = result.cf_transfer_id or result.transfer_id
    w.processed_by       = admin.id
    w.admin_notes        = payload.notes
    w.processed_at       = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        f"Admin {admin.id} processed withdrawal {w.id} → Cashfree transfer "
        f"{result.transfer_id} (cf={result.cf_transfer_id}) | ₹{w.amount} | {result.status}"
    )

    if result.is_failed:
        mark_withdrawal_failed(w, result.failure_reason, db)
        return {
            "message": f"Cashfree rejected the payout: {result.failure_reason}",
            "withdrawal_id": w.id,
            "payout_reference": w.razorpay_payout_id,
            "provider_status": result.status,
            "status": "failed",
        }

    if result.is_success:
        mark_withdrawal_completed(w, w.razorpay_payout_id, db)
    elif teacher.fcm_token:
        notify_withdrawal_processing(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            withdrawal_id=w.id,
        )

    return {
        "message": "Payout initiated via Cashfree.",
        "withdrawal_id": w.id,
        "payout_reference": w.razorpay_payout_id,
        "provider_status": result.status,
        "status": w.status,
    }


# ── Sync (Cashfree status re-check) ───────────────────────────────────────────

@router.post("/{withdrawal_id}/sync")
def sync_withdrawal_status(
    withdrawal_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Pull the latest transfer status from Cashfree and apply it.
    Backup for a missed/delayed webhook. Only for 'processing' withdrawals.
    """
    w = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.id == withdrawal_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")

    if w.status != "processing":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Withdrawal is '{w.status}'. Only processing withdrawals can be synced.",
        )

    result = cashfree.get_transfer(cashfree.transfer_id_for(w.id))
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Cashfree has no transfer for this withdrawal.",
        )

    if result.is_success:
        mark_withdrawal_completed(w, result.cf_transfer_id or result.transfer_id, db)
    elif result.is_failed:
        mark_withdrawal_failed(w, result.failure_reason, db)

    return {
        "withdrawal_id": w.id,
        "provider_status": result.status,
        "provider_status_code": result.status_code,
        "status": w.status,
    }


# ── Reject ────────────────────────────────────────────────────────────────────

@router.post("/{withdrawal_id}/reject")
def reject_withdrawal(
    withdrawal_id: int,
    payload: AdminRejectWithdrawalRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Reject a pending withdrawal with a reason.
    This does NOT interact with the payout provider at all.
    A rejected request frees the teacher to submit a new one.
    """
    w = db.query(WithdrawalRequest).filter(
        WithdrawalRequest.id == withdrawal_id
    ).first()
    if not w:
        raise HTTPException(status_code=404, detail="Withdrawal request not found.")

    if w.status != "pending":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Withdrawal is '{w.status}', not 'pending'. Cannot reject.",
        )

    w.status           = "rejected"
    w.rejection_reason = payload.reason
    w.processed_by     = admin.id
    w.processed_at     = datetime.now(timezone.utc)
    db.commit()

    logger.info(
        f"Admin {admin.id} rejected withdrawal {w.id}: {payload.reason}"
    )

    # Notify teacher
    teacher = w.teacher
    if teacher and teacher.fcm_token:
        notify_withdrawal_rejected(
            fcm_token=teacher.fcm_token,
            amount=float(w.amount),
            reason=payload.reason,
        )

    return {
        "message": "Withdrawal rejected.",
        "withdrawal_id": w.id,
        "reason": payload.reason,
    }
