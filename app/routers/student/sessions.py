"""
Student — Video Call Session Booking & Joining
POST /api/student/sessions/book          Book a session with a teacher
GET  /api/student/sessions/              My upcoming/past sessions
POST /api/student/sessions/{id}/join     Get Agora token to join
POST /api/student/sessions/{id}/cancel   Cancel a booking
"""
import uuid
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.models.session import VideoCallSession, SessionStatus
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.schemas.session import (
    BookSessionRequest, SessionResponse, JoinSessionResponse, EndSessionResponse
)
from app.services.subscription_service import require_video_minutes

router = APIRouter()


def _generate_agora_token(channel: str, uid: int) -> str:
    """
    Generate Agora RTC token.
    In production replace with real Agora token builder.
    """
    try:
        # Production: use agora-token package
        # from agora_token_builder import RtcTokenBuilder, Role_Publisher
        # token = RtcTokenBuilder.buildTokenWithUid(
        #     APP_ID, APP_CERT, channel, uid, Role_Publisher, expire_ts
        # )
        # return token
        return f"agora_dev_token_{channel}_{uid}"  # Dev placeholder
    except Exception:
        return f"agora_dev_token_{channel}_{uid}"


@router.post("/book", response_model=SessionResponse, status_code=201)
def book_session(
    payload: BookSessionRequest,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Book a video call session.
    Requires active subscription with enough remaining minutes.
    """
    # 1. Check subscription entitlement
    sub = require_video_minutes(student.id, db)
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used
    if remaining < payload.duration_mins:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Insufficient minutes. You have {remaining} mins remaining, session needs {payload.duration_mins} mins.",
        )

    # 2. Verify teacher exists and is approved
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
        raise HTTPException(status_code=403, detail="Teacher is not yet approved for video calls.")

    # 3. Validate scheduled time is in the future
    if payload.scheduled_at <= datetime.now(timezone.utc):
        raise HTTPException(status_code=400, detail="scheduled_at must be in the future.")

    # 4. Generate unique Agora channel
    channel_name = f"session_{uuid.uuid4().hex}"

    # 5. Create session
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
    """
    Get Agora token to join the session.
    Only allowed within 10 minutes before scheduled start.
    """
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.student_id == student.id,
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status not in (SessionStatus.BOOKED.value, SessionStatus.IN_PROGRESS.value):
        raise HTTPException(status_code=400, detail=f"Session cannot be joined (status: {sess.status}).")

    # Allow join 10 minutes early
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
    """Cancel a booked session. Only allowed before it starts."""
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
    return {"message": "Session cancelled successfully."}
