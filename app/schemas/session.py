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
    teacher_id: int
    status: str
    agora_channel_name: Optional[str]
    scheduled_at: datetime
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    scheduled_duration_mins: int
    actual_duration_mins: Optional[int]
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
