"""
Teacher — Session Management & Earnings
GET    /api/teacher/sessions/              My upcoming sessions
POST   /api/teacher/sessions/{id}/start    Mark session in-progress
POST   /api/teacher/sessions/{id}/end      End session → deduct minutes + create earning
GET    /api/teacher/earnings/              My earning history
GET    /api/teacher/earnings/summary       Monthly summary
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func, extract
from typing import List, Optional
from decimal import Decimal

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.session import VideoCallSession, SessionStatus
from app.models.payout import TeacherEarning
from app.schemas.session import SessionResponse, EndSessionRequest, EndSessionResponse
from app.schemas.payout import TeacherEarningItem, TeacherEarningSummary
from app.services.payout_service import create_teacher_earning

router = APIRouter()


@router.get("/sessions", response_model=List[SessionResponse])
def list_my_sessions(
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """List all sessions assigned to this teacher."""
    return (
        db.query(VideoCallSession)
        .filter(VideoCallSession.teacher_id == teacher.id)
        .order_by(VideoCallSession.scheduled_at.desc())
        .all()
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
    ).first()
    if not sess:
        raise HTTPException(status_code=404, detail="Active session not found.")

    actual_mins = payload.actual_duration_mins

    # 1. Complete the session
    sess.status = SessionStatus.COMPLETED.value
    sess.ended_at = datetime.now(timezone.utc)
    sess.actual_duration_mins = actual_mins

    # 2. Deduct minutes from student subscription
    sub = sess.subscription
    sub.video_call_minutes_used += actual_mins
    remaining = sub.video_call_minutes_total - sub.video_call_minutes_used

    # 3. Create teacher earning (rate snapshot + gross calculation)
    earning = create_teacher_earning(sess, actual_mins, db)
    db.flush()   # Get earning.id before commit

    db.commit()

    return EndSessionResponse(
        session_id=sess.id,
        actual_duration_mins=actual_mins,
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
