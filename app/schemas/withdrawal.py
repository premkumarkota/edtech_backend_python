"""
Withdrawal Pydantic Schemas

Used for:
- Teacher bank details save/view
- Teacher withdrawal request creation
- Admin withdrawal list/action responses
"""
from pydantic import BaseModel, field_validator
from typing import Optional
from datetime import datetime
from decimal import Decimal


# ── Bank Details ──────────────────────────────────────────────────────────────

class BankDetailsSave(BaseModel):
    """Teacher submits / updates bank account info."""
    account_holder_name: str
    account_number: str
    ifsc_code: str
    bank_name: Optional[str] = None
    upi_id: Optional[str] = None

    @field_validator("account_number")
    @classmethod
    def validate_account_number(cls, v: str) -> str:
        v = v.strip()
        if not v.isdigit() or len(v) < 9 or len(v) > 18:
            raise ValueError("Account number must be 9–18 digits")
        return v

    @field_validator("ifsc_code")
    @classmethod
    def validate_ifsc(cls, v: str) -> str:
        v = v.strip().upper()
        # IFSC format: 4 alpha + 0 + 6 alphanumeric  e.g. HDFC0001234
        if len(v) != 11 or not v[:4].isalpha() or v[4] != "0":
            raise ValueError("Invalid IFSC code format (expected: ABCD0123456)")
        return v

    @field_validator("account_holder_name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or len(v) > 200:
            raise ValueError("Account holder name must be 2–200 characters")
        return v


class BankDetailsResponse(BaseModel):
    """Returned to teacher — account number is partially masked."""
    id: int
    account_holder_name: str
    account_number_masked: str   # e.g. ****3456
    ifsc_code: str
    bank_name: Optional[str]
    upi_id: Optional[str]
    has_razorpay_fund_account: bool
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True


# ── Withdrawal Request ────────────────────────────────────────────────────────

class WithdrawalRequestCreate(BaseModel):
    """Teacher requests a withdrawal."""
    amount: Decimal

    @field_validator("amount")
    @classmethod
    def validate_amount(cls, v: Decimal) -> Decimal:
        if v < Decimal("100.00"):
            raise ValueError("Minimum withdrawal amount is ₹100")
        if v > Decimal("500000.00"):
            raise ValueError("Maximum single withdrawal is ₹5,00,000")
        return v.quantize(Decimal("0.01"))


class WithdrawalRequestResponse(BaseModel):
    """Returned to teacher for their own requests."""
    id: int
    amount: Decimal
    status: str
    bank_snapshot: Optional[str]    # JSON string — shown as-is to teacher
    failure_reason: Optional[str]
    rejection_reason: Optional[str]
    requested_at: datetime
    processed_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class WithdrawalBalanceResponse(BaseModel):
    """How much the teacher can withdraw right now."""
    available_balance: Decimal     # sum of pending TeacherEarning.gross_earning
    pending_sessions: int          # count of pending earning rows
    active_withdrawal: Optional[WithdrawalRequestResponse]  # if one is in flight
    minimum_withdrawal: Decimal = Decimal("100.00")


# ── Admin schemas ─────────────────────────────────────────────────────────────

class AdminWithdrawalResponse(BaseModel):
    """Full withdrawal detail shown to admin."""
    id: int
    teacher_id: int
    teacher_name: Optional[str]
    teacher_phone: Optional[str]
    amount: Decimal
    status: str
    bank_snapshot: Optional[str]
    razorpay_payout_id: Optional[str]
    admin_notes: Optional[str]
    rejection_reason: Optional[str]
    failure_reason: Optional[str]
    requested_at: datetime
    processed_at: Optional[datetime]
    completed_at: Optional[datetime]

    class Config:
        from_attributes = True


class AdminProcessWithdrawalRequest(BaseModel):
    """Admin body when triggering the Razorpay payout."""
    notes: Optional[str] = None


class AdminRejectWithdrawalRequest(BaseModel):
    """Admin body when rejecting a withdrawal request."""
    reason: str

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 5:
            raise ValueError("Rejection reason must be at least 5 characters")
        return v
