"""
AI Study Plan Daily Notification Scheduler.

Runs every 30 minutes, checks for students whose preferred reminder time
has arrived, generates their daily plan, and sends a push notification.

When STUDY_PLANNER_V2_CANONICAL=1 (default), this scheduler is not started
from main.py — Study Planner V2 owns reminders instead. The guard below is a
safety net if the scheduler is started manually.
"""
import logging
import os
from datetime import datetime, timezone, timedelta, time as dt_time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.ai_study_planner import AiStudentPreference, AiStudyPlan
from app.services.ai_study_planner_service import generate_daily_plan
from app.utils.fcm import send_push

logger = logging.getLogger(__name__)

# IST offset (UTC+5:30) — most students are in India
_IST_OFFSET = timedelta(hours=5, minutes=30)


def _v2_canonical_reminders() -> bool:
    return os.environ.get("STUDY_PLANNER_V2_CANONICAL", "1").strip().lower() in (
        "1", "true", "yes",
    )


def _send_study_plan_reminders() -> None:
    """
    Find students whose reminder_time matches the current time (±15 min window).
    Generate daily plan if not already generated, and send FCM push.
    """
    # V2 study planner scheduler sends session/streak reminders — skip V1 pushes.
    if _v2_canonical_reminders():
        return

    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        # Convert to IST for time matching (students set time in local IST)
        now_ist = now_utc + _IST_OFFSET
        current_time = now_ist.time()
        today = now_ist.date()

        # Find preferences where reminder_time is within ±15 min of now
        # Simple approach: query all enabled preferences and filter in Python
        # (small table, efficient enough for MVP)
        prefs = (
            db.query(AiStudentPreference)
            .filter(
                AiStudentPreference.is_enabled == True,  # noqa: E712
            )
            .all()
        )

        for pref in prefs:
            reminder_time = pref.reminder_time or dt_time(8, 0)

            # Check if current time is within 15 minutes of reminder time
            reminder_dt = datetime.combine(today, reminder_time)
            current_dt = datetime.combine(today, current_time)
            diff_mins = abs((current_dt - reminder_dt).total_seconds() / 60)

            if diff_mins > 15:
                continue

            # Check if plan already generated today
            existing_plan = (
                db.query(AiStudyPlan)
                .filter(
                    AiStudyPlan.student_id == pref.student_id,
                    AiStudyPlan.plan_date == today,
                )
                .first()
            )

            if existing_plan:
                # Plan exists — just send reminder if not already sent
                # (We use a simple check: if plan was generated more than 30 min ago, skip)
                if existing_plan.generated_at:
                    age_mins = (now_utc - existing_plan.generated_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
                    if age_mins < 35:
                        continue  # Already sent recently
                summary = existing_plan.summary or "Your study plan is ready!"
            else:
                # Generate new plan
                student = db.query(User).filter(User.id == pref.student_id).first()
                if not student or student.role != UserRole.STUDENT.value:
                    continue

                plan_data = generate_daily_plan(student, db, pref.daily_study_hours)
                if not plan_data:
                    continue

                # Save plan
                new_plan = AiStudyPlan(
                    student_id=pref.student_id,
                    plan_date=today,
                    tasks=plan_data.get("tasks", []),
                    summary=plan_data.get("summary", "Your study plan is ready!"),
                )
                db.add(new_plan)
                db.flush()

                summary = plan_data.get("summary", "Your study plan is ready!")

            # Send FCM push
            student = db.query(User).filter(User.id == pref.student_id).first()
            if student and student.fcm_token:
                send_push(
                    fcm_token=student.fcm_token,
                    title="📚 Today's Study Plan",
                    body=summary,
                    data={
                        "type": "study_plan_reminder",
                        "date": str(today),
                    },
                )
                logger.info(f"Study plan reminder sent to student {pref.student_id}")

        db.commit()

    except Exception as e:
        db.rollback()
        logger.error(f"AI plan scheduler failed: {e}")
    finally:
        db.close()


# ── Public API ────────────────────────────────────────────────────────────────

_ai_scheduler: BackgroundScheduler | None = None


def start_ai_plan_scheduler() -> None:
    """Start the AI study plan reminder scheduler. Called once at app startup."""
    global _ai_scheduler
    _ai_scheduler = BackgroundScheduler(timezone="UTC")
    _ai_scheduler.add_job(
        _send_study_plan_reminders,
        trigger=IntervalTrigger(minutes=30),
        id="ai_study_plan_reminders",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=120,
    )
    _ai_scheduler.start()
    logger.info("AI Study Plan reminder scheduler started (every 30 min).")


def stop_ai_plan_scheduler() -> None:
    """Gracefully stop the AI plan scheduler on app shutdown."""
    global _ai_scheduler
    if _ai_scheduler and _ai_scheduler.running:
        _ai_scheduler.shutdown(wait=False)
        logger.info("AI Study Plan reminder scheduler stopped.")
