"""
Study Plan Engine — Smart calendar generation algorithm.

Distributes chapters across available days using a Study → Practice → Revise
cycle. When few chapters exist relative to available days, the engine expands
each chapter into multiple sessions to fill the schedule meaningfully.
"""
import logging
import math
from datetime import date, timedelta, datetime, timezone, time as dt_time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
from app.models.quiz import Quiz, QuizStatus
from app.models.study_planner_v2 import (
    StudyGoal, StudyGoalSubject, StudyTimeSlot,
    StudyCalendarEntry, GoalExam,
)

logger = logging.getLogger(__name__)

# Difficulty multipliers for time estimation
_DIFFICULTY_MULTIPLIERS = {
    "easy": 0.8,
    "medium": 1.0,
    "hard": 1.3,
}

_DEFAULT_CHAPTER_MINS = 45

# Students are largely in India — anchor "today" to IST so the plan starts on
# the correct local day (matches the student router).
_IST = timezone(timedelta(hours=5, minutes=30))


def _ist_today() -> date:
    return datetime.now(_IST).date()

# Session type labels used in the cycle
_SESSION_LEARN = "Learn"
_SESSION_PRACTICE = "Practice"
_SESSION_REVISE = "Revise"


def _parse_time(t: str) -> dt_time:
    parts = t.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def _slot_duration_mins(start: dt_time, end: dt_time) -> int:
    """Calculate duration in minutes between two times."""
    start_mins = start.hour * 60 + start.minute
    end_mins = end.hour * 60 + end.minute
    if end_mins <= start_mins:
        end_mins += 24 * 60
    return end_mins - start_mins


def calculate_feasibility(
    study_units: list[dict],
    target_date: date,
    time_slots: list[dict],
    start_date: date = None,
) -> dict:
    """
    Check if the plan is feasible given the constraints.
    Returns feasibility info dict.
    """
    if start_date is None:
        start_date = _ist_today()

    total_days = (target_date - start_date).days + 1
    if total_days <= 0:
        return {
            "is_feasible": False,
            "message": "Target date must be in the future",
            "daily_topics_avg": 0,
            "total_hours_required": 0,
            "total_days": 0,
            "buffer_days": 0,
        }

    slots_per_day = len(time_slots)
    total_required_mins = sum(u["adjusted_mins"] for u in study_units)
    total_hours_required = round(total_required_mins / 60, 1)

    total_units = len(study_units)
    days_needed = -(-total_units // slots_per_day)
    buffer_days = total_days - days_needed
    daily_topics_avg = round(total_units / max(total_days, 1), 1)

    is_feasible = buffer_days >= 0

    if is_feasible:
        if buffer_days >= 7:
            message = f"Your plan covers all {total_units} topics with {buffer_days} buffer days for revision"
        elif buffer_days >= 1:
            message = f"Your plan fits with {buffer_days} buffer day(s). Stay consistent!"
        else:
            message = "Tight schedule! Every day counts — no buffer days."
    else:
        message = (
            f"Not enough time. You need {days_needed} study days but only have {total_days}. "
            f"Consider extending your deadline or adding more time slots."
        )

    return {
        "is_feasible": is_feasible,
        "message": message,
        "daily_topics_avg": daily_topics_avg,
        "total_hours_required": total_hours_required,
        "total_days": total_days,
        "buffer_days": max(buffer_days, 0),
    }


def collect_study_units(
    subject_ids: list[int],
    db: Session,
    *,
    require_content: bool = False,
) -> list[dict]:
    """
    Collect all chapters from selected subjects and convert to study units.
    Each chapter becomes one study unit.
    If require_content=True, chapters with zero syllabus_contents are skipped.
    """
    study_units = []
    skipped_empty = 0

    for syllabus_id in subject_ids:
        syllabus = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
        if not syllabus:
            continue

        chapters = (
            db.query(Chapter)
            .filter(Chapter.syllabus_id == syllabus_id)
            .order_by(Chapter.order_index, Chapter.id)
            .all()
        )

        for ch in chapters:
            difficulty = getattr(ch, "difficulty", None) or "medium"
            estimated = getattr(ch, "estimated_minutes", None) or _DEFAULT_CHAPTER_MINS
            multiplier = _DIFFICULTY_MULTIPLIERS.get(difficulty, 1.0)
            adjusted_mins = int(estimated * multiplier)

            contents = (
                db.query(SyllabusContent)
                .filter(SyllabusContent.chapter_id == ch.id)
                .order_by(SyllabusContent.order_index)
                .all()
            )
            content_ids = [c.id for c in contents]

            # A chapter is "studyable" if it has uploaded lessons OR published AI
            # content OR a published chapter quiz.
            has_ai_content = bool(
                getattr(ch, "content_published", False)
                and (ch.description or "").strip()
            )
            has_quiz = (
                db.query(Quiz.id)
                .filter(
                    Quiz.chapter_id == ch.id,
                    Quiz.quiz_type == "chapter",
                    Quiz.status == QuizStatus.PUBLISHED,
                )
                .first()
                is not None
            )
            has_materials = bool(content_ids) or has_ai_content or has_quiz

            if require_content and not has_materials:
                skipped_empty += 1
                continue

            study_units.append({
                "syllabus_id": syllabus_id,
                "subject_name": syllabus.title,
                "chapter_id": ch.id,
                "chapter_name": ch.title,
                "topic_title": ch.title,
                "difficulty": difficulty,
                "estimated_mins": estimated,
                "adjusted_mins": adjusted_mins,
                "content_ids": content_ids,
                "has_materials": has_materials,
            })

    if require_content and skipped_empty and not study_units:
        logger = __import__("logging").getLogger(__name__)
        logger.warning("All %s chapters skipped — no study materials uploaded", skipped_empty)

    return study_units


def _round_robin_interleave(units: list[dict]) -> list[dict]:
    """
    Interleave units by subject for variety.
    e.g., Physics Ch1 → Maths Ch1 → Physics Ch2 → Maths Ch2 → ...
    """
    from collections import defaultdict

    by_subject: dict[int, list[dict]] = defaultdict(list)
    subject_order = []

    for u in units:
        sid = u["syllabus_id"]
        if sid not in subject_order:
            subject_order.append(sid)
        by_subject[sid].append(u)

    result = []
    max_len = max((len(v) for v in by_subject.values()), default=0)

    for i in range(max_len):
        for sid in subject_order:
            if i < len(by_subject[sid]):
                result.append(by_subject[sid][i])

    return result


def _expand_units_to_sessions(
    study_units: list[dict],
    total_available_slots: int,
) -> list[dict]:
    """
    Expand each study unit into multiple sessions (Learn → Practice → Revise)
    to fill the available schedule meaningfully.

    With 2 chapters and 37 slots, this creates ~6 sessions per chapter
    (study, deep-dive, practice problems, revise, mock test, final review).
    """
    num_units = len(study_units)
    if num_units == 0:
        return []

    # How many sessions can we create per unit?
    sessions_per_unit = max(1, total_available_slots // num_units)
    # Cap at 6 session types per chapter to keep it meaningful
    sessions_per_unit = min(sessions_per_unit, 6)

    # Define session phases per chapter
    _PHASES = [
        (_SESSION_LEARN, "📖 Learn — Read & understand the concepts", 1.0),
        ("Deep Dive", "🔍 Deep Dive — Detailed study of formulas & theory", 0.8),
        (_SESSION_PRACTICE, "✏️ Practice — Solve problems & exercises", 0.9),
        (_SESSION_REVISE, "🔄 Revise — Review key points & notes", 0.6),
        ("Mock Test", "📝 Mock Test — MCQ self-assessment", 0.5),
        ("Final Review", "📋 Final Review — Quick recap before moving on", 0.4),
    ]

    expanded = []
    for unit in study_units:
        for phase_idx in range(sessions_per_unit):
            phase_name, phase_desc, duration_factor = _PHASES[phase_idx]
            session = {
                **unit,
                "topic_title": f"{phase_name}: {unit['topic_title']}",
                "original_topic": unit["topic_title"],
                "session_phase": phase_name,
                "phase_order": phase_idx,
                "adjusted_mins": max(20, int(unit["adjusted_mins"] * duration_factor)),
            }
            expanded.append(session)

    return expanded


def generate_calendar(
    goal: StudyGoal,
    study_units: list[dict],
    time_slots: list[StudyTimeSlot],
    db: Session,
) -> list[StudyCalendarEntry]:
    """
    Generate calendar entries by assigning study units to available slots.
    Spreads sessions evenly across available days.
    Starts from today, not tomorrow.
    """
    if not study_units or not time_slots:
        return []

    sorted_slots = sorted(time_slots, key=lambda s: (s.start_time.hour, s.start_time.minute))
    slots_count = len(sorted_slots)

    # Study window: today (IST) through the target date, inclusive.
    start_date = _ist_today()
    total_days = (goal.target_date - start_date).days + 1
    if total_days <= 0:
        total_days = 1
    capacity = total_days * slots_count

    # Expand units into Learn/Practice/Revise sessions when there's room, so the
    # plan meaningfully fills the window up to the target date.
    if len(study_units) < capacity:
        expanded = _expand_units_to_sessions(study_units, capacity)
    else:
        expanded = study_units

    interleaved = _round_robin_interleave(expanded)
    num_sessions = len(interleaved)
    if num_sessions == 0:
        return []

    # Distribute sessions EVENLY across the whole window (today → target). Each
    # session i is anchored to a day proportional to its position, then packed
    # into that day's slots (rolling forward if a day is already full). This
    # guarantees the plan spans right up to the target date instead of clustering
    # early or scattering with large empty gaps.
    per_day_count: dict[int, int] = {}
    entries = []
    for i, unit in enumerate(interleaved):
        if num_sessions > 1:
            day_offset = round(i * (total_days - 1) / (num_sessions - 1))
        else:
            day_offset = 0

        # Respect per-day slot capacity; roll forward to the next free day.
        while day_offset < total_days and per_day_count.get(day_offset, 0) >= slots_count:
            day_offset += 1
        if day_offset >= total_days:
            break  # window is full to capacity before the target date

        slot_in_day = per_day_count.get(day_offset, 0)
        per_day_count[day_offset] = slot_in_day + 1

        entry_date = start_date + timedelta(days=day_offset)
        slot = sorted_slots[slot_in_day]

        entries.append(StudyCalendarEntry(
            goal_id=goal.id,
            plan_date=entry_date,
            slot_label=slot.label,
            slot_order=slot_in_day,
            syllabus_id=unit["syllabus_id"],
            chapter_id=unit["chapter_id"],
            topic_title=unit["topic_title"],
            subject_name=unit["subject_name"],
            chapter_name=unit["chapter_name"],
            difficulty=unit["difficulty"],
            duration_mins=unit["adjusted_mins"],
            content_ids=unit["content_ids"],
            status="pending",
        ))

    return entries


def generate_plan_for_goal(goal: StudyGoal, db: Session) -> dict:
    """
    Full plan generation pipeline for a goal.
    Collects units, checks feasibility, generates calendar, persists.
    Returns status dict.
    """
    subject_ids = [gs.syllabus_id for gs in goal.subjects]
    if not subject_ids:
        return {"status": "error", "total_units": 0, "message": "No subjects selected"}

    # Schedule EVERY chapter of the selected subjects — even name-only chapters
    # (they become "study this chapter" sessions that deep-link to the content).
    study_units = collect_study_units(subject_ids, db, require_content=False)
    skipped_note = ""
    if not study_units:
        return {
            "status": "error",
            "total_units": 0,
            "message": "No chapters found for the selected subjects. Add chapters under Admin → Syllabus.",
        }

    time_slots = goal.time_slots
    if not time_slots:
        return {"status": "error", "total_units": 0, "message": "No time slots configured"}

    slot_dicts = [{"label": s.label, "start": s.start_time, "end": s.end_time} for s in time_slots]
    feasibility = calculate_feasibility(study_units, goal.target_date, slot_dicts)

    # Delete existing pending entries (regenerate)
    db.query(StudyCalendarEntry).filter(
        StudyCalendarEntry.goal_id == goal.id,
        StudyCalendarEntry.status == "pending",
    ).delete(synchronize_session="fetch")

    # Keep completed/in_progress entries
    completed_chapter_ids = set()
    existing = (
        db.query(StudyCalendarEntry)
        .filter(
            StudyCalendarEntry.goal_id == goal.id,
            StudyCalendarEntry.status.in_(["completed", "in_progress"]),
        )
        .all()
    )
    for e in existing:
        if e.chapter_id:
            completed_chapter_ids.add(e.chapter_id)

    remaining_units = [u for u in study_units if u["chapter_id"] not in completed_chapter_ids]

    if not remaining_units:
        goal.status = "completed"
        goal.completion_pct = 100.0
        db.flush()
        return {"status": "completed", "total_units": len(study_units), "message": "All topics already completed!"}

    entries = generate_calendar(goal, remaining_units, time_slots, db)

    for entry in entries:
        db.add(entry)

    # Update goal stats — count expanded sessions as total units
    goal.total_study_units = len(entries) + len(completed_chapter_ids)
    goal.completed_units = len(completed_chapter_ids)
    goal.completion_pct = round(len(completed_chapter_ids) / goal.total_study_units * 100, 1) if goal.total_study_units else 0
    goal.updated_at = datetime.now(timezone.utc)

    db.flush()

    return {
        "status": "generated",
        "total_units": goal.total_study_units,
        "message": feasibility["message"] + skipped_note,
    }
