"""
Teacher Withdrawal API
======================
GET    /api/teacher/withdrawals/bank-details         View saved bank account
PATCH  /api/teacher/withdrawals/bank-details         Save / update bank account
GET    /api/teacher/withdrawals/balance              Available balance + active request
POST   /api/teacher/withdrawals/request              Submit withdrawal request
GET    /api/teacher/withdrawals                      Withdrawal history

Security:
- All endpoints require a valid teacher JWT (get_current_teacher).
- Teachers can only see and act on their own data.
- Duplicate active-request guard prevents double submission.
- Amount validation against actual pending earnings in DB (not client-supplied).
"""
import json
import logging
from decimal import Decimal
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.payout import TeacherEarning
from app.models.withdrawal import TeacherBankDetails, WithdrawalRequest
from app.schemas.withdrawal import (
    BankDetailsSave,
    BankDetailsResponse,
    WithdrawalBalanceResponse,
    WithdrawalRequestCreate,
    WithdrawalRequestResponse,
)

router = APIRouter()
logger = logging.getLogger(__name__)

MIN_WITHDRAWAL = Decimal("100.00")

# Statuses that mean "a withdrawal is already in-flight"
_ACTIVE_STATUSES = {"pending", "processing"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask_account(number: str) -> str:
    """Return ****XXXX keeping only last 4 digits visible."""
    if not number or len(number) < 4:
        return "****"
    return f"****{number[-4:]}"


def _get_pending_balance(teacher_id: int, db: Session) -> tuple[Decimal, int]:
    """
    Return (total_pending_amount, count_of_pending_earnings).
    Only counts TeacherEarning rows with payout_status='pending'.
    """
    row = (
        db.query(
            func.coalesce(func.sum(TeacherEarning.gross_earning), Decimal("0.00")),
            func.count(TeacherEarning.id),
        )
        .filter(
            TeacherEarning.teacher_id == teacher_id,
            TeacherEarning.payout_status == "pending",
        )
        .one()
    )
    return row[0], row[1]


def _active_withdrawal(teacher_id: int, db: Session) -> WithdrawalRequest | None:
    """Return the currently pending/processing withdrawal if any."""
    return (
        db.query(WithdrawalRequest)
        .filter(
            WithdrawalRequest.teacher_id == teacher_id,
            WithdrawalRequest.status.in_(_ACTIVE_STATUSES),
        )
        .first()
    )


# ── Bank Details ──────────────────────────────────────────────────────────────

@router.get("/bank-details", response_model=BankDetailsResponse)
def get_bank_details(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Return the teacher's saved bank details (account number masked)."""
    details = (
        db.query(TeacherBankDetails)
        .filter(TeacherBankDetails.teacher_id == current_user.id)
        .first()
    )
    if not details:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No bank details saved yet.",
        )
    return BankDetailsResponse(
        id=details.id,
        account_holder_name=details.account_holder_name,
        account_number_masked=_mask_account(details.account_number),
        ifsc_code=details.ifsc_code,
        bank_name=details.bank_name,
        upi_id=details.upi_id,
        has_razorpay_fund_account=bool(details.razorpay_fund_account_id),
        updated_at=details.updated_at,
    )


@router.patch("/bank-details", response_model=BankDetailsResponse)
def save_bank_details(
    payload: BankDetailsSave,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Save or update bank details.
    If the account number or IFSC changes, the cached Razorpay fund account
    is invalidated so a new one is created on the next payout.
    """
    details = (
        db.query(TeacherBankDetails)
        .filter(TeacherBankDetails.teacher_id == current_user.id)
        .first()
    )

    if details:
        bank_changed = (
            details.account_number != payload.account_number
            or details.ifsc_code != payload.ifsc_code.upper()
        )
        details.account_holder_name = payload.account_holder_name
        details.account_number      = payload.account_number
        details.ifsc_code           = payload.ifsc_code.upper()
        details.bank_name           = payload.bank_name
        details.upi_id              = payload.upi_id
        if bank_changed:
            # Invalidate cached fund account — new one will be created on next payout
            details.razorpay_fund_account_id = None
            logger.info(
                f"Teacher {current_user.id} changed bank details — "
                "fund_account_id invalidated"
            )
    else:
        details = TeacherBankDetails(
            teacher_id=current_user.id,
            account_holder_name=payload.account_holder_name,
            account_number=payload.account_number,
            ifsc_code=payload.ifsc_code.upper(),
            bank_name=payload.bank_name,
            upi_id=payload.upi_id,
        )
        db.add(details)

    db.commit()
    db.refresh(details)

    return BankDetailsResponse(
        id=details.id,
        account_holder_name=details.account_holder_name,
        account_number_masked=_mask_account(details.account_number),
        ifsc_code=details.ifsc_code,
        bank_name=details.bank_name,
        upi_id=details.upi_id,
        has_razorpay_fund_account=bool(details.razorpay_fund_account_id),
        updated_at=details.updated_at,
    )


# ── Balance ───────────────────────────────────────────────────────────────────

@router.get("/balance", response_model=WithdrawalBalanceResponse)
def get_withdrawal_balance(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Return the teacher's available withdrawal balance and any active request.
    Available balance = sum of all pending TeacherEarning rows.
    """
    balance, count = _get_pending_balance(current_user.id, db)
    active = _active_withdrawal(current_user.id, db)

    active_response = None
    if active:
        active_response = WithdrawalRequestResponse(
            id=active.id,
            amount=active.amount,
            status=active.status,
            bank_snapshot=active.bank_snapshot,
            failure_reason=active.failure_reason,
            rejection_reason=active.rejection_reason,
            requested_at=active.requested_at,
            processed_at=active.processed_at,
            completed_at=active.completed_at,
        )

    return WithdrawalBalanceResponse(
        available_balance=balance,
        pending_sessions=count,
        active_withdrawal=active_response,
        minimum_withdrawal=MIN_WITHDRAWAL,
    )


# ── Request Withdrawal ────────────────────────────────────────────────────────

@router.post("/request", response_model=WithdrawalRequestResponse, status_code=201)
def request_withdrawal(
    payload: WithdrawalRequestCreate,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Submit a withdrawal request.

    Guards (all checked server-side — never trust client):
    1. Teacher must have bank details saved.
    2. No active (pending/processing) withdrawal already exists.
    3. Requested amount >= MIN_WITHDRAWAL (₹100).
    4. Requested amount <= actual pending earnings balance.
    """
    # Guard 1: bank details required
    bank = (
        db.query(TeacherBankDetails)
        .filter(TeacherBankDetails.teacher_id == current_user.id)
        .first()
    )
    if not bank:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Please add your bank details before requesting a withdrawal.",
        )

    # Guard 2: no duplicate active request
    if _active_withdrawal(current_user.id, db):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="You already have a withdrawal request in progress. "
                   "Please wait for it to complete.",
        )

    # Guard 3: minimum amount (also validated in schema, belt-and-suspenders)
    if payload.amount < MIN_WITHDRAWAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Minimum withdrawal is ₹{MIN_WITHDRAWAL}.",
        )

    # Guard 4: amount <= available balance
    balance, _ = _get_pending_balance(current_user.id, db)
    if payload.amount > balance:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Requested amount ₹{payload.amount} exceeds "
                f"available balance ₹{balance}."
            ),
        )

    # Snapshot bank details at request time (immutable for this request)
    bank_snapshot = json.dumps({
        "account_holder_name": bank.account_holder_name,
        "account_number_masked": _mask_account(bank.account_number),
        "ifsc_code": bank.ifsc_code,
        "bank_name": bank.bank_name,
        "upi_id": bank.upi_id,
    })

    withdrawal = WithdrawalRequest(
        teacher_id=current_user.id,
        amount=payload.amount,
        status="pending",
        bank_snapshot=bank_snapshot,
        razorpay_fund_account_id=bank.razorpay_fund_account_id,
    )
    db.add(withdrawal)
    db.commit()
    db.refresh(withdrawal)

    logger.info(
        f"Withdrawal request #{withdrawal.id} created by teacher {current_user.id} "
        f"for ₹{payload.amount}"
    )

    return WithdrawalRequestResponse(
        id=withdrawal.id,
        amount=withdrawal.amount,
        status=withdrawal.status,
        bank_snapshot=withdrawal.bank_snapshot,
        failure_reason=withdrawal.failure_reason,
        rejection_reason=withdrawal.rejection_reason,
        requested_at=withdrawal.requested_at,
        processed_at=withdrawal.processed_at,
        completed_at=withdrawal.completed_at,
    )


# ── History ───────────────────────────────────────────────────────────────────

@router.get("", response_model=List[WithdrawalRequestResponse])
def list_withdrawals(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Return all withdrawal requests for this teacher, newest first."""
    rows = (
        db.query(WithdrawalRequest)
        .filter(WithdrawalRequest.teacher_id == current_user.id)
        .order_by(WithdrawalRequest.requested_at.desc())
        .all()
    )
    return [
        WithdrawalRequestResponse(
            id=r.id,
            amount=r.amount,
            status=r.status,
            bank_snapshot=r.bank_snapshot,
            failure_reason=r.failure_reason,
            rejection_reason=r.rejection_reason,
            requested_at=r.requested_at,
            processed_at=r.processed_at,
            completed_at=r.completed_at,
        )
        for r in rows
    ]
