"""
Session completion — the one place a live session is settled.

Used by teacher End Session, student End Session, and the auto-close job for
abandoned calls, so all three bill identically:
  1. session → completed, actual_duration_mins = billable minutes
  2. student subscription minutes deducted
  3. teacher earning created (rate snapshot)

Billable minutes are capped at scheduled duration + overrun grace and at the
student's remaining minutes, so subscriptions never go negative.
"""
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.payout import TeacherEarning
from app.models.session import VideoCallSession, SessionStatus
from app.services.payout_service import create_teacher_earning

SESSION_OVERRUN_GRACE_MINS = 10


def minutes_remaining(sess: VideoCallSession) -> int:
    sub = sess.subscription
    return (sub.video_call_minutes_total - sub.video_call_minutes_used) if sub else 0


def billable_minutes(sess: VideoCallSession, duration_mins: int) -> int:
    return max(0, min(
        int(duration_mins),
        (sess.scheduled_duration_mins or 0) + SESSION_OVERRUN_GRACE_MINS,
        minutes_remaining(sess),
    ))


def complete_session(
    sess: VideoCallSession,
    duration_mins: int,
    db: Session,
    ended_at: Optional[datetime] = None,
) -> tuple[int, Optional[TeacherEarning]]:
    """Settle `sess` (caller holds the row lock and commits).
    Returns (billable_mins, earning or None when nothing was billable)."""
    billable = billable_minutes(sess, duration_mins)

    sess.status = SessionStatus.COMPLETED.value
    sess.ended_at = ended_at or datetime.now(timezone.utc)
    sess.actual_duration_mins = billable

    earning = None
    if billable >= 1 and not sess.earning:
        sess.subscription.video_call_minutes_used += billable
        earning = create_teacher_earning(sess, billable, db)
        db.flush()
    return billable, earning
