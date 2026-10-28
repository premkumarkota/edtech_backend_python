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
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
from app.models.session import VideoCallSession, SessionStatus
from app.models.user import User
from app.utils.fcm import notify_session_reminder
from app.services.session_auto_close import auto_close_stale_sessions

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


def _settle_streaks() -> None:
    """Just after IST midnight: apply freezes / breaks for missed days."""
    from app.services.streak_service import nightly_settle_all
    db = SessionLocal()
    try:
        n = nightly_settle_all(db)
        db.commit()
        if n:
            logger.info(f"Streaks settled for {n} student(s)")
    except Exception as e:
        db.rollback()
        logger.error(f"Streak settle job failed: {e}")
    finally:
        db.close()


def _send_streak_reminders() -> None:
    """19:00 IST: one nudge to students whose live streak isn't safe yet today."""
    from app.services.streak_service import students_to_remind, today_ist
    from app.utils.fcm import notify_student_learning_streak_reminder
    db = SessionLocal()
    try:
        today = today_ist()
        sent = 0
        for user, streak in students_to_remind(db):
            streak.last_reminder_on = today   # mark first, like session reminders
            db.flush()
            if notify_student_learning_streak_reminder(
                user.fcm_token, streak.current_streak, user_id=user.id,
            ):
                sent += 1
        db.commit()
        if sent:
            logger.info(f"Streak reminders sent: {sent}")
    except Exception as e:
        db.rollback()
        logger.error(f"Streak reminder job failed: {e}")
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
    # Learning streaks: settle at 00:05 IST (18:35 UTC), remind at 19:00 IST (13:30 UTC).
    _scheduler.add_job(_settle_streaks, trigger=CronTrigger(hour=18, minute=35),
                       id="streak_settle", replace_existing=True,
                       max_instances=1, misfire_grace_time=3600)
    _scheduler.add_job(_send_streak_reminders, trigger=CronTrigger(hour=13, minute=30),
                       id="streak_reminder", replace_existing=True,
                       max_instances=1, misfire_grace_time=1800)
    # Completes live sessions the teacher left without tapping End Session.
    _scheduler.add_job(
        auto_close_stale_sessions,
        trigger=IntervalTrigger(minutes=2),
        id="session_auto_close",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )
    _scheduler.start()
    logger.info("Session reminder + auto-close scheduler started (every 2 min).")


def stop_scheduler() -> None:
    """Gracefully stop the scheduler on app shutdown."""
    global _scheduler
    if _scheduler and _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Session reminder scheduler stopped.")
