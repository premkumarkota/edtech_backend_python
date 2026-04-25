"""
Teacher — Session Management & Earnings
GET    /api/teacher/sessions/              My upcoming sessions
POST   /api/teacher/sessions/{id}/start    Mark session in-progress
POST   /api/teacher/sessions/{id}/end      End session → deduct minutes + create earning
GET    /api/teacher/earnings/              My earning history
GET    /api/teacher/earnings/summary       Monthly summary
"""
import uuid
import time as time_module
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, extract
from typing import List, Optional
from decimal import Decimal

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.session import VideoCallSession, SessionStatus
from app.models.instant_session import InstantSessionRequest, InstantSessionRequestStatus
from app.models.teacher_profile import TeacherProfile
from app.models.payout import TeacherEarning, TeacherRate
from app.schemas.session import (
    SessionResponse, EndSessionRequest, EndSessionResponse, JoinSessionResponse,
    CancelSessionRequest, CancelSessionResponse, NoShowRequest, NoShowResponse,
    InstantSessionRequestResponse, InstantSessionDeclineRequest,
)
from app.schemas.payout import TeacherEarningItem, TeacherEarningSummary
from app.services.payout_service import create_teacher_earning
from app.utils.fcm import (
    notify_student_session_cancelled,
    notify_student_incoming_call,
    notify_student_instant_session_accepted,
    notify_student_instant_session_declined,
    notify_student_instant_session_expired,
)
from app.config import settings

router = APIRouter()

AGORA_ROLE_PUBLISHER = 1
SESSION_OVERRUN_GRACE_MINS = 10
NO_SHOW_GRACE_MINS = 15


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
    stale = (
        db.query(InstantSessionRequest)
        .options(joinedload(InstantSessionRequest.student), joinedload(InstantSessionRequest.teacher))
        .filter(
            InstantSessionRequest.status == InstantSessionRequestStatus.REQUESTED.value,
            InstantSessionRequest.expires_at <= now,
        )
        .all()
    )
    for req in stale:
        req.status = InstantSessionRequestStatus.EXPIRED.value
        req.responded_at = now
        if req.student and req.student.fcm_token:
            notify_student_instant_session_expired(
                fcm_token=req.student.fcm_token,
                teacher_name=req.teacher.name if req.teacher else "Your tutor",
                request_id=req.id,
            )
    if stale:
        db.flush()


def _teacher_has_active_session(teacher_id: int, db: Session) -> bool:
    return db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == teacher_id,
        VideoCallSession.status == SessionStatus.IN_PROGRESS.value,
    ).first() is not None


@router.get("/rate")
def get_my_rate(
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher checks their approved hourly rate set by admin.
    Returns null if admin hasn't set a rate yet (pending approval).
    """
    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher.id).first()
    return {
        "teacher_id": teacher.id,
        "rate_per_hour": float(rate_row.rate_per_hour) if rate_row else None,
        "is_rate_set": rate_row is not None,
        "message": f"Your approved rate is ₹{rate_row.rate_per_hour}/hr" if rate_row else "Rate not set yet. Awaiting admin approval.",
    }


@router.get("/sessions", response_model=List[SessionResponse])
def list_my_sessions(
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """List all sessions assigned to this teacher."""
    return (
        db.query(VideoCallSession)
        .options(
            joinedload(VideoCallSession.student),
            joinedload(VideoCallSession.teacher),
        )
        .filter(VideoCallSession.teacher_id == teacher.id)
        .order_by(VideoCallSession.scheduled_at.desc())
        .all()
    )


@router.get("/sessions/instant-requests", response_model=List[InstantSessionRequestResponse])
def list_instant_requests(
    status: Optional[str] = None,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """List instant session requests sent to this teacher."""
    _expire_stale_instant_requests(db)
    db.commit()
    q = (
        db.query(InstantSessionRequest)
        .options(
            joinedload(InstantSessionRequest.student),
            joinedload(InstantSessionRequest.teacher),
        )
        .filter(InstantSessionRequest.teacher_id == teacher.id)
    )
    if status:
        q = q.filter(InstantSessionRequest.status == status)
    return q.order_by(InstantSessionRequest.requested_at.desc()).all()


@router.post("/sessions/instant-requests/{request_id}/accept", response_model=SessionResponse)
def accept_instant_request(
    request_id: int,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Accept a pending instant request and create a joinable session."""
    _expire_stale_instant_requests(db)
    req = (
        db.query(InstantSessionRequest)
        .filter(
            InstantSessionRequest.id == request_id,
            InstantSessionRequest.teacher_id == teacher.id,
        )
        .with_for_update()
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Instant request not found.")
    if req.status != InstantSessionRequestStatus.REQUESTED.value:
        raise HTTPException(status_code=400, detail=f"Request is not pending (status: {req.status}).")
    if _utc(req.expires_at) <= datetime.now(timezone.utc):
        req.status = InstantSessionRequestStatus.EXPIRED.value
        req.responded_at = datetime.now(timezone.utc)
        db.commit()
        raise HTTPException(status_code=400, detail="Instant request has expired.")
    if _teacher_has_active_session(teacher.id, db):
        raise HTTPException(status_code=400, detail="You are already in an active session.")

    session = VideoCallSession(
        student_id=req.student_id,
        teacher_id=req.teacher_id,
        subscription_id=req.subscription_id,
        scheduled_at=datetime.now(timezone.utc),
        scheduled_duration_mins=req.duration_mins,
        agora_channel_name=f"session_{uuid.uuid4().hex}",
        status=SessionStatus.BOOKED.value,
    )
    db.add(session)
    db.flush()

    req.status = InstantSessionRequestStatus.ACCEPTED.value
    req.session_id = session.id
    req.responded_at = datetime.now(timezone.utc)

    profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == teacher.id).first()
    if profile:
        profile.is_available_now = False
        profile.available_now_started_at = None
        profile.available_now_expires_at = None

    db.commit()
    db.refresh(session)

    if req.student and req.student.fcm_token:
        notify_student_instant_session_accepted(
            fcm_token=req.student.fcm_token,
            teacher_name=teacher.name or "Your tutor",
            request_id=req.id,
            session_id=session.id,
        )

    return (
        db.query(VideoCallSession)
        .options(joinedload(VideoCallSession.student), joinedload(VideoCallSession.teacher))
        .filter(VideoCallSession.id == session.id)
        .first()
    )


@router.post("/sessions/instant-requests/{request_id}/decline", response_model=InstantSessionRequestResponse)
def decline_instant_request(
    request_id: int,
    payload: InstantSessionDeclineRequest,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Decline a pending instant request."""
    req = (
        db.query(InstantSessionRequest)
        .filter(
            InstantSessionRequest.id == request_id,
            InstantSessionRequest.teacher_id == teacher.id,
        )
        .with_for_update()
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="Instant request not found.")
    if req.status != InstantSessionRequestStatus.REQUESTED.value:
        raise HTTPException(status_code=400, detail=f"Request is not pending (status: {req.status}).")

    req.status = InstantSessionRequestStatus.DECLINED.value
    req.decline_reason = payload.reason
    req.responded_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(req)

    if req.student and req.student.fcm_token:
        notify_student_instant_session_declined(
            fcm_token=req.student.fcm_token,
            teacher_name=teacher.name or "Your tutor",
            request_id=req.id,
            reason=payload.reason,
        )

    return req


@router.post("/sessions/{session_id}/join", response_model=JoinSessionResponse)
def join_session(
    session_id: int,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Get Agora token to join. Only allowed within 10 minutes before scheduled start."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.teacher_id == teacher.id,
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")

    if sess.status not in (SessionStatus.BOOKED.value, SessionStatus.IN_PROGRESS.value):
        raise HTTPException(status_code=400, detail="Session is not joinable.")

    now = datetime.now(timezone.utc)
    scheduled = sess.scheduled_at.replace(tzinfo=timezone.utc) if sess.scheduled_at.tzinfo is None else sess.scheduled_at
    if now < scheduled - timedelta(minutes=10):
        raise HTTPException(status_code=400, detail="Too early to join. Join within 10 minutes of the session.")

    # Mark in-progress on first join
    if sess.status == SessionStatus.BOOKED.value:
        sess.status = SessionStatus.IN_PROGRESS.value
        sess.started_at = now
        db.commit()
        db.refresh(sess)

        student = db.query(User).filter(User.id == sess.student_id).first()
        if student and student.fcm_token:
            notify_student_incoming_call(
                fcm_token=student.fcm_token,
                teacher_name=teacher.name or "Your tutor",
                session_id=sess.id,
            )

    agora_token = _generate_agora_token(sess.agora_channel_name, teacher.id)
    return JoinSessionResponse(
        session_id=sess.id,
        agora_app_id=settings.AGORA_APP_ID,
        agora_channel_name=sess.agora_channel_name,
        agora_token=agora_token,
        uid=teacher.id,
    )


@router.post("/sessions/{session_id}/start", response_model=SessionResponse)
def start_session(
    session_id: int,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Teacher joins the call — marks session as in_progress."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.teacher_id == teacher.id,
        VideoCallSession.status == SessionStatus.BOOKED.value,
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found or already started.")

    sess.status = SessionStatus.IN_PROGRESS.value
    sess.started_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(sess)
    return sess


@router.post("/sessions/{session_id}/cancel", response_model=CancelSessionResponse)
def cancel_session(
    session_id: int,
    payload: CancelSessionRequest,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Teacher cancels a booked session. No student penalty."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.teacher_id == teacher.id,
    ).with_for_update().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status != SessionStatus.BOOKED.value:
        raise HTTPException(status_code=400, detail="Only booked sessions can be cancelled.")

    now = datetime.now(timezone.utc)
    sess.status = SessionStatus.CANCELLED.value
    sess.cancelled_by = teacher.id
    sess.cancel_reason = payload.reason
    sess.cancelled_at = now
    sess.is_late_cancel = now >= _utc(sess.scheduled_at) - timedelta(minutes=30)
    sess.penalty_minutes = 0
    sess.refund_minutes = 0
    db.commit()

    student = db.query(User).filter(User.id == sess.student_id).first()
    if student and student.fcm_token:
        notify_student_session_cancelled(
            fcm_token=student.fcm_token,
            teacher_name=teacher.name or "Your tutor",
            session_date=sess.scheduled_at.strftime("%d %b %Y"),
            session_time=sess.scheduled_at.strftime("%I:%M %p"),
            session_id=sess.id,
        )

    return CancelSessionResponse(
        session_id=sess.id,
        status=sess.status,
        cancelled_by=teacher.id,
        cancelled_at=sess.cancelled_at,
        is_late_cancel=sess.is_late_cancel,
        penalty_minutes=0,
        student_minutes_remaining=sess.subscription.video_minutes_remaining,
        message="Session cancelled successfully.",
    )


@router.post("/sessions/{session_id}/no-show", response_model=NoShowResponse)
def mark_no_show(
    session_id: int,
    payload: NoShowRequest,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Mark a booked session as no-show after the grace window."""
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.teacher_id == teacher.id,
    ).with_for_update().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found.")
    if sess.status != SessionStatus.BOOKED.value:
        raise HTTPException(status_code=400, detail="Only booked sessions can be marked no-show.")

    allowed_at = _utc(sess.scheduled_at) + timedelta(minutes=NO_SHOW_GRACE_MINS)
    if datetime.now(timezone.utc) < allowed_at:
        raise HTTPException(status_code=400, detail="No-show can be marked after the grace window.")

    sess.status = SessionStatus.NO_SHOW.value
    sess.no_show_marked_by = teacher.id
    sess.no_show_reason = payload.reason
    db.commit()

    return NoShowResponse(
        session_id=sess.id,
        status=sess.status,
        no_show_marked_by=teacher.id,
        no_show_reason=payload.reason,
    )


@router.post("/sessions/{session_id}/end", response_model=EndSessionResponse)
def end_session(
    session_id: int,
    payload: EndSessionRequest,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Teacher ends the call.

    ONE ATOMIC TRANSACTION:
    1. Mark session completed + record actual duration
    2. Deduct minutes from student subscription
    3. Create teacher earning record (rate snapshot)
    """
    sess = db.query(VideoCallSession).filter(
        VideoCallSession.id == session_id,
        VideoCallSession.teacher_id == teacher.id,
        VideoCallSession.status == SessionStatus.IN_PROGRESS.value,
    ).with_for_update().first()
    if not sess:
        raise HTTPException(status_code=404, detail="Active session not found.")

    if sess.earning:
        raise HTTPException(status_code=409, detail="Session earning already exists.")

    sub = sess.subscription
    remaining_before = sub.video_call_minutes_total - sub.video_call_minutes_used
    if remaining_before <= 0:
        raise HTTPException(status_code=400, detail="Student has no video minutes remaining.")

    max_billable_mins = sess.scheduled_duration_mins + SESSION_OVERRUN_GRACE_MINS
    billable_mins = min(
        payload.actual_duration_mins,
        max_billable_mins,
        remaining_before,
    )

    if billable_mins < 1:
        raise HTTPException(status_code=400, detail="Billable session duration must be at least 1 minute.")

    # 1. Complete the session
    sess.status = SessionStatus.COMPLETED.value
    sess.ended_at = datetime.now(timezone.utc)
    sess.actual_duration_mins = billable_mins

    # 2. Deduct minutes from student subscription
    sub.video_call_minutes_used += billable_mins
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used

    # 3. Create teacher earning (rate snapshot + gross calculation)
    earning = create_teacher_earning(sess, billable_mins, db)
    db.flush()   # Get earning.id before commit

    db.commit()

    return EndSessionResponse(
        session_id=sess.id,
        actual_duration_mins=billable_mins,
        student_minutes_remaining=max(0, remaining),
        teacher_earning=float(earning.gross_earning),
    )


@router.get("/earnings", response_model=TeacherEarningSummary)
def my_earnings(
    month: Optional[str] = None,   # e.g. "2026-03"
    payout_status: Optional[str] = None,
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Get all earnings for the teacher.
    Optional filter by month (YYYY-MM) and payout status (pending/paid).
    """
    q = db.query(TeacherEarning).filter(TeacherEarning.teacher_id == teacher.id)

    if month:
        try:
            year, mo = int(month.split("-")[0]), int(month.split("-")[1])
            q = q.filter(
                extract("year", TeacherEarning.created_at) == year,
                extract("month", TeacherEarning.created_at) == mo,
            )
        except Exception:
            raise HTTPException(status_code=400, detail="month must be in YYYY-MM format")

    if payout_status:
        q = q.filter(TeacherEarning.payout_status == payout_status)

    earnings = q.order_by(TeacherEarning.created_at.desc()).all()

    total_pending = sum(
        e.gross_earning for e in earnings if e.payout_status == "pending"
    )
    total_paid = sum(
        e.gross_earning for e in earnings if e.payout_status == "paid"
    )

    return TeacherEarningSummary(
        total_pending=total_pending,
        total_paid=total_paid,
        total_sessions=len(earnings),
        earnings=earnings,
    )
