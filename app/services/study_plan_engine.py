"""
Study Plan Engine — Deterministic calendar generation algorithm.

Distributes chapters across available days and time slots based on
difficulty, interleaving subjects for variety.
"""
import logging
from datetime import date, timedelta, datetime, timezone, time as dt_time
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
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


def _parse_time(t: str) -> dt_time:
    parts = t.split(":")
    return dt_time(int(parts[0]), int(parts[1]))


def _slot_duration_mins(start: dt_time, end: dt_time) -> int:
    """Calculate duration in minutes between two times."""
    start_mins = start.hour * 60 + start.minute
    end_mins = end.hour * 60 + end.minute
    if end_mins <= start_mins:
        end_mins += 24 * 60  # handle overnight
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
        start_date = date.today() + timedelta(days=1)

    total_days = (target_date - start_date).days
    if total_days <= 0:
        return {
            "is_feasible": False,
            "message": "Target date must be in the future",
            "daily_topics_avg": 0,
            "total_hours_required": 0,
            "total_days": 0,
            "buffer_days": 0,
        }

    # Calculate daily capacity
    slots_per_day = len(time_slots)
    total_required_mins = sum(u["adjusted_mins"] for u in study_units)
    total_hours_required = round(total_required_mins / 60, 1)

    # Each slot gets 1 topic. Days needed = ceil(units / slots_per_day)
    total_units = len(study_units)
    days_needed = -(-total_units // slots_per_day)  # ceiling division
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
) -> list[dict]:
    """
    Collect all chapters from selected subjects and convert to study units.
    Each chapter becomes one study unit.
    """
    study_units = []

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

            # Get content IDs for this chapter
            contents = (
                db.query(SyllabusContent)
                .filter(SyllabusContent.chapter_id == ch.id)
                .order_by(SyllabusContent.order_index)
                .all()
            )
            content_ids = [c.id for c in contents]

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
            })

    return study_units


def _round_robin_interleave(units: list[dict]) -> list[dict]:
    """
    Interleave units by subject for variety.
    e.g., Physics Ch1 → Chemistry Ch1 → Biology Ch1 → Physics Ch2 → ...
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


def generate_calendar(
    goal: StudyGoal,
    study_units: list[dict],
    time_slots: list[StudyTimeSlot],
    db: Session,
) -> list[StudyCalendarEntry]:
    """
    Generate calendar entries by assigning study units to available slots.
    Returns list of StudyCalendarEntry objects (not yet committed).
    """
    if not study_units or not time_slots:
        return []

    # Sort time slots by start_time
    sorted_slots = sorted(time_slots, key=lambda s: (s.start_time.hour, s.start_time.minute))

    # Interleave subjects
    interleaved = _round_robin_interleave(study_units)

    entries = []
    current_date = date.today() + timedelta(days=1)
    slot_index = 0
    slots_count = len(sorted_slots)

    for unit in interleaved:
        if current_date > goal.target_date:
            break  # Overflow — skip remaining

        slot = sorted_slots[slot_index % slots_count]
        slot_in_day = slot_index % slots_count

        entry = StudyCalendarEntry(
            goal_id=goal.id,
            plan_date=current_date,
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
        )
        entries.append(entry)

        slot_index += 1
        if slot_index % slots_count == 0:
            current_date += timedelta(days=1)

    return entries


def generate_plan_for_goal(goal: StudyGoal, db: Session) -> dict:
    """
    Full plan generation pipeline for a goal.
    Collects units, checks feasibility, generates calendar, persists.
    Returns status dict.
    """
    # Collect subject IDs
    subject_ids = [gs.syllabus_id for gs in goal.subjects]
    if not subject_ids:
        return {"status": "error", "total_units": 0, "message": "No subjects selected"}

    # Collect study units
    study_units = collect_study_units(subject_ids, db)
    if not study_units:
        return {"status": "error", "total_units": 0, "message": "No chapters found for selected subjects"}

    # Get time slots
    time_slots = goal.time_slots
    if not time_slots:
        return {"status": "error", "total_units": 0, "message": "No time slots configured"}

    # Feasibility
    slot_dicts = [{"label": s.label, "start": s.start_time, "end": s.end_time} for s in time_slots]
    feasibility = calculate_feasibility(study_units, goal.target_date, slot_dicts)

    # Delete existing calendar entries (regenerate)
    db.query(StudyCalendarEntry).filter(
        StudyCalendarEntry.goal_id == goal.id,
        StudyCalendarEntry.status == "pending",
    ).delete(synchronize_session="fetch")

    # Keep completed/skipped entries
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

    # Filter out already completed chapters
    remaining_units = [u for u in study_units if u["chapter_id"] not in completed_chapter_ids]

    if not remaining_units:
        goal.status = "completed"
        goal.completion_pct = 100.0
        db.flush()
        return {"status": "completed", "total_units": len(study_units), "message": "All topics already completed!"}

    # Generate calendar
    entries = generate_calendar(goal, remaining_units, time_slots, db)

    for entry in entries:
        db.add(entry)

    # Update goal stats
    goal.total_study_units = len(study_units)
    goal.completed_units = len(completed_chapter_ids)
    goal.completion_pct = round(len(completed_chapter_ids) / len(study_units) * 100, 1) if study_units else 0
    goal.updated_at = datetime.now(timezone.utc)

    db.flush()

    return {
        "status": "generated",
        "total_units": len(entries) + len(completed_chapter_ids),
        "message": feasibility["message"],
    }
