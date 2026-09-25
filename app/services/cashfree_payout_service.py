"""
Cashfree Payouts Service (V2 API)
=================================
Teacher bank payouts via Cashfree Payouts. Replaces Razorpay X, which is not
available for our account. Student collections stay on Razorpay PG.

Flow (per withdrawal):
  create_transfer()  → POST /payout/transfers with beneficiary details inline
                       (no beneficiary objects to create/cache — a bank change
                       simply shows up on the next transfer).
  Webhook (TRANSFER_SUCCESS / FAILED / REJECTED / REVERSED) finalises status.
  get_transfer()     → GET /payout/transfers — used for reconciliation when a
                       webhook is missed or a create call timed out.

Idempotency:
  transfer_id = "withdrawal_{id}". Cashfree rejects a reused transfer_id with
  DUPLICATE_TRANSFER, so a withdrawal can never be paid twice. When our create
  call times out or returns DUPLICATE_TRANSFER we look up the existing transfer
  instead of failing the withdrawal.

Auth:
  x-client-id / x-client-secret (Payouts keys, not PG keys).
  Production also needs 2FA. Cloud Run has no static egress IP, so we use
  Public Key 2FA: x-cf-signature = base64(RSA-OAEP(clientId + "." + unix_ts)).

IMPORTANT:
- Amounts are in **rupees** (not paise), minimum ₹1.00.
- Cashfree returns HTTP 200 with status REJECTED/FAILED for business errors
  (insufficient balance, invalid account) — always inspect `status`.
"""
import base64
import hashlib
import hmac
import logging
import re
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

import httpx
from fastapi import HTTPException, status

from app.config import settings

logger = logging.getLogger(__name__)

_BASE_URLS = {
    "test": "https://sandbox.cashfree.com/payout",
    "prod": "https://api.cashfree.com/payout",
}
_API_VERSION = "2024-01-01"

# Terminal / near-terminal statuses returned by Cashfree
SUCCESS_STATUSES = {"SUCCESS"}
FAILED_STATUSES = {"FAILED", "REJECTED", "REVERSED", "MANUALLY_REJECTED"}


@dataclass
class TransferResult:
    transfer_id: str
    cf_transfer_id: Optional[str]
    status: str                 # RECEIVED | PENDING | QUEUED | SUCCESS | FAILED | REJECTED | REVERSED ...
    status_code: Optional[str]
    status_description: Optional[str]

    @property
    def is_success(self) -> bool:
        return self.status in SUCCESS_STATUSES

    @property
    def is_failed(self) -> bool:
        return self.status in FAILED_STATUSES

    @property
    def failure_reason(self) -> str:
        return self.status_description or self.status_code or self.status


def transfer_id_for(withdrawal_request_id: int) -> str:
    return f"withdrawal_{withdrawal_request_id}"


def is_configured() -> bool:
    return bool(settings.CASHFREE_PAYOUT_CLIENT_ID and settings.CASHFREE_PAYOUT_CLIENT_SECRET)


# ── Auth ──────────────────────────────────────────────────────────────────────

def _signature() -> Optional[str]:
    """Public Key 2FA signature. Valid for 5 minutes, so generate per request."""
    pem = settings.CASHFREE_PAYOUT_PUBLIC_KEY.strip()
    if not pem:
        return None
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    public_key = serialization.load_pem_public_key(pem.replace("\\n", "\n").encode())
    plain = f"{settings.CASHFREE_PAYOUT_CLIENT_ID}.{int(time.time())}".encode()
    encrypted = public_key.encrypt(
        plain,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )
    return base64.b64encode(encrypted).decode()


def _headers() -> dict:
    headers = {
        "x-client-id": settings.CASHFREE_PAYOUT_CLIENT_ID,
        "x-client-secret": settings.CASHFREE_PAYOUT_CLIENT_SECRET,
        "x-api-version": _API_VERSION,
        "Content-Type": "application/json",
    }
    signature = _signature()
    if signature:
        headers["x-cf-signature"] = signature
    return headers


def _base_url() -> str:
    env = settings.CASHFREE_PAYOUT_ENV.lower()
    return _BASE_URLS["prod" if env in ("prod", "production", "live") else "test"]


def _require_configured() -> None:
    if not is_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Cashfree Payouts is not configured. Contact platform admin.",
        )


def _to_result(data: dict, fallback_transfer_id: str) -> TransferResult:
    return TransferResult(
        transfer_id=data.get("transfer_id") or fallback_transfer_id,
        cf_transfer_id=str(data["cf_transfer_id"]) if data.get("cf_transfer_id") else None,
        status=(data.get("status") or "").upper(),
        status_code=data.get("status_code"),
        status_description=data.get("status_description"),
    )


# ── Sanitisers (Cashfree field rules) ─────────────────────────────────────────

def _clean_name(name: str) -> str:
    """beneficiary_name: only alphabets and whitespace."""
    cleaned = re.sub(r"[^A-Za-z ]", " ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:100] or "MyMentor Teacher"


def _clean_phone(phone: Optional[str]) -> Optional[str]:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-10:] if len(digits) >= 10 else None


def _clean_remarks(text: str) -> str:
    """transfer_remarks: alphanumeric and whitespace, max 70 chars."""
    return re.sub(r"[^A-Za-z0-9 ]", "", text)[:70]


# ── Transfer ──────────────────────────────────────────────────────────────────

def get_transfer(transfer_id: str) -> Optional[TransferResult]:
    """Fetch a transfer by our transfer_id. Returns None if Cashfree has no such transfer."""
    _require_configured()
    try:
        response = httpx.get(
            f"{_base_url()}/transfers",
            params={"transfer_id": transfer_id},
            headers=_headers(),
            timeout=30,
        )
    except httpx.RequestError as exc:
        logger.error(f"Cashfree network error [get_transfer {transfer_id}]: {exc}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Cashfree. Please try again.",
        )

    if response.status_code == 404:
        return None
    if response.status_code != 200:
        logger.error(
            f"Cashfree error [get_transfer {transfer_id}] {response.status_code}: {response.text}"
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Couldn't fetch the payout status from Cashfree. Try again shortly.",
        )
    return _to_result(response.json(), transfer_id)


def create_transfer(
    withdrawal_request_id: int,
    amount_inr: Decimal,
    account_holder_name: str,
    account_number: str,
    ifsc_code: str,
    phone: Optional[str],
    email: Optional[str],
    narration: str = "MyMentor teacher payout",
) -> TransferResult:
    """
    Send money to the teacher's bank account.

    Returns the TransferResult even when Cashfree synchronously rejects the
    transfer (status REJECTED/FAILED) — the caller decides how to record it.
    Raises HTTPException only when the outcome is unknown or the request was
    invalid, in which case nothing was debited and admin can retry safely.
    """
    _require_configured()

    if amount_inr < Decimal("1.00"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cashfree payout amount must be at least ₹1.00.",
        )

    transfer_id = transfer_id_for(withdrawal_request_id)
    beneficiary = {
        "beneficiary_name": _clean_name(account_holder_name),
        "beneficiary_instrument_details": {
            "bank_account_number": account_number.strip(),
            "bank_ifsc": ifsc_code.strip().upper(),
        },
        "beneficiary_contact_details": {},
    }
    if clean_phone := _clean_phone(phone):
        beneficiary["beneficiary_contact_details"]["beneficiary_phone"] = clean_phone
    if email:
        beneficiary["beneficiary_contact_details"]["beneficiary_email"] = email
    if not beneficiary["beneficiary_contact_details"]:
        del beneficiary["beneficiary_contact_details"]

    payload = {
        "transfer_id": transfer_id,
        "transfer_amount": float(amount_inr),
        "transfer_currency": "INR",
        "transfer_mode": "banktransfer",   # Cashfree picks IMPS/NEFT/RTGS
        "beneficiary_details": beneficiary,
        "transfer_remarks": _clean_remarks(narration),
    }

    try:
        response = httpx.post(
            f"{_base_url()}/transfers", json=payload, headers=_headers(), timeout=30
        )
    except httpx.RequestError as exc:
        # Outcome unknown — the transfer may have been accepted. Reconcile.
        logger.error(f"Cashfree network error [create_transfer {transfer_id}]: {exc}")
        existing = _safe_lookup(transfer_id)
        if existing:
            return existing
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach Cashfree. Please try again.",
        )

    if response.status_code >= 500:
        logger.error(
            f"Cashfree 5xx [create_transfer {transfer_id}] {response.status_code}: {response.text}"
        )
        existing = _safe_lookup(transfer_id)
        if existing:
            return existing
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Cashfree is having trouble right now. Please try again.",
        )

    if response.status_code == 409:
        # Live API answers a reused transfer_id with 409 transfer_id_already_exists
        # (the docs also mention HTTP 200 + DUPLICATE_TRANSFER, handled below).
        existing = get_transfer(transfer_id)
        if existing:
            logger.warning(f"Cashfree transfer {transfer_id} already exists — adopting it")
            return existing

    if response.status_code not in (200, 201, 202):
        logger.error(
            f"Cashfree error [create_transfer {transfer_id}] {response.status_code}: {response.text}"
        )
        try:
            message = response.json().get("message") or response.text
        except Exception:
            message = response.text
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Cashfree rejected this payout: {message}",
        )

    result = _to_result(response.json(), transfer_id)

    if result.status_code == "DUPLICATE_TRANSFER":
        # We already sent this withdrawal earlier (e.g. lost response). Use the original.
        existing = get_transfer(transfer_id)
        if existing:
            logger.warning(f"Cashfree duplicate transfer {transfer_id} — adopting existing transfer")
            return existing

    logger.info(
        f"Cashfree transfer {transfer_id} (cf={result.cf_transfer_id}) for withdrawal "
        f"{withdrawal_request_id} | ₹{amount_inr} | status={result.status}/{result.status_code}"
    )
    return result


def _safe_lookup(transfer_id: str) -> Optional[TransferResult]:
    try:
        return get_transfer(transfer_id)
    except HTTPException:
        return None


# ── Webhook ───────────────────────────────────────────────────────────────────

def verify_webhook_signature(raw_body: bytes, signature: str, timestamp: str) -> bool:
    """
    V2 webhook: base64(HMAC_SHA256(timestamp + raw_body, client_secret)).
    Headers: x-webhook-signature, x-webhook-timestamp.
    """
    secret = settings.CASHFREE_PAYOUT_CLIENT_SECRET
    if not secret or not signature or not timestamp:
        return False
    digest = hmac.new(
        secret.encode(), timestamp.encode() + raw_body, hashlib.sha256
    ).digest()
    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)


# ── Wallet balance ────────────────────────────────────────────────────────────
# Balance is only exposed on Cashfree's V1 API, which needs a short-lived
# bearer token from /v1/authorize (same client id/secret + 2FA signature).

_V1_HOSTS = {
    "test": "https://sandbox.cashfree.com",
    "prod": "https://payout-api.cashfree.com",
}


def get_wallet_balance() -> Optional[dict]:
    """
    Return {"available": Decimal, "ledger": Decimal} for the payout wallet,
    or None if Cashfree can't be reached. Never raises — it only feeds a
    dashboard figure, and must not break the withdrawals screen.
    """
    if not is_configured():
        return None
    env = settings.CASHFREE_PAYOUT_ENV.lower()
    host = _V1_HOSTS["prod" if env in ("prod", "production", "live") else "test"]
    try:
        auth_headers = {
            "X-Client-Id": settings.CASHFREE_PAYOUT_CLIENT_ID,
            "X-Client-Secret": settings.CASHFREE_PAYOUT_CLIENT_SECRET,
        }
        if signature := _signature():
            auth_headers["X-Cf-Signature"] = signature
        auth = httpx.post(f"{host}/payout/v1/authorize", headers=auth_headers, timeout=15).json()
        token = (auth.get("data") or {}).get("token")
        if not token:
            logger.warning(f"Cashfree authorize failed: {auth.get('subCode')} {auth.get('message')}")
            return None
        data = httpx.get(
            f"{host}/payout/v1.2/getBalance",
            headers={"Authorization": f"Bearer {token}"},
            timeout=15,
        ).json().get("data") or {}
        return {
            "available": Decimal(str(data.get("availableBalance", "0"))),
            "ledger": Decimal(str(data.get("balance", "0"))),
        }
    except Exception as exc:
        logger.warning(f"Cashfree balance lookup failed: {exc}")
        return None
