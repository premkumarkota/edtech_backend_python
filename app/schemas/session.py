from pydantic import BaseModel, validator
from typing import Optional
from datetime import datetime


# ── Session Booking ──────────────────────────────────────────────

class BookSessionRequest(BaseModel):
    teacher_id: int
    scheduled_at: datetime
    duration_mins: int = 30

    @validator("duration_mins")
    def valid_duration(cls, v):
        if v not in (15, 30, 45, 60):
            raise ValueError("duration_mins must be 15, 30, 45, or 60")
        return v


class SessionResponse(BaseModel):
    id: int
    student_id: int
    student_name: Optional[str] = None
    teacher_id: int
    teacher_name: Optional[str] = None
    status: str
    agora_channel_name: Optional[str]
    scheduled_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    scheduled_duration_mins: int
    duration_mins: int
    actual_duration_mins: Optional[int]
    cancelled_at: Optional[datetime] = None
    cancelled_by: Optional[int] = None
    cancel_reason: Optional[str] = None
    is_late_cancel: bool = False
    penalty_minutes: int = 0
    refund_minutes: int = 0
    no_show_marked_by: Optional[int] = None
    no_show_reason: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class JoinSessionResponse(BaseModel):
    session_id: int
    agora_channel_name: str
    agora_token: str
    uid: int


class EndSessionRequest(BaseModel):
    actual_duration_mins: int

    @validator("actual_duration_mins")
    def positive_duration(cls, v):
        if v < 1:
            raise ValueError("Session duration must be at least 1 minute")
        return v


class EndSessionResponse(BaseModel):
    session_id: int
    actual_duration_mins: int
    student_minutes_remaining: int
    teacher_earning: float


class CancelSessionRequest(BaseModel):
    reason: str = "Cancelled"


class CancelSessionResponse(BaseModel):
    session_id: int
    status: str
    cancelled_by: int
    cancelled_at: datetime
    is_late_cancel: bool
    penalty_minutes: int
    student_minutes_remaining: Optional[int] = None
    message: str


class NoShowRequest(BaseModel):
    reason: str = "No show"


class NoShowResponse(BaseModel):
    session_id: int
    status: str
    no_show_marked_by: int
    no_show_reason: str


# ── Instant session requests ────────────────────────────────────────────────

class InstantSessionRequestCreate(BaseModel):
    teacher_id: int
    duration_mins: int = 30
    subject_context: Optional[str] = None

    @validator("duration_mins")
    def valid_duration(cls, v):
        if v not in (15, 30, 45, 60):
            raise ValueError("duration_mins must be 15, 30, 45, or 60")
        return v


class InstantSessionRequestResponse(BaseModel):
    id: int
    student_id: int
    student_name: Optional[str] = None
    teacher_id: int
    teacher_name: Optional[str] = None
    subscription_id: int
    session_id: Optional[int] = None
    status: str
    duration_mins: int
    subject_context: Optional[str] = None
    decline_reason: Optional[str] = None
    cancel_reason: Optional[str] = None
    requested_at: datetime
    expires_at: datetime
    responded_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class InstantSessionDeclineRequest(BaseModel):
    reason: str = "Teacher declined"
