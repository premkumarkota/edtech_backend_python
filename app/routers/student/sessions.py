"""
Student — Video Call Session Booking & Joining
POST /api/student/sessions/book          Book a session with a teacher
GET  /api/student/sessions               My upcoming/past sessions
POST /api/student/sessions/{id}/join     Get Agora token to join
POST /api/student/sessions/{id}/cancel   Cancel a booking
"""
import uuid
import time as time_module
from datetime import datetime, date, time, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.models.session import VideoCallSession, SessionStatus
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.availability import TeacherAvailability, TeacherAvailabilityOverride
from app.schemas.session import (
    BookSessionRequest, SessionResponse, JoinSessionResponse,
)
from app.services.subscription_service import require_video_minutes
from app.utils.fcm import notify_teacher_session_booked, notify_teacher_session_cancelled
from app.config import settings

router = APIRouter()

# Agora role constants
AGORA_ROLE_PUBLISHER = 1


def _generate_agora_token(channel: str, uid: int) -> str:
    """Generate Agora RTC token valid for 2 hours."""
    from agora_token_builder import RtcTokenBuilder
    expire_ts = int(time_module.time()) + 7200  # 2 hours
    return RtcTokenBuilder.buildTokenWithUid(
        settings.AGORA_APP_ID,
        settings.AGORA_APP_CERTIFICATE,
        channel,
        uid,
        AGORA_ROLE_PUBLISHER,
        expire_ts,
    )


def _check_teacher_availability(
    teacher_id: int,
    scheduled_at: datetime,
    duration_mins: int,
    db: Session,
) -> None:
    """
    Raise HTTPException if the teacher is not available at the requested time.
    Checks:
      1. Date is not blocked (day off)
      2. scheduled_at falls inside a weekly availability block
      3. No existing BOOKED/IN_PROGRESS session overlaps this slot

    NOTE: Availability times are stored in IST (India Standard Time, UTC+5:30).
    All comparisons are done in IST regardless of how the client sends the datetime.
    """
    IST = timezone(timedelta(hours=5, minutes=30))
    local_date = scheduled_at.astimezone(IST).date()
    local_time = scheduled_at.astimezone(IST).time()
    slot_end_time = (
        datetime.combine(local_date, local_time) + timedelta(minutes=duration_mins)
    ).time()

    # 1. Check date override (blocked day off)
    override = db.query(TeacherAvailabilityOverride).filter(
        TeacherAvailabilityOverride.teacher_id == teacher_id,
        TeacherAvailabilityOverride.date == local_date,
        TeacherAvailabilityOverride.is_blocked == True,
    ).first()
    if override:
        raise HTTPException(
            status_code=400,
            detail="Teacher is not available on this date.",
        )

    # 2. Check weekly schedule — slot must fit inside at least one block
    day_of_week = local_date.weekday()  # 0=Mon … 6=Sun
    blocks = db.query(TeacherAvailability).filter(
        TeacherAvailability.teacher_id == teacher_id,
        TeacherAvailability.day_of_week == day_of_week,
        TeacherAvailability.is_active == True,
    ).all()

    if not blocks:
        raise HTTPException(
            status_code=400,
            detail="Teacher has no availability set for this day.",
        )

    slot_fits = any(
        b.start_time <= local_time and slot_end_time <= b.end_time
        for b in blocks
    )
    if not slot_fits:
        raise HTTPException(
            status_code=400,
            detail="Requested time is outside the teacher's available hours.",
        )

    # 3. Double-booking check — no overlap with existing sessions
    session_end = scheduled_at + timedelta(minutes=duration_mins)
    conflict = db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == teacher_id,
        VideoCallSession.status.in_([
            SessionStatus.BOOKED.value,
            SessionStatus.IN_PROGRESS.value,
        ]),
        VideoCallSession.scheduled_at < session_end,
        (VideoCallSession.scheduled_at +
         timedelta(minutes=1) * VideoCallSession.scheduled_duration_mins) > scheduled_at,
    ).first()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail="Teacher already has a session booked at this time. Please choose another slot.",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/book", response_model=SessionResponse, status_code=201)
def book_session(
    payload: BookSessionRequest,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Book a video call session.
    Validates: subscription minutes, teacher approval, availability, no double-booking.
    Sends FCM push notification to teacher on success.
    """
    # 1. Subscription check
    sub = require_video_minutes(student.id, db)
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used
    if remaining < payload.duration_mins:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient minutes. You have {remaining} mins remaining.",
        )

    # 2. Teacher exists and is approved
    teacher = db.query(User).filter(
        User.id == payload.teacher_id,
        User.role == UserRole.TEACHER,
        User.is_active == True,
    ).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found or inactive.")

    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == payload.teacher_id,
        TeacherProfile.status == TeacherStatus.APPROVED.value,
    ).first()
    if not teacher_profile:
        raise HTTPException(status_code=403, detail="Teacher is not yet approved.")

    # 3. Future time check
    if payload.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future.")

    # 4. Availability + double-booking check
    _check_teacher_availability(
        teacher_id=payload.teacher_id,
        scheduled_at=payload.scheduled_at,
        duration_mins=payload.duration_mins,
        db=db,
    )

    # 5. Create session
    channel_name = f"session_{uuid.uuid4().hex}"
    session = VideoCallSession(
        student_id=student.id,
        teacher_id=payload.teacher_id,
        subscription_id=sub.id,
        scheduled_at=payload.scheduled_at,
        scheduled_duration_mins=payload.duration_mins,
        agora_channel_name=channel_name,
        status=SessionStatus.BOOKED.value,
    )
    db.add(session)
    db.commit()
    db.refresh(session)

    # 6. Notify teacher via FCM (non-blocking — failure doesn't break booking)
    if teacher.fcm_token:
        notify_teacher_session_booked(
            fcm_token=teacher.fcm_token,
            student_name=student.name or "A student",
            session_date=payload.scheduled_at.strftime("%d %b %Y"),
            session_time=payload.scheduled_at.strftime("%I:%M %p"),
            duration_mins=payload.duration_mins,
            session_id=session.id,
        )

    return session


@router.get("", response_model=List[SessionResponse])
def list_my_sessions(
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """List all sessions for the current student (newest first)."""
    return (
        db.query(VideoCallSession)
        .filter(VideoCallSession.student_id == student.id)
        .order_by(VideoCallSession.scheduled_at.desc())
        .all()
    )


@router.post("/{session_id}/join", response_model=JoinSessionResponse)
def join_session(
    session_id: int,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Get Agora token to join. Only allowed within 10 minutes before scheduled start."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.student_id == student.id,
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status not in (SessionStatus.BOOKED.value, SessionStatus.IN_PROGRESS.value):
        raise HTTPException(status_code=400, detail=f"Session cannot be joined (status: {sess.status}).")

    earliest_join = sess.scheduled_at - timedelta(minutes=10)
    if datetime.now(timezone.utc) < earliest_join:
        raise HTTPException(
            status_code=400,
            detail=f"Too early to join. Session starts at {sess.scheduled_at.isoformat()}.",
        )

    agora_token = _generate_agora_token(sess.agora_channel_name, student.id)
    return JoinSessionResponse(
        session_id=sess.id,
        agora_channel_name=sess.agora_channel_name,
        agora_token=agora_token,
        uid=student.id,
    )


@router.post("/{session_id}/cancel")
def cancel_session(
    session_id: int,
    reason: str = "Student cancelled",
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Cancel a booked session. Notifies teacher via FCM."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.student_id == student.id,
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status != SessionStatus.BOOKED.value:
        raise HTTPException(status_code=400, detail="Only booked sessions can be cancelled.")

    sess.status = SessionStatus.CANCELLED.value
    sess.cancelled_by = student.id
    sess.cancel_reason = reason
    db.commit()

    # Notify teacher
    teacher = db.query(User).filter(User.id == sess.teacher_id).first()
    if teacher and teacher.fcm_token:
        notify_teacher_session_cancelled(
            fcm_token=teacher.fcm_token,
            student_name=student.name or "A student",
            session_date=sess.scheduled_at.strftime("%d %b %Y"),
            session_time=sess.scheduled_at.strftime("%I:%M %p"),
            session_id=sess.id,
        )

    return {"message": "Session cancelled successfully."}
