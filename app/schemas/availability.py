"""
Schemas for teacher availability and student slot viewing.
"""
from datetime import time, date, datetime
from typing import List, Optional
from pydantic import BaseModel, field_validator


# ── Teacher: set availability ─────────────────────────────────────────────────

class AvailabilityCreateRequest(BaseModel):
    """Add a recurring weekly time block."""
    day_of_week:        int         # 0=Mon … 6=Sun
    start_time:         time        # "09:00"
    end_time:           time        # "12:00"
    slot_duration_mins: int = 60    # length of each bookable slot

    @field_validator("day_of_week")
    @classmethod
    def validate_day(cls, v: int) -> int:
        if v < 0 or v > 6:
            raise ValueError("day_of_week must be 0 (Mon) to 6 (Sun)")
        return v

    @field_validator("end_time")
    @classmethod
    def validate_end_after_start(cls, end: time, info) -> time:
        start = info.data.get("start_time")
        if start and end <= start:
            raise ValueError("end_time must be after start_time")
        return end

    @field_validator("slot_duration_mins")
    @classmethod
    def validate_slot_duration(cls, v: int) -> int:
        if v < 15 or v > 480:
            raise ValueError("slot_duration_mins must be between 15 and 480")
        return v


class AvailabilityResponse(BaseModel):
    id:                 int
    teacher_id:         int
    day_of_week:        int
    start_time:         time
    end_time:           time
    slot_duration_mins: int
    is_active:          bool

    model_config = {"from_attributes": True}


# ── Teacher: block a date ────────────────────────────────────────────────────

class BlockDateRequest(BaseModel):
    date: date


# ── Student: view available slots ────────────────────────────────────────────

class TimeSlot(BaseModel):
    start:     str   # "09:00"
    end:       str   # "10:00"
    available: bool  # False if already booked


class TeacherSlotsResponse(BaseModel):
    teacher_id: int
    date:       date
    slots:      List[TimeSlot]


# ── FCM token update ─────────────────────────────────────────────────────────

class FcmTokenRequest(BaseModel):
    fcm_token: str


# ── Teacher: instant availability ────────────────────────────────────────────

class InstantAvailabilityRequest(BaseModel):
    is_available_now: bool
    expires_in_mins: int = 120

    @field_validator("expires_in_mins")
    @classmethod
    def validate_expiry(cls, v: int) -> int:
        if v < 15 or v > 480:
            raise ValueError("expires_in_mins must be between 15 and 480")
        return v


class InstantAvailabilityResponse(BaseModel):
    teacher_id: int
    is_available_now: bool
    available_now_started_at: Optional[datetime] = None
    available_now_expires_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
