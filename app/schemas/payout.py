from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal


# ── Teacher Rate Schemas ─────────────────────────────────────────

class TeacherRateSet(BaseModel):
    rate_per_hour: Decimal

    @validator("rate_per_hour")
    def rate_positive(cls, v):
        if v < 0:
            raise ValueError("Rate must be non-negative")
        return v


class TeacherRateResponse(BaseModel):
    teacher_id: int
    teacher_name: Optional[str]
    rate_per_hour: Decimal
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Payout Schemas ───────────────────────────────────────────────

class TriggerPayoutRequest(BaseModel):
    period_from: date
    period_to: date
    notes: Optional[str] = None


class TeacherPayoutItem(BaseModel):
    teacher_id: int
    teacher_name: Optional[str]
    amount: Decimal
    sessions_count: int
    status: str
    transferred_at: Optional[datetime]

    class Config:
        from_attributes = True


class PayoutBatchResponse(BaseModel):
    id: int
    period_from: date
    period_to: date
    total_teachers: int
    total_amount: Decimal
    status: str
    notes: Optional[str]
    created_at: datetime
    completed_at: Optional[datetime]
    payouts: List[TeacherPayoutItem] = []

    class Config:
        from_attributes = True


class MarkPayoutTransferredRequest(BaseModel):
    notes: Optional[str] = None


# ── Earning Schemas (for teacher view) ───────────────────────────

class TeacherEarningItem(BaseModel):
    id: int
    session_id: int
    duration_mins: int
    rate_per_hour: Decimal
    gross_earning: Decimal
    payout_status: str
    created_at: datetime

    class Config:
        from_attributes = True


class TeacherEarningSummary(BaseModel):
    total_pending: Decimal
    total_paid: Decimal
    total_sessions: int
    earnings: List[TeacherEarningItem]
