from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime
from decimal import Decimal


# ── Request Schemas ──────────────────────────────────────────────

class SubscriptionPlanCreate(BaseModel):
    name: str
    description: Optional[str] = None
    price: Decimal
    validity_days: int = 30
    mock_tests_allowed: int = 0              # 0 = unlimited
    video_call_minutes_per_month: int = 0    # 0 = no video access

    @validator("price")
    def price_positive(cls, v):
        if v <= 0:
            raise ValueError("Price must be positive")
        return v

    @validator("validity_days")
    def validity_positive(cls, v):
        if v <= 0:
            raise ValueError("validity_days must be positive")
        return v


class SubscriptionPlanUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    price: Optional[Decimal] = None
    validity_days: Optional[int] = None
    mock_tests_allowed: Optional[int] = None
    video_call_minutes_per_month: Optional[int] = None
    is_active: Optional[bool] = None


# ── Response Schemas ─────────────────────────────────────────────

class SubscriptionPlanResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: Decimal
    validity_days: int
    mock_tests_allowed: int
    video_call_minutes_per_month: int
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class SubscriptionStatusResponse(BaseModel):
    """Returned to the student after a successful purchase or on status check."""
    has_active_subscription: bool
    subscription_id: Optional[int] = None
    plan: Optional[SubscriptionPlanResponse] = None
    expires_at: Optional[datetime] = None
    days_remaining: Optional[int] = None
    mock_tests_allowed: Optional[int] = None       # None = unlimited
    mock_tests_used: Optional[int] = None
    mock_tests_remaining: Optional[int] = None     # None = unlimited
    video_call_minutes_total: Optional[int] = None
    video_call_minutes_used: Optional[int] = None
    video_call_minutes_remaining: Optional[int] = None


# ── Payment Schemas ──────────────────────────────────────────────

class CreateOrderRequest(BaseModel):
    plan_id: int


class CreateOrderResponse(BaseModel):
    razorpay_order_id: str
    amount: int               # In paise (multiply rupees by 100)
    currency: str = "INR"
    key_id: str               # Razorpay Key ID (public) — safe to send to app
    subscription_id: int
    plan_name: str


class VerifyPaymentRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class StudentSubscriptionResponse(BaseModel):
    id: int
    student_id: int
    plan_id: int
    status: str
    started_at: Optional[datetime]
    expires_at: Optional[datetime]
    mock_tests_allowed: int
    mock_tests_used: int
    video_call_minutes_total: int
    video_call_minutes_used: int
    amount_paid: Optional[Decimal]
    created_at: datetime

    class Config:
        from_attributes = True
