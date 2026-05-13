"""
Razorpay X Payout Service
=========================
Handles teacher bank payouts via the Razorpay Payouts API.

Flow (per withdrawal):
  1. get_or_create_contact()      → razorpay_contact_id  (cached on TeacherBankDetails)
  2. get_or_create_fund_account() → razorpay_fund_account_id (cached; nulled on bank change)
  3. create_payout()              → razorpay_payout_id    (stored on WithdrawalRequest)

Webhook (payout.processed / payout.failed / payout.reversed) uses reference_id
= "withdrawal_{withdrawal_request_id}" to find the right WithdrawalRequest row.

IMPORTANT:
- Razorpay X requires a linked Current Account with sufficient balance.
- Auth: HTTP Basic (Key ID : Key Secret) — same credentials as collection API.
- All amounts are in **paise** (multiply ₹ × 100).
- Never retry create_payout() on the same withdrawal — razorpay_payout_id is
  unique-constrained in the DB. Idempotency via reference_id only if Razorpay
  supports it (not guaranteed for payouts).
"""
import json
import logging
import base64
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

_RAZORPAY_BASE = "https://api.razorpay.com/v1"


def _auth_header() -> dict:
    """HTTP Basic auth header for Razorpay API."""
    token = base64.b64encode(
        f"{settings.RAZORPAY_KEY_ID}:{settings.RAZORPAY_KEY_SECRET}".encode()
    ).decode()
    return {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }


def _post(path: str, payload: dict) -> dict:
    """
    Synchronous POST to Razorpay API.
    Raises HTTPException(502) on network error or non-2xx response.
    """
    url = f"{_RAZORPAY_BASE}{path}"
    try:
        response = httpx.post(url, json=payload, headers=_auth_header(), timeout=30)
    except httpx.RequestError as exc:
        logger.error(f"Razorpay network error [{path}]: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Razorpay. Please try again.",
        )

    if response.status_code not in (200, 201):
        error_body = response.text
        logger.error(f"Razorpay error [{path}] {response.status_code}: {error_body}")
        try:
            err = response.json()
            detail = err.get("error", {}).get("description", error_body)
        except Exception:
            detail = error_body
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Razorpay error: {detail}",
        )

    return response.json()


# ── Step 1: Contact ───────────────────────────────────────────────────────────

def get_or_create_contact(
    teacher_id: int,
    name: str,
    phone: Optional[str],
    email: Optional[str],
    existing_contact_id: Optional[str],
) -> str:
    """
    Return cached contact_id if available.
    Otherwise create a new Razorpay Contact and return its id.

    The caller is responsible for persisting the returned id to
    TeacherBankDetails.razorpay_contact_id.
    """
    if existing_contact_id:
        logger.info(f"Using cached Razorpay contact {existing_contact_id} for teacher {teacher_id}")
        return existing_contact_id

    payload = {
        "name": name or f"Teacher {teacher_id}",
        "type": "employee",
        "reference_id": f"teacher_{teacher_id}",
    }
    if phone:
        payload["contact"] = phone.lstrip("+").lstrip("0")[-10:]  # last 10 digits
    if email:
        payload["email"] = email

    data = _post("/contacts", payload)
    contact_id = data["id"]
    logger.info(f"Created Razorpay contact {contact_id} for teacher {teacher_id}")
    return contact_id


# ── Step 2: Fund Account ──────────────────────────────────────────────────────

def get_or_create_fund_account(
    teacher_id: int,
    contact_id: str,
    account_holder_name: str,
    account_number: str,
    ifsc_code: str,
    existing_fund_account_id: Optional[str],
) -> str:
    """
    Return cached fund_account_id if available.
    Otherwise create a new Fund Account linked to the contact.

    The caller must null out TeacherBankDetails.razorpay_fund_account_id
    whenever the teacher changes their bank account, forcing re-creation here.
    """
    if existing_fund_account_id:
        logger.info(
            f"Using cached Razorpay fund account {existing_fund_account_id} "
            f"for teacher {teacher_id}"
        )
        return existing_fund_account_id

    payload = {
        "contact_id": contact_id,
        "account_type": "bank_account",
        "bank_account": {
            "name": account_holder_name,
            "ifsc": ifsc_code.upper(),
            "account_number": account_number,
        },
    }

    data = _post("/fund_accounts", payload)
    fund_account_id = data["id"]
    logger.info(
        f"Created Razorpay fund account {fund_account_id} for teacher {teacher_id}"
    )
    return fund_account_id


# ── Step 3: Payout ────────────────────────────────────────────────────────────

def create_payout(
    withdrawal_request_id: int,
    fund_account_id: str,
    amount_inr: Decimal,
    narration: str = "MyMentor teacher payout",
) -> str:
    """
    Create the actual money transfer via Razorpay Payouts.

    reference_id = "withdrawal_{id}" — used by the webhook handler to
    identify which WithdrawalRequest to update.

    Returns the Razorpay payout ID (e.g. "pout_XXXXXXXXXXXXX").
    Raises HTTPException on any Razorpay error.

    IMPORTANT: Call this ONCE per WithdrawalRequest. Never retry — the payout
    may have already been queued by Razorpay even if our response was lost.
    Use the webhook (payout.processed / payout.failed) as the final truth.
    """
    account_number = getattr(settings, "RAZORPAY_ACCOUNT_NUMBER", "")
    if not account_number:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Razorpay X account not configured. Contact platform admin.",
        )

    amount_paise = int(amount_inr * 100)
    if amount_paise <= 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payout amount must be positive.",
        )

    # IMPS for amounts ≤ ₹2,00,000 (instant); NEFT otherwise
    mode = "IMPS" if amount_paise <= 20_000_000 else "NEFT"

    payload = {
        "account_number": account_number,
        "fund_account_id": fund_account_id,
        "amount": amount_paise,
        "currency": "INR",
        "mode": mode,
        "purpose": "payout",
        "queue_if_low_balance": True,   # Queue instead of failing if low balance
        "reference_id": f"withdrawal_{withdrawal_request_id}",
        "narration": narration,
    }

    data = _post("/payouts", payload)
    payout_id = data["id"]
    logger.info(
        f"Razorpay payout {payout_id} created for withdrawal {withdrawal_request_id} "
        f"| amount=₹{amount_inr} | mode={mode}"
    )
    return payout_id
