"""
RazorpayX Payout Service
========================
Teacher bank payouts via the RazorpayX Payouts API (live rail from 2026-10-30;
Cashfree stays as a fallback, see cashfree_payout_service.py).

Flow (per withdrawal):
  1. get_or_create_contact()      → razorpay_contact_id      (cached on TeacherBankDetails)
  2. get_or_create_fund_account() → razorpay_fund_account_id (cached; nulled on bank change)
  3. create_payout()              → PayoutResult, payout id stored on WithdrawalRequest
  Webhook (payout.processed / failed / reversed / rejected) finalises status.
  get_payout() / find_payout_by_reference() → admin "sync" when a webhook is missed.

Idempotency:
  reference_id and the X-Payout-Idempotency header are both "withdrawal_{id}".
  Razorpay returns the original payout for a repeated idempotency key, so a
  create call that timed out can be retried safely and a withdrawal can never be
  paid twice.

Auth:
  HTTP Basic with RAZORPAYX_KEY_ID / RAZORPAYX_KEY_SECRET, falling back to the
  PG keys (same Razorpay account). rzp_test_ keys hit RazorpayX test mode.

IMPORTANT:
- Amounts are in **paise** (₹ × 100), minimum ₹1.00.
- Payout statuses: queued → pending (approval workflow) → processing →
  processed | reversed; rejected / cancelled / failed are terminal failures.
- Contacts and fund accounts belong to one Razorpay account. After switching
  accounts the cached IDs must be cleared (see features/migration_plan_payments.md).
"""
import base64
import hashlib
import hmac
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

_RAZORPAY_BASE = "https://api.razorpay.com/v1"

SUCCESS_STATUSES = {"processed"}
FAILED_STATUSES = {"failed", "reversed", "rejected", "cancelled"}


@dataclass
class PayoutResult:
    payout_id: str
    reference_id: str
    status: str                 # queued | pending | processing | processed | reversed | rejected | cancelled | failed
    utr: Optional[str]
    status_reason: Optional[str]
    status_description: Optional[str]

    @property
    def is_success(self) -> bool:
        return self.status in SUCCESS_STATUSES

    @property
    def is_failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @property
    def failure_reason(self) -> str:
        return self.status_description or self.status_reason or self.status


def reference_for(withdrawal_request_id: int) -> str:
    return f"withdrawal_{withdrawal_request_id}"


def _key_pair() -> tuple[str, str]:
    if settings.RAZORPAYX_KEY_ID and settings.RAZORPAYX_KEY_SECRET:
        return settings.RAZORPAYX_KEY_ID, settings.RAZORPAYX_KEY_SECRET
    return settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET


def is_configured() -> bool:
    key_id, key_secret = _key_pair()
    return bool(key_id and key_secret and settings.RAZORPAY_ACCOUNT_NUMBER)


def environment() -> str:
    """'test' for rzp_test_ keys, otherwise 'prod'."""
    return "test" if _key_pair()[0].startswith("rzp_test_") else "prod"


def _require_configured() -> None:
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="RazorpayX payouts are not configured. Set the RazorpayX keys and account number.",
        )


def _headers(idempotency_key: Optional[str] = None) -> dict:
    key_id, key_secret = _key_pair()
    token = base64.b64encode(f"{key_id}:{key_secret}".encode()).decode()
    headers = {
        "Authorization": f"Basic {token}",
        "Content-Type": "application/json",
    }
    if idempotency_key:
        headers["X-Payout-Idempotency"] = idempotency_key
    return headers


def _error_description(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("description") or response.text
    except Exception:
        return response.text


def _request(
    method: str,
    path: str,
    payload: Optional[dict] = None,
    params: Optional[dict] = None,
    idempotency_key: Optional[str] = None,
) -> dict:
    """
    Call the Razorpay API. Raises HTTPException(502) on network error or
    non-2xx. httpx.TimeoutException is re-raised as-is so create_payout can
    resolve an unknown outcome.
    """
    url = f"{_RAZORPAY_BASE}{path}"
    try:
        response = httpx.request(
            method, url, json=payload, params=params,
            headers=_headers(idempotency_key), timeout=30,
        )
    except httpx.TimeoutException:
        raise
    except httpx.RequestError as exc:
        logger.error(f"Razorpay network error [{method} {path}]: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Razorpay. Please try again.",
        )

    if response.status_code not in (200, 201):
        detail = _error_description(response)
        logger.error(f"Razorpay error [{method} {path}] {response.status_code}: {response.text}")
        # Only admins reach the payout endpoints, so Razorpay's reason is shown as-is.
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Razorpay declined the request: {detail}",
        )

    return response.json()


def parse_payout(data: dict) -> PayoutResult:
    details = data.get("status_details") or {}
    return PayoutResult(
        payout_id=data.get("id", ""),
        reference_id=data.get("reference_id") or "",
        status=(data.get("status") or "").lower(),
        utr=data.get("utr"),
        status_reason=details.get("reason") or data.get("failure_reason"),
        status_description=details.get("description"),
    )


# ── Step 1: Contact ───────────────────────────────────────────────────────────

def get_or_create_contact(
    teacher_id: int,
    name: str,
    phone: Optional[str],
    email: Optional[str],
    existing_contact_id: Optional[str],
) -> str:
    """
    Return cached contact_id if available, otherwise create a Razorpay Contact.
    The caller persists the id to TeacherBankDetails.razorpay_contact_id.
    """
    if existing_contact_id:
        return existing_contact_id

    _require_configured()
    payload = {
        "name": (name or f"Teacher {teacher_id}")[:50],
        "type": "vendor",
        "reference_id": f"teacher_{teacher_id}",
    }
    if phone:
        digits = "".join(ch for ch in phone if ch.isdigit())
        if len(digits) >= 10:
            payload["contact"] = digits[-10:]
    if email:
        payload["email"] = email

    data = _request("POST", "/contacts", payload)
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
    Return cached fund_account_id if available, otherwise create a bank Fund
    Account on the contact. Teacher bank changes null the cached id
    (routers/teacher/withdrawals.py), forcing re-creation here.
    """
    if existing_fund_account_id:
        return existing_fund_account_id

    _require_configured()
    payload = {
        "contact_id": contact_id,
        "account_type": "bank_account",
        "bank_account": {
            "name": account_holder_name,
            "ifsc": ifsc_code.strip().upper(),
            "account_number": account_number.strip(),
        },
    }

    data = _request("POST", "/fund_accounts", payload)
    fund_account_id = data["id"]
    logger.info(f"Created Razorpay fund account {fund_account_id} for teacher {teacher_id}")
    return fund_account_id


# ── Step 3: Payout ────────────────────────────────────────────────────────────

def create_payout(
    withdrawal_request_id: int,
    fund_account_id: str,
    amount_inr: Decimal,
    narration: str = "MyMentor teacher payout",
) -> PayoutResult:
    """
    Create the money transfer. Inspect the returned status: Razorpay can answer
    with a terminal state straight away (e.g. processed in test mode).

    A timeout leaves the outcome unknown, so we look the payout up by
    reference_id. If it is still not visible we raise 502 and the withdrawal
    stays pending; retrying is safe because of the idempotency key.
    """
    _require_configured()

    amount_paise = int((amount_inr * 100).to_integral_value())
    if amount_paise < 100:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Payout amount must be at least ₹1.00.",
        )

    # IMPS is instant up to ₹5,00,000; NEFT above that.
    mode = "IMPS" if amount_paise <= 50_000_000 else "NEFT"
    reference_id = reference_for(withdrawal_request_id)

    payload = {
        "account_number": settings.RAZORPAY_ACCOUNT_NUMBER,
        "fund_account_id": fund_account_id,
        "amount": amount_paise,
        "currency": "INR",
        "mode": mode,
        "purpose": "payout",
        "queue_if_low_balance": True,   # wait for a top-up instead of failing
        "reference_id": reference_id,
        "narration": narration[:30],     # Razorpay limit: 30 chars, alphanumeric + spaces
        "notes": {"withdrawal_id": str(withdrawal_request_id)},
    }

    try:
        data = _request("POST", "/payouts", payload, idempotency_key=reference_id)
    except httpx.TimeoutException:
        logger.error(f"Razorpay payout create timed out for withdrawal {withdrawal_request_id}")
        existing = find_payout_by_reference(withdrawal_request_id)
        if existing:
            return existing
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Razorpay did not answer in time. Try Pay again; it will not pay twice.",
        )

    result = parse_payout(data)
    logger.info(
        f"Razorpay payout {result.payout_id} for withdrawal {withdrawal_request_id} "
        f"| ₹{amount_inr} | {mode} | {result.status}"
    )
    return result


# ── Reconciliation ────────────────────────────────────────────────────────────

def get_payout(payout_id: str) -> Optional[PayoutResult]:
    """Fetch a payout by its pout_ id. None if Razorpay has no such payout."""
    _require_configured()
    try:
        return parse_payout(_request("GET", f"/payouts/{payout_id}"))
    except httpx.TimeoutException:
        raise HTTPException(status_code=502, detail="Razorpay did not answer in time.")
    except HTTPException as exc:
        if "does not exist" in str(exc.detail).lower():
            return None
        raise


def find_payout_by_reference(withdrawal_request_id: int) -> Optional[PayoutResult]:
    """Look up the payout for a withdrawal by reference_id (newest first)."""
    _require_configured()
    try:
        data = _request(
            "GET", "/payouts",
            params={
                "account_number": settings.RAZORPAY_ACCOUNT_NUMBER,
                "reference_id": reference_for(withdrawal_request_id),
                "count": 1,
            },
        )
    except httpx.TimeoutException:
        return None
    items = data.get("items") or []
    return parse_payout(items[0]) if items else None


# ── Webhook ───────────────────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature: str) -> bool:
    """X-Razorpay-Signature = hex(HMAC_SHA256(raw body, RAZORPAYX_WEBHOOK_SECRET))."""
    secret = settings.RAZORPAYX_WEBHOOK_SECRET
    if not secret or not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)
