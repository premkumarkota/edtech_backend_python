"""
Admin — Session Monitoring
GET /api/admin/sessions
GET /api/admin/sessions/instant-requests
"""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.session import VideoCallSession
from app.models.instant_session import InstantSessionRequest

router = APIRouter()


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _session_payload(sess: VideoCallSession) -> dict:
    return {
        "id": sess.id,
        "student_id": sess.student_id,
        "student_name": sess.student.name if sess.student else None,
        "teacher_id": sess.teacher_id,
        "teacher_name": sess.teacher.name if sess.teacher else None,
        "subscription_id": sess.subscription_id,
        "status": sess.status,
        "scheduled_at": _iso(sess.scheduled_at),
        "started_at": _iso(sess.started_at),
        "ended_at": _iso(sess.ended_at),
        "scheduled_duration_mins": sess.scheduled_duration_mins,
        "actual_duration_mins": sess.actual_duration_mins,
        "cancelled_by": sess.cancelled_by,
        "cancel_reason": sess.cancel_reason,
        "cancelled_at": _iso(sess.cancelled_at),
        "is_late_cancel": sess.is_late_cancel,
        "penalty_minutes": sess.penalty_minutes,
        "refund_minutes": sess.refund_minutes,
        "no_show_marked_by": sess.no_show_marked_by,
        "no_show_reason": sess.no_show_reason,
        "created_at": _iso(sess.created_at),
    }


def _instant_request_payload(req: InstantSessionRequest) -> dict:
    return {
        "id": req.id,
        "student_id": req.student_id,
        "student_name": req.student.name if req.student else None,
        "teacher_id": req.teacher_id,
        "teacher_name": req.teacher.name if req.teacher else None,
        "subscription_id": req.subscription_id,
        "session_id": req.session_id,
        "status": req.status,
        "duration_mins": req.duration_mins,
        "subject_context": req.subject_context,
        "decline_reason": req.decline_reason,
        "cancel_reason": req.cancel_reason,
        "requested_at": _iso(req.requested_at),
        "expires_at": _iso(req.expires_at),
        "responded_at": _iso(req.responded_at),
        "created_at": _iso(req.created_at),
    }


@router.get("")
def list_sessions(
    status: Optional[str] = Query(None),
    teacher_id: Optional[int] = Query(None),
    student_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(VideoCallSession).options(
        joinedload(VideoCallSession.student),
        joinedload(VideoCallSession.teacher),
    )
    if status:
        q = q.filter(VideoCallSession.status == status)
    if teacher_id:
        q = q.filter(VideoCallSession.teacher_id == teacher_id)
    if student_id:
        q = q.filter(VideoCallSession.student_id == student_id)

    sessions = q.order_by(VideoCallSession.created_at.desc()).limit(limit).all()
    return [_session_payload(s) for s in sessions]


@router.get("/instant-requests")
def list_instant_requests(
    status: Optional[str] = Query(None),
    teacher_id: Optional[int] = Query(None),
    student_id: Optional[int] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(InstantSessionRequest).options(
        joinedload(InstantSessionRequest.student),
        joinedload(InstantSessionRequest.teacher),
    )
    if status:
        q = q.filter(InstantSessionRequest.status == status)
    if teacher_id:
        q = q.filter(InstantSessionRequest.teacher_id == teacher_id)
    if student_id:
        q = q.filter(InstantSessionRequest.student_id == student_id)

    requests = q.order_by(InstantSessionRequest.requested_at.desc()).limit(limit).all()
    return [_instant_request_payload(r) for r in requests]
