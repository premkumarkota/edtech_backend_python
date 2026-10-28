"""
Auto-close abandoned live sessions.

A session only becomes `completed` when the teacher taps End Session
(`POST /api/teacher/sessions/{id}/end`). If the teacher left the call any other
way (back gesture, app killed, network drop) the session stayed `in_progress`
forever, and both apps kept offering "Rejoin" for a call that was over.

This job (run by the reminder scheduler every 2 minutes) completes any
`in_progress` session whose slot ended more than `_AUTO_CLOSE_AFTER_MINS` ago.
It settles the session the same way End Session does: it bills the booked
duration (capped by the student's remaining minutes) and creates the teacher
earning. That is what the teacher would have billed for a full-length session,
and End Session caps billing near that duration anyway.
"""
import logging
from datetime import datetime, timezone, timedelta

from app.database import SessionLocal
from app.models.session import VideoCallSession, SessionStatus
from app.services.session_completion import complete_session

logger = logging.getLogger(__name__)

# Past End Session's 10-min overrun grace, so a call that runs slightly long
# is never closed while the teacher can still end it.
_AUTO_CLOSE_AFTER_MINS = 30


def _utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def auto_close_stale_sessions() -> None:
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc)
        candidates = (
            db.query(VideoCallSession)
            .filter(VideoCallSession.status == SessionStatus.IN_PROGRESS.value)
            .with_for_update(skip_locked=True)   # Safe for multi-instance Cloud Run
            .all()
        )

        closed = 0
        for sess in candidates:
            start = _utc(sess.started_at or sess.scheduled_at)
            slot_end = start + timedelta(minutes=sess.scheduled_duration_mins or 0)
            if now < slot_end + timedelta(minutes=_AUTO_CLOSE_AFTER_MINS):
                continue

            # Bill the booked duration (see module docstring).
            billable, _ = complete_session(
                sess, sess.scheduled_duration_mins or 0, db, ended_at=slot_end,
            )
            closed += 1
            logger.info(
                "Auto-closed abandoned session %s (billed %s min)", sess.id, billable,
            )

        db.commit()
        if closed:
            logger.info("Auto-closed %s abandoned live session(s)", closed)
    except Exception as e:
        db.rollback()
        logger.error(f"Session auto-close job failed: {e}")
    finally:
        db.close()
