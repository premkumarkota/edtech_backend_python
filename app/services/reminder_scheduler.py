"""
Session Reminder Scheduler
Runs as a background thread inside the FastAPI process.

Every 2 minutes, scans for sessions starting in ~15 minutes and sends
push notifications to both the student and teacher.

The `reminder_sent` flag on VideoCallSession prevents duplicate sends
even if multiple app instances are running simultaneously.
"""
import logging
from datetime import datetime, timezone, timedelta

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import SessionLocal
from app.models.session import VideoCallSession, SessionStatus
from app.models.user import User
from app.utils.fcm import notify_session_reminder

logger = logging.getLogger(__name__)

# Window: sessions starting between 13 and 17 minutes from now
_REMIND_BEFORE_MINS = 15
_WINDOW_SECS        = 120   # ±2 min window (matches job interval)


def _send_reminders() -> None:
    """
    Find all booked sessions starting in ~15 minutes and
    send push notifications to student + teacher.
    Marks reminder_sent=True atomically to prevent duplicates.
    """
    db = SessionLocal()
    try:
        now        = datetime.now(timezone.utc)
        window_min = now + timedelta(minutes=_REMIND_BEFORE_MINS) - timedelta(seconds=_WINDOW_SECS)
        window_max = now + timedelta(minutes=_REMIND_BEFORE_MINS) + timedelta(seconds=_WINDOW_SECS)

        sessions = (
            db.query(VideoCallSession)
            .filter(
                VideoCallSession.status == SessionStatus.BOOKED.value,
                VideoCallSession.reminder_sent == False,           # noqa: E712
                VideoCallSession.scheduled_at >= window_min,
                VideoCallSession.scheduled_at <= window_max,
            )
            .with_for_update(skip_locked=True)   # Safe for multi-instance Cloud Run
            .all()
        )

        for sess in sessions:
            # Mark sent FIRST (before push) — prevents retry loop if push fails
            sess.reminder_sent = True
            db.flush()

            student = db.query(User).filter(User.id == sess.student_id).first()
            teacher = db.query(User).filter(User.id == sess.teacher_id).first()

            if student and student.fcm_token:
                notify_session_reminder(
                    fcm_token=student.fcm_token,
                    other_party_name=teacher.name if teacher else "your teacher",
                    session_id=sess.id,
                )

            if teacher and teacher.fcm_token:
                notify_session_reminder(
                    fcm_token=teacher.fcm_token,
                    other_party_name=student.name if student else "your student",
                    session_id=sess.id,
                )

            logger.info(
                f"Reminder sent for session {sess.id} at "
                f"{sess.scheduled_at.strftime('%d %b %Y %H:%M UTC')}"
            )

        if sessions:
            db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"Reminder job failed: {e}")
    finally:
        db.close()


# ── Public API ────────────────────────────────────────────────────────────────

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> None:
    """Start the background reminder scheduler. Called once at app startup."""
    global _scheduler
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _send_reminders,
        trigger=IntervalTrigger(minutes=2),
        id="session_reminders",
        replace_existing=True,
        max_instances=1,       # Never run two reminder jobs at once
        misfire_grace_time=60, # Allow up to 60s late if server was busy
    )
    _scheduler.start()
    logger.info("Session reminder scheduler started (every 2 min).")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler on app shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Session reminder scheduler stopped.")
