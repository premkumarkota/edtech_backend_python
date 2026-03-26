"""
Razorpay Service
Handles order creation, HMAC signature verification,
and webhook signature verification.
"""
import hmac
import hashlib
from decimal import Decimal
from fastapi import HTTPException, status
from app.config import settings


def get_razorpay_client():
    """Get authenticated Razorpay client."""
    try:
        import razorpay
    except ImportError:
        raise RuntimeError("razorpay package not installed. Run: pip install razorpay")
    return razorpay.Client(
        auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET)
    )


def create_razorpay_order(amount_inr: Decimal, receipt: str, notes: dict = None) -> dict:
    """
    Create a Razorpay order.
    amount_inr: price in Indian Rupees (will be converted to paise internally).
    Returns the full Razorpay order dict.
    """
    client = get_razorpay_client()
    amount_paise = int(amount_inr * 100)   # Razorpay expects paise
    order_data = {
        "amount": amount_paise,
        "currency": "INR",
        "receipt": receipt,
        "payment_capture": 1,              # Auto-capture — no manual step needed
        "notes": notes or {},
    }
    try:
        order = client.order.create(order_data)
        return order
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to create Razorpay order: {str(e)}"
        )


def verify_payment_signature(
    razorpay_order_id: str,
    razorpay_payment_id: str,
    razorpay_signature: str,
) -> bool:
    """
    Verify HMAC-SHA256 signature from the Razorpay SDK callback.
    Called after the student pays in-app.
    """
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected_signature = hmac.new(
        settings.RAZORPAY_KEY_SECRET.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected_signature, razorpay_signature)


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    """
    Verify HMAC-SHA256 signature from Razorpay webhook.
    Uses RAZORPAY_WEBHOOK_SECRET (different from key secret).
    """
    expected = hmac.new(
        settings.RAZORPAY_WEBHOOK_SECRET.encode("utf-8"),
        body,
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
