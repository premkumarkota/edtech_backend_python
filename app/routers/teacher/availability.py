"""
Teacher Availability & FCM Token
GET    /api/teacher/availability          → list my weekly schedule
POST   /api/teacher/availability          → add a time block
DELETE /api/teacher/availability/{id}     → remove a time block
POST   /api/teacher/availability/block-date → block a specific date (day off)
DELETE /api/teacher/availability/block-date/{date} → unblock a date
PUT    /api/teacher/fcm-token             → save device push token
"""
from datetime import date as date_type, datetime, timezone, timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.session import VideoCallSession, SessionStatus
from app.models.availability import TeacherAvailability, TeacherAvailabilityOverride
from app.schemas.availability import (
    AvailabilityCreateRequest,
    AvailabilityResponse,
    BlockDateRequest,
    FcmTokenRequest,
    InstantAvailabilityRequest,
    InstantAvailabilityResponse,
)

router = APIRouter()

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _instant_availability_payload(profile: TeacherProfile, teacher_id: int) -> dict:
    now = datetime.now(timezone.utc)
    is_live = bool(
        profile.is_available_now
        and profile.available_now_expires_at
        and profile.available_now_expires_at > now
    )
    return {
        "teacher_id": teacher_id,
        "is_available_now": is_live,
        "available_now_started_at": profile.available_now_started_at if is_live else None,
        "available_now_expires_at": profile.available_now_expires_at if is_live else None,
        "last_seen_at": profile.last_seen_at,
    }


# ── Weekly schedule ───────────────────────────────────────────────────────────

@router.get("", response_model=List[AvailabilityResponse])
def get_my_availability(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Return my weekly recurring availability blocks, ordered by day + start time."""
    return (
        db.query(TeacherAvailability)
        .filter(
            TeacherAvailability.teacher_id == current_user.id,
            TeacherAvailability.is_active == True,
        )
        .order_by(TeacherAvailability.day_of_week, TeacherAvailability.start_time)
        .all()
    )


@router.post("", response_model=AvailabilityResponse, status_code=201)
def add_availability_block(
    payload: AvailabilityCreateRequest,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Add a recurring weekly time block.
    E.g. every Monday 09:00–12:00 with 60-min slots.
    Duplicate day+start_time for the same teacher is rejected.
    """
    # Check for overlap with existing block on same day
    existing = (
        db.query(TeacherAvailability)
        .filter(
            TeacherAvailability.teacher_id == current_user.id,
            TeacherAvailability.day_of_week == payload.day_of_week,
            TeacherAvailability.is_active == True,
        )
        .all()
    )
    for block in existing:
        # Overlap: new block starts before existing ends AND ends after existing starts
        if payload.start_time < block.end_time and payload.end_time > block.start_time:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Time overlap with existing block on {DAY_NAMES[payload.day_of_week]} "
                    f"{block.start_time.strftime('%H:%M')}–{block.end_time.strftime('%H:%M')}"
                ),
            )

    block = TeacherAvailability(
        teacher_id=current_user.id,
        day_of_week=payload.day_of_week,
        start_time=payload.start_time,
        end_time=payload.end_time,
        slot_duration_mins=payload.slot_duration_mins,
    )
    db.add(block)
    db.commit()
    db.refresh(block)
    return block


@router.delete("/{block_id}", status_code=204)
def remove_availability_block(
    block_id: int,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Soft-delete (deactivate) an availability block."""
    block = db.query(TeacherAvailability).filter(
        TeacherAvailability.id == block_id,
        TeacherAvailability.teacher_id == current_user.id,
    ).first()
    if not block:
        raise HTTPException(status_code=404, detail="Availability block not found.")
    block.is_active = False
    db.commit()


# ── Instant availability ─────────────────────────────────────────────────────

@router.get("/instant", response_model=InstantAvailabilityResponse)
def get_instant_availability(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id,
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    payload = _instant_availability_payload(profile, current_user.id)
    if profile.is_available_now and not payload["is_available_now"]:
        profile.is_available_now = False
        db.commit()
    return payload


@router.put("/instant", response_model=InstantAvailabilityResponse)
def update_instant_availability(
    payload: InstantAvailabilityRequest,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id,
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher profile not found.")

    if profile.status != TeacherStatus.APPROVED:
        raise HTTPException(status_code=403, detail="Only approved teachers can go available now.")

    now = datetime.now(timezone.utc)
    active_session = db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == current_user.id,
        VideoCallSession.status == SessionStatus.IN_PROGRESS.value,
    ).first()
    if payload.is_available_now and active_session:
        raise HTTPException(status_code=400, detail="You are already in an active session.")

    profile.last_seen_at = now
    if payload.is_available_now:
        profile.is_available_now = True
        profile.available_now_started_at = now
        profile.available_now_expires_at = now + timedelta(minutes=payload.expires_in_mins)
    else:
        profile.is_available_now = False
        profile.available_now_started_at = None
        profile.available_now_expires_at = None

    db.commit()
    db.refresh(profile)
    return _instant_availability_payload(profile, current_user.id)


# ── Date overrides (block a day off) ─────────────────────────────────────────

@router.post("/block-date", status_code=201)
def block_date(
    payload: BlockDateRequest,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Mark a specific date as unavailable (holiday / day off)."""
    # Upsert — if already exists just ensure it's blocked
    override = db.query(TeacherAvailabilityOverride).filter(
        TeacherAvailabilityOverride.teacher_id == current_user.id,
        TeacherAvailabilityOverride.date == payload.date,
    ).first()

    if override:
        override.is_blocked = True
    else:
        override = TeacherAvailabilityOverride(
            teacher_id=current_user.id,
            date=payload.date,
            is_blocked=True,
        )
        db.add(override)

    db.commit()
    return {"message": f"{payload.date} blocked successfully."}


@router.delete("/block-date/{blocked_date}", status_code=200)
def unblock_date(
    blocked_date: date_type,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Remove a date block — teacher is available again on that day."""
    override = db.query(TeacherAvailabilityOverride).filter(
        TeacherAvailabilityOverride.teacher_id == current_user.id,
        TeacherAvailabilityOverride.date == blocked_date,
    ).first()
    if override:
        db.delete(override)
        db.commit()
    return {"message": f"{blocked_date} unblocked."}


@router.get("/block-date", response_model=List[date_type])
def get_blocked_dates(
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Return all upcoming blocked dates for the teacher."""
    today = date_type.today()
    overrides = (
        db.query(TeacherAvailabilityOverride)
        .filter(
            TeacherAvailabilityOverride.teacher_id == current_user.id,
            TeacherAvailabilityOverride.is_blocked == True,
            TeacherAvailabilityOverride.date >= today,
        )
        .order_by(TeacherAvailabilityOverride.date)
        .all()
    )
    return [o.date for o in overrides]


# ── FCM token ─────────────────────────────────────────────────────────────────

@router.put("/fcm-token")
def update_teacher_fcm_token(
    payload: FcmTokenRequest,
    current_user: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Called by the teacher app on every login to keep the push token fresh.
    Tokens change when the app is reinstalled or the device is reset.
    """
    current_user.fcm_token = payload.fcm_token
    db.commit()
    return {"message": "FCM token updated."}
