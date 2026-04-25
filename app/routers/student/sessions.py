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
from fastapi import APIRouter, Depends, HTTPException, Body, status
from sqlalchemy.orm import Session, joinedload
from typing import List

from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.models.session import VideoCallSession, SessionStatus
from app.models.instant_session import InstantSessionRequest, InstantSessionRequestStatus
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.availability import TeacherAvailability, TeacherAvailabilityOverride
from app.schemas.session import (
    BookSessionRequest, SessionResponse, JoinSessionResponse,
    CancelSessionRequest, CancelSessionResponse,
    InstantSessionRequestCreate, InstantSessionRequestResponse,
)
from app.services.subscription_service import require_video_minutes
from app.utils.fcm import (
    notify_teacher_session_booked,
    notify_teacher_session_cancelled,
    notify_teacher_instant_session_request,
)
from app.config import settings

router = APIRouter()

# Agora role constants
AGORA_ROLE_PUBLISHER = 1
INSTANT_REQUEST_EXPIRY_SECS = 60
LATE_CANCEL_WINDOW_MINS = 30
STUDENT_LATE_CANCEL_PENALTY_MINS = 5


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


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _expire_stale_instant_requests(db: Session) -> None:
    now = datetime.now(timezone.utc)
    stale = db.query(InstantSessionRequest).filter(
        InstantSessionRequest.status == InstantSessionRequestStatus.REQUESTED.value,
        InstantSessionRequest.expires_at <= now,
    ).all()
    for req in stale:
        req.status = InstantSessionRequestStatus.EXPIRED.value
        req.responded_at = now
    if stale:
        db.flush()


def _get_approved_teacher(teacher_id: int, db: Session) -> tuple[User, TeacherProfile]:
    teacher = db.query(User).filter(
        User.id == teacher_id,
        User.role == UserRole.TEACHER,
        User.is_active == True,
    ).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found or inactive.")

    teacher_profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == teacher_id,
        TeacherProfile.status == TeacherStatus.APPROVED.value,
    ).first()
    if not teacher_profile:
        raise HTTPException(status_code=403, detail="Teacher is not yet approved.")
    return teacher, teacher_profile


def _is_teacher_available_now(profile: TeacherProfile) -> bool:
    return bool(
        profile.is_available_now
        and profile.available_now_expires_at
        and _utc(profile.available_now_expires_at) > datetime.now(timezone.utc)
    )


def _has_active_teacher_session(teacher_id: int, db: Session) -> bool:
    return db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == teacher_id,
        VideoCallSession.status == SessionStatus.IN_PROGRESS.value,
    ).first() is not None


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
    session_start = (
        scheduled_at.replace(tzinfo=timezone.utc)
        if scheduled_at.tzinfo is None
        else scheduled_at
    )
    session_end = session_start + timedelta(minutes=duration_mins)
    active_sessions = db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == teacher_id,
        VideoCallSession.status.in_([
            SessionStatus.BOOKED.value,
            SessionStatus.IN_PROGRESS.value,
        ]),
    ).all()
    conflict = None
    for existing in active_sessions:
        existing_start = (
            existing.scheduled_at.replace(tzinfo=timezone.utc)
            if existing.scheduled_at.tzinfo is None
            else existing.scheduled_at
        )
        existing_end = existing_start + timedelta(
            minutes=existing.scheduled_duration_mins
        )
        if existing_start < session_end and existing_end > session_start:
            conflict = existing
            break
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
    teacher, _ = _get_approved_teacher(payload.teacher_id, db)

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
        .options(
            joinedload(VideoCallSession.student),
            joinedload(VideoCallSession.teacher),
        )
        .filter(VideoCallSession.student_id == student.id)
        .order_by(VideoCallSession.scheduled_at.desc())
        .all()
    )


@router.post("/instant/request", response_model=InstantSessionRequestResponse, status_code=201)
def request_instant_session(
    payload: InstantSessionRequestCreate,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Ask an Available Now teacher for an immediate session."""
    _expire_stale_instant_requests(db)

    sub = require_video_minutes(student.id, db)
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used
    if remaining < payload.duration_mins:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient minutes. You have {remaining} mins remaining.",
        )

    teacher, profile = _get_approved_teacher(payload.teacher_id, db)
    if not _is_teacher_available_now(profile):
        raise HTTPException(status_code=400, detail="Teacher is not available now.")
    if _has_active_teacher_session(payload.teacher_id, db):
        raise HTTPException(status_code=400, detail="Teacher is already in an active session.")

    existing_student_request = db.query(InstantSessionRequest).filter(
        InstantSessionRequest.student_id == student.id,
        InstantSessionRequest.status == InstantSessionRequestStatus.REQUESTED.value,
    ).first()
    if existing_student_request:
        raise HTTPException(status_code=409, detail="You already have a pending instant request.")

    existing_teacher_request = db.query(InstantSessionRequest).filter(
        InstantSessionRequest.teacher_id == payload.teacher_id,
        InstantSessionRequest.status == InstantSessionRequestStatus.REQUESTED.value,
    ).first()
    if existing_teacher_request:
        raise HTTPException(status_code=409, detail="Teacher already has a pending instant request.")

    now = datetime.now(timezone.utc)
    request = InstantSessionRequest(
        student_id=student.id,
        teacher_id=payload.teacher_id,
        subscription_id=sub.id,
        duration_mins=payload.duration_mins,
        subject_context=payload.subject_context,
        status=InstantSessionRequestStatus.REQUESTED.value,
        expires_at=now + timedelta(seconds=INSTANT_REQUEST_EXPIRY_SECS),
    )
    db.add(request)
    db.commit()
    db.refresh(request)

    if teacher.fcm_token:
        notify_teacher_instant_session_request(
            fcm_token=teacher.fcm_token,
            student_name=student.name or "A student",
            duration_mins=payload.duration_mins,
            request_id=request.id,
        )

    return request


@router.get("/instant/requests", response_model=List[InstantSessionRequestResponse])
def list_my_instant_requests(
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """List instant requests for the current student."""
    _expire_stale_instant_requests(db)
    db.commit()
    return (
        db.query(InstantSessionRequest)
        .options(
            joinedload(InstantSessionRequest.student),
            joinedload(InstantSessionRequest.teacher),
        )
        .filter(InstantSessionRequest.student_id == student.id)
        .order_by(InstantSessionRequest.requested_at.desc())
        .all()
    )


@router.post("/instant/requests/{request_id}/cancel", response_model=InstantSessionRequestResponse)
def cancel_instant_request(
    request_id: int,
    payload: CancelSessionRequest | None = Body(None),
    reason: str | None = None,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """Cancel a pending instant request before teacher accepts it."""
    req = db.query(InstantSessionRequest).filter(
        InstantSessionRequest.id == request_id,
        InstantSessionRequest.student_id == student.id,
    ).with_for_update().first()
    if not req:
        raise HTTPException(status_code=404, detail="Instant request not found.")
    if req.status != InstantSessionRequestStatus.REQUESTED.value:
        raise HTTPException(status_code=400, detail=f"Request cannot be cancelled (status: {req.status}).")

    req.status = InstantSessionRequestStatus.CANCELLED.value
    req.cancel_reason = payload.reason if payload else (reason or "Student cancelled instant request")
    req.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)
    return req


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

    scheduled = (
        sess.scheduled_at.replace(tzinfo=timezone.utc)
        if sess.scheduled_at.tzinfo is None
        else sess.scheduled_at
    )
    earliest_join = scheduled - timedelta(minutes=10)
    if datetime.now(timezone.utc) < earliest_join:
        raise HTTPException(
            status_code=400,
            detail=f"Too early to join. Session starts at {scheduled.isoformat()}.",
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
    payload: CancelSessionRequest | None = Body(None),
    reason: str | None = None,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
) -> CancelSessionResponse:
    """Cancel a booked session. Notifies teacher via FCM."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.student_id == student.id,
    ).with_for_update().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status != SessionStatus.BOOKED.value:
        raise HTTPException(status_code=400, detail="Only booked sessions can be cancelled.")

    now = datetime.now(timezone.utc)
    scheduled = _utc(sess.scheduled_at)
    is_late = now >= scheduled - timedelta(minutes=LATE_CANCEL_WINDOW_MINS)
    penalty = 0
    if is_late:
        sub = sess.subscription
        remaining = sub.video_call_minutes_total - sub.video_call_minutes_used
        penalty = min(STUDENT_LATE_CANCEL_PENALTY_MINS, remaining)
        sub.video_call_minutes_used += penalty

    sess.status = SessionStatus.CANCELLED.value
    sess.cancelled_by = student.id
    sess.cancel_reason = payload.reason if payload else (reason or "Student cancelled")
    sess.cancelled_at = now
    sess.is_late_cancel = is_late
    sess.penalty_minutes = penalty
    sess.refund_minutes = 0
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

    remaining_after = (
        sess.subscription.video_call_minutes_total
        - sess.subscription.video_call_minutes_used
    )
    return CancelSessionResponse(
        session_id=sess.id,
        status=sess.status,
        cancelled_by=student.id,
        cancelled_at=sess.cancelled_at,
        is_late_cancel=is_late,
        penalty_minutes=penalty,
        student_minutes_remaining=max(0, remaining_after),
        message="Session cancelled successfully.",
    )
