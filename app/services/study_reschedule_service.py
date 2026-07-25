"""
Study Reschedule Service — Nightly cron for auto-rescheduling,
streak calculation, and badge awarding.
"""
import logging
from datetime import datetime, timezone, timedelta, date, time as dt_time
from collections import defaultdict

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import SessionLocal
from app.models.user import User, UserRole
from app.models.study_planner_v2 import (
    StudyGoal, StudyCalendarEntry, StudyTimeSlot,
    StudyStreak, StudyBadge, StudyMcqAttempt,
)

logger = logging.getLogger(__name__)

_IST_OFFSET = timedelta(hours=5, minutes=30)

# Badge definitions
_STREAK_BADGES = {
    7: ("streak_7", "Week Warrior", "flame_7"),
    30: ("streak_30", "Monthly Master", "flame_30"),
    100: ("streak_100", "Century Club", "flame_100"),
}

_MAX_RESCHEDULE_PER_TOPIC = 2


def _reschedule_missed_sessions(db) -> int:
    """
    Find pending sessions from past dates and reschedule them.
    Returns count of rescheduled sessions.
    """
    now_ist = datetime.now(timezone.utc) + _IST_OFFSET
    today_ist = now_ist.date()

    # Find missed (pending sessions from past dates)
    missed = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.plan_date < today_ist,
            StudyCalendarEntry.status == "pending",
            StudyGoal.status == "active",
        )
        .all()
    )

    rescheduled_count = 0

    for entry in missed:
        # Mark as skipped
        entry.status = "skipped"

        # Count how many times this chapter has been rescheduled
        reschedule_count = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == entry.goal_id,
                StudyCalendarEntry.chapter_id == entry.chapter_id,
                StudyCalendarEntry.is_revision == False,
                StudyCalendarEntry.original_date.isnot(None),
            )
            .count()
        )

        if reschedule_count >= _MAX_RESCHEDULE_PER_TOPIC:
            continue  # Stop rescheduling after max attempts

        # Find next available date
        goal = db.query(StudyGoal).filter(StudyGoal.id == entry.goal_id).first()
        if not goal:
            continue

        time_slots = goal.time_slots
        if not time_slots:
            continue

        slots_per_day = len(time_slots)

        # Find the latest date with entries
        last_entry = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == goal.id,
                StudyCalendarEntry.status == "pending",
            )
            .order_by(StudyCalendarEntry.plan_date.desc(), StudyCalendarEntry.slot_order.desc())
            .first()
        )

        if last_entry:
            # Check if last day has room for another slot
            entries_on_last_day = (
                db.query(StudyCalendarEntry)
                .filter(
                    StudyCalendarEntry.goal_id == goal.id,
                    StudyCalendarEntry.plan_date == last_entry.plan_date,
                    StudyCalendarEntry.status == "pending",
                )
                .count()
            )

            if entries_on_last_day < slots_per_day:
                new_date = last_entry.plan_date
                new_slot_order = entries_on_last_day
            else:
                new_date = last_entry.plan_date + timedelta(days=1)
                new_slot_order = 0
        else:
            new_date = today_ist
            new_slot_order = 0

        if new_date > goal.target_date:
            # Can't reschedule past deadline — just skip
            continue

        # Determine slot label
        sorted_slots = sorted(time_slots, key=lambda s: (s.start_time.hour, s.start_time.minute))
        slot = sorted_slots[new_slot_order % len(sorted_slots)]

        new_entry = StudyCalendarEntry(
            goal_id=entry.goal_id,
            plan_date=new_date,
            slot_label=slot.label,
            slot_order=new_slot_order,
            syllabus_id=entry.syllabus_id,
            chapter_id=entry.chapter_id,
            topic_title=entry.topic_title,
            subject_name=entry.subject_name,
            chapter_name=entry.chapter_name,
            difficulty=entry.difficulty,
            duration_mins=entry.duration_mins,
            content_ids=entry.content_ids,
            status="pending",
            is_revision=False,
            original_date=entry.plan_date,
        )
        db.add(new_entry)
        rescheduled_count += 1

    return rescheduled_count


def _schedule_revision_sessions(db) -> int:
    """
    Schedule revision sessions for topics where MCQ was failed.
    Places revision 2-3 days after the original attempt.
    """
    now_ist = datetime.now(timezone.utc) + _IST_OFFSET
    today_ist = now_ist.date()

    # Find completed entries with MCQ fail that don't already have a revision scheduled
    failed_entries = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.mcq_passed == False,
            StudyCalendarEntry.status == "completed",
            StudyCalendarEntry.is_revision == False,
            StudyGoal.status == "active",
        )
        .all()
    )

    revision_count = 0

    for entry in failed_entries:
        # Check if revision already exists
        existing_revision = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == entry.goal_id,
                StudyCalendarEntry.chapter_id == entry.chapter_id,
                StudyCalendarEntry.is_revision == True,
                StudyCalendarEntry.status == "pending",
            )
            .first()
        )

        if existing_revision:
            continue

        # Schedule revision 3 days from now (or from completion date)
        base_date = entry.completed_at.date() if entry.completed_at else today_ist
        revision_date = base_date + timedelta(days=3)

        if revision_date < today_ist:
            revision_date = today_ist + timedelta(days=1)

        goal = db.query(StudyGoal).filter(StudyGoal.id == entry.goal_id).first()
        if not goal or revision_date > goal.target_date:
            continue

        sorted_slots = sorted(goal.time_slots, key=lambda s: (s.start_time.hour, s.start_time.minute))
        if not sorted_slots:
            continue

        new_entry = StudyCalendarEntry(
            goal_id=entry.goal_id,
            plan_date=revision_date,
            slot_label=sorted_slots[0].label,
            slot_order=0,
            syllabus_id=entry.syllabus_id,
            chapter_id=entry.chapter_id,
            topic_title=f"[Revision] {entry.topic_title}",
            subject_name=entry.subject_name,
            chapter_name=entry.chapter_name,
            difficulty=entry.difficulty,
            duration_mins=entry.duration_mins,
            content_ids=entry.content_ids,
            status="pending",
            is_revision=True,
            original_date=entry.plan_date,
        )
        db.add(new_entry)
        revision_count += 1

    return revision_count


def update_streak(student_id: int, db) -> StudyStreak:
    """
    Update streak for a student. Called after completing a session
    or during nightly cron.
    """
    now_ist = datetime.now(timezone.utc) + _IST_OFFSET
    today_ist = now_ist.date()
    yesterday = today_ist - timedelta(days=1)

    streak = db.query(StudyStreak).filter(StudyStreak.student_id == student_id).first()
    if not streak:
        streak = StudyStreak(student_id=student_id, current_streak=0, longest_streak=0)
        db.add(streak)
        db.flush()

    # Check if student studied today
    studied_today = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyGoal.student_id == student_id,
            StudyCalendarEntry.status == "completed",
            StudyCalendarEntry.completed_at.isnot(None),
        )
        .all()
    )

    # Filter by IST date
    today_completed = any(
        (e.completed_at.replace(tzinfo=timezone.utc) + _IST_OFFSET).date() == today_ist
        for e in studied_today
        if e.completed_at
    )

    if today_completed:
        if streak.last_study_date == yesterday:
            streak.current_streak += 1
        elif streak.last_study_date == today_ist:
            pass  # Already counted today
        else:
            streak.current_streak = 1

        streak.last_study_date = today_ist
        streak.longest_streak = max(streak.longest_streak, streak.current_streak)
    else:
        # During nightly cron: if yesterday wasn't studied, reset
        if streak.last_study_date and streak.last_study_date < yesterday:
            streak.current_streak = 0

    streak.updated_at = datetime.now(timezone.utc)
    db.flush()
    return streak


def check_and_award_badges(student_id: int, goal_id: int, db) -> list[dict]:
    """
    Check badge conditions and award any new badges.
    Returns list of newly awarded badge dicts.
    """
    new_badges = []

    streak = db.query(StudyStreak).filter(StudyStreak.student_id == student_id).first()

    # Streak badges
    if streak:
        for threshold, (badge_type, badge_name, badge_icon) in _STREAK_BADGES.items():
            if streak.current_streak >= threshold:
                existing = db.query(StudyBadge).filter(
                    StudyBadge.student_id == student_id,
                    StudyBadge.badge_type == badge_type,
                ).first()
                if not existing:
                    badge = StudyBadge(
                        student_id=student_id,
                        goal_id=goal_id,
                        badge_type=badge_type,
                        badge_name=badge_name,
                        badge_icon=badge_icon,
                    )
                    db.add(badge)
                    new_badges.append({"badge_type": badge_type, "badge_name": badge_name, "badge_icon": badge_icon})

    # Goal completion badge
    goal = db.query(StudyGoal).filter(StudyGoal.id == goal_id).first()
    if goal and goal.status == "completed":
        existing = db.query(StudyBadge).filter(
            StudyBadge.student_id == student_id,
            StudyBadge.badge_type == "goal_complete",
            StudyBadge.goal_id == goal_id,
        ).first()
        if not existing:
            badge = StudyBadge(
                student_id=student_id,
                goal_id=goal_id,
                badge_type="goal_complete",
                badge_name="Goal Crusher",
                badge_icon="trophy",
            )
            db.add(badge)
            new_badges.append({"badge_type": "goal_complete", "badge_name": "Goal Crusher", "badge_icon": "trophy"})

    # Quiz master: avg score >= 80 across at least 10 MCQ attempts
    attempts = (
        db.query(StudyMcqAttempt)
        .filter(StudyMcqAttempt.student_id == student_id)
        .order_by(StudyMcqAttempt.attempted_at.desc())
        .limit(10)
        .all()
    )
    if len(attempts) >= 10:
        avg = sum(a.score for a in attempts) / len(attempts)
        all_passed = all(a.passed for a in attempts)
        if avg >= 80 and all_passed:
            existing = db.query(StudyBadge).filter(
                StudyBadge.student_id == student_id,
                StudyBadge.badge_type == "quiz_master",
            ).first()
            if not existing:
                badge = StudyBadge(
                    student_id=student_id,
                    goal_id=goal_id,
                    badge_type="quiz_master",
                    badge_name="Quiz Master",
                    badge_icon="star_gold",
                )
                db.add(badge)
                new_badges.append({"badge_type": "quiz_master", "badge_name": "Quiz Master", "badge_icon": "star_gold"})

    # Perfect week: 7 consecutive days all sessions completed (check last 7 days)
    seven_days_ago = (datetime.now(timezone.utc) + _IST_OFFSET).date() - timedelta(days=7)
    today_ist = (datetime.now(timezone.utc) + _IST_OFFSET).date()
    perfect_week = True
    for day_offset in range(7):
        check_date = seven_days_ago + timedelta(days=day_offset + 1)
        day_entries = (
            db.query(StudyCalendarEntry)
            .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
            .filter(
                StudyGoal.student_id == student_id,
                StudyCalendarEntry.plan_date == check_date,
            )
            .all()
        )
        if day_entries:
            all_done = all(e.status in ("completed", "skipped") for e in day_entries)
            has_completed = any(e.status == "completed" for e in day_entries)
            if not (all_done and has_completed):
                perfect_week = False
                break
        else:
            perfect_week = False
            break

    if perfect_week:
        existing = db.query(StudyBadge).filter(
            StudyBadge.student_id == student_id,
            StudyBadge.badge_type == "perfect_week",
        ).first()
        if not existing:
            badge = StudyBadge(
                student_id=student_id,
                badge_type="perfect_week",
                badge_name="Perfect Week",
                badge_icon="calendar_check",
            )
            db.add(badge)
            new_badges.append({"badge_type": "perfect_week", "badge_name": "Perfect Week", "badge_icon": "calendar_check"})

    if new_badges:
        db.flush()

    return new_badges


def _update_goal_progress(db):
    """Recalculate progress for all active goals."""
    active_goals = db.query(StudyGoal).filter(StudyGoal.status == "active").all()

    for goal in active_goals:
        total = (
            db.query(StudyCalendarEntry)
            .filter(StudyCalendarEntry.goal_id == goal.id)
            .count()
        )
        completed = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == goal.id,
                StudyCalendarEntry.status == "completed",
            )
            .count()
        )

        goal.total_study_units = total
        goal.completed_units = completed
        goal.completion_pct = round(completed / total * 100, 1) if total else 0

        # Calculate avg MCQ score
        mcq_entries = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == goal.id,
                StudyCalendarEntry.mcq_score.isnot(None),
            )
            .all()
        )
        if mcq_entries:
            goal.avg_mcq_score = round(sum(e.mcq_score for e in mcq_entries) / len(mcq_entries), 1)

        # Calculate total study minutes
        completed_entries = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == goal.id,
                StudyCalendarEntry.status == "completed",
            )
            .all()
        )
        goal.total_study_minutes = sum(e.duration_mins for e in completed_entries)

        # Check if goal is complete
        pending = (
            db.query(StudyCalendarEntry)
            .filter(
                StudyCalendarEntry.goal_id == goal.id,
                StudyCalendarEntry.status.in_(["pending", "in_progress"]),
            )
            .count()
        )
        if total > 0 and pending == 0 and completed > 0:
            goal.status = "completed"
            goal.completion_pct = 100.0

        goal.updated_at = datetime.now(timezone.utc)


def _nightly_cron_job():
    """
    Nightly cron job at 00:30 IST (19:00 UTC).
    - Reschedule missed sessions
    - Schedule revision sessions
    - Update streaks for all students
    - Check badge triggers
    - Update goal progress
    """
    db = SessionLocal()
    try:
        logger.info("Study planner nightly cron started")

        # 1. Reschedule missed sessions
        rescheduled = _reschedule_missed_sessions(db)
        logger.info(f"Rescheduled {rescheduled} missed sessions")

        # 2. Schedule revision sessions
        revisions = _schedule_revision_sessions(db)
        logger.info(f"Scheduled {revisions} revision sessions")

        # 3. Update streaks for all students with active goals
        student_ids = (
            db.query(StudyGoal.student_id)
            .filter(StudyGoal.status == "active")
            .distinct()
            .all()
        )
        for (student_id,) in student_ids:
            streak = update_streak(student_id, db)
            # Check badges for each active goal
            goals = db.query(StudyGoal).filter(
                StudyGoal.student_id == student_id,
                StudyGoal.status == "active",
            ).all()
            for goal in goals:
                check_and_award_badges(student_id, goal.id, db)

        # 4. Update goal progress
        _update_goal_progress(db)

        db.commit()
        logger.info("Study planner nightly cron completed")

    except Exception as e:
        db.rollback()
        logger.error(f"Study planner nightly cron failed: {e}", exc_info=True)
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# DAILY SESSION REMINDER — runs every 5 minutes
# ═══════════════════════════════════════════════════════════

def _session_reminder_job() -> None:
    """
    Check every student's time slots. If a slot starts within the next 5 minutes
    (IST), send a push notification with today's session info for that slot.
    Runs every 5 minutes.
    """
    from app.utils.fcm import notify_student_study_reminder

    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        now_ist = now_utc + _IST_OFFSET
        today_ist = now_ist.date()
        current_time_ist = now_ist.time()

        # Window: current time to +5 minutes
        window_end_ist = (now_ist + timedelta(minutes=5)).time()

        # Handle midnight wraparound (e.g., 23:57 → 00:02)
        wraps_midnight = window_end_ist < current_time_ist

        # Find all time slots for active goals where start_time falls in window
        query = (
            db.query(StudyTimeSlot, StudyGoal)
            .join(StudyGoal, StudyGoal.id == StudyTimeSlot.goal_id)
            .filter(StudyGoal.status == "active")
        )

        if wraps_midnight:
            # Two ranges: [current, 23:59:59] OR [00:00, window_end]
            from sqlalchemy import or_
            query = query.filter(
                or_(
                    StudyTimeSlot.start_time >= current_time_ist,
                    StudyTimeSlot.start_time <= window_end_ist,
                )
            )
        else:
            query = query.filter(
                StudyTimeSlot.start_time >= current_time_ist,
                StudyTimeSlot.start_time < window_end_ist,
            )

        slots_to_notify = query.all()

        if not slots_to_notify:
            return

        sent = 0
        for slot, goal in slots_to_notify:
            # Get today's calendar entry for this goal and slot
            entry = (
                db.query(StudyCalendarEntry)
                .filter(
                    StudyCalendarEntry.goal_id == goal.id,
                    StudyCalendarEntry.plan_date == today_ist,
                    StudyCalendarEntry.slot_label == slot.label,
                    StudyCalendarEntry.status == "pending",
                )
                .first()
            )

            if not entry:
                continue

            # Get student's FCM token
            student = db.query(User).filter(User.id == goal.student_id).first()
            if not student or not student.fcm_token:
                continue

            notify_student_study_reminder(
                fcm_token=student.fcm_token,
                slot_label=slot.label,
                topic_title=entry.topic_title,
                subject_name=entry.subject_name or "",
                goal_id=goal.id,
                entry_id=entry.id,
            )
            sent += 1

        if sent:
            logger.info(f"Study reminders sent: {sent}")

    except Exception as e:
        logger.error(f"Session reminder job failed: {e}", exc_info=True)
    finally:
        db.close()


# ═══════════════════════════════════════════════════════════
# STREAK REMINDER — runs once at 16:30 IST (11:00 UTC)
# ═══════════════════════════════════════════════════════════

def _streak_reminder_job() -> None:
    """
    Evening nudge: if a student has an active streak but hasn't studied today,
    send a reminder to not break it.
    """
    from app.utils.fcm import notify_student_streak_reminder

    db = SessionLocal()
    try:
        now_utc = datetime.now(timezone.utc)
        today_ist = (now_utc + _IST_OFFSET).date()

        # Get all students with active goals
        student_ids = (
            db.query(StudyGoal.student_id)
            .filter(StudyGoal.status == "active")
            .distinct()
            .all()
        )

        sent = 0
        for (student_id,) in student_ids:
            # Check if already studied today
            completed_today = (
                db.query(StudyCalendarEntry)
                .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
                .filter(
                    StudyGoal.student_id == student_id,
                    StudyCalendarEntry.plan_date == today_ist,
                    StudyCalendarEntry.status == "completed",
                )
                .first()
            )

            if completed_today:
                continue  # Already studied, no reminder needed

            # Check current streak
            streak = db.query(StudyStreak).filter(
                StudyStreak.student_id == student_id
            ).first()

            if not streak or streak.current_streak < 1:
                continue  # No active streak to protect

            # Check last study date — only remind if streak is still alive
            # (i.e., they studied yesterday)
            yesterday = today_ist - timedelta(days=1)
            if streak.last_study_date != yesterday:
                continue

            student = db.query(User).filter(User.id == student_id).first()
            if not student or not student.fcm_token:
                continue

            notify_student_streak_reminder(
                fcm_token=student.fcm_token,
                current_streak=streak.current_streak,
            )
            sent += 1

        if sent:
            logger.info(f"Streak reminders sent: {sent}")

    except Exception as e:
        logger.error(f"Streak reminder job failed: {e}", exc_info=True)
    finally:
        db.close()


# ── Public API ──────────────────────────────────────────────

_study_scheduler: BackgroundScheduler | None = None


def start_study_planner_scheduler() -> None:
    """Start the study planner scheduler with all jobs."""
    global _study_scheduler
    _study_scheduler = BackgroundScheduler(timezone="UTC")

    # 1. Nightly cron — reschedule, streaks, badges (19:00 UTC = 00:30 IST)
    _study_scheduler.add_job(
        _nightly_cron_job,
        trigger=CronTrigger(hour=19, minute=0),
        id="study_planner_nightly",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    # 2. Session reminders — every 5 minutes, checks if any slot is starting
    _study_scheduler.add_job(
        _session_reminder_job,
        trigger=CronTrigger(minute="*/5"),
        id="study_session_reminder",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=60,
    )

    # 3. Streak reminder — 11:00 UTC = 16:30 IST (evening nudge)
    _study_scheduler.add_job(
        _streak_reminder_job,
        trigger=CronTrigger(hour=11, minute=0),
        id="study_streak_reminder",
        replace_existing=True,
        max_instances=1,
        misfire_grace_time=300,
    )

    _study_scheduler.start()
    logger.info(
        "Study Planner scheduler started: "
        "nightly@19:00UTC, reminders@every5min, streak@11:00UTC"
    )


def stop_study_planner_scheduler() -> None:
    """Stop the study planner scheduler."""
    global _study_scheduler
    if _study_scheduler and _study_scheduler.running:
        _study_scheduler.shutdown(wait=False)
        logger.info("Study Planner scheduler stopped.")
