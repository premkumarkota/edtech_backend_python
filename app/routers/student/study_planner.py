"""
Student Study Planner V2 Routes.

Goal-based study planning with calendar, MCQ testing, progress, streaks, badges.
"""
import logging
from datetime import date, datetime, timezone, timedelta, time as dt_time
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func as sqlfunc

from app.database import get_db
from app.dependencies.auth import get_onboarded_student
from app.models.user import User
from app.models.syllabus import Syllabus, SyllabusContent
from app.models.study_planner_v2 import (
    GoalExam, GoalExamSubject, StudyGoal, StudyGoalSubject,
    StudyTimeSlot, StudyCalendarEntry, StudyMcqBank, StudyMcqAttempt,
    StudyStreak, StudyBadge,
)
from app.schemas.study_planner_v2 import (
    GoalSetupRequest, GoalSetupResponse, GoalListResponse, GoalSubjectInfo,
    GoalUpdateRequest, FeasibilityInfo, GenerateStatusResponse,
    CalendarResponse, CalendarEntryResponse, CalendarSummary,
    TodayPlanResponse, TodaySessionResponse, ContentInfo,
    SessionStartResponse,
    McqGenerateRequest, McqGenerateResponse, McqQuestionResponse,
    McqSubmitRequest, McqSubmitResponse, McqResultDetail,
    ProgressResponse, OverallProgress, WeeklyDataPoint, SubjectBreakdown,
    StreakResponse, StreakHistoryDay, BadgeResponse,
    GoalReportResponse, SubjectScoreReport, StreakStatsReport,
    ExamResponse, ExamSubjectResponse,
)
from app.services.study_plan_engine import (
    collect_study_units, calculate_feasibility, generate_plan_for_goal,
)
from app.services.study_mcq_service import get_or_generate_mcq, score_mcq_attempt
from app.services.study_reschedule_service import update_streak, check_and_award_badges

logger = logging.getLogger(__name__)

router = APIRouter()

_IST_OFFSET = timedelta(hours=5, minutes=30)


# ═══════════════════════════════════════════════════════════
# EXAMS (list for students)
# ═══════════════════════════════════════════════════════════

@router.get("/exams", response_model=list[ExamResponse])
def list_exams(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """List available exams for goal creation."""
    exams = (
        db.query(GoalExam)
        .filter(GoalExam.is_active == True)
        .order_by(GoalExam.display_order, GoalExam.name)
        .all()
    )
    results = []
    for exam in exams:
        subjects = []
        for es in exam.subjects:
            syl = es.syllabus
            if syl:
                subjects.append(ExamSubjectResponse(
                    syllabus_id=syl.id,
                    title=syl.title,
                    is_required=es.is_required,
                    weightage=es.weightage,
                ))
        results.append(ExamResponse(
            id=exam.id, name=exam.name, code=exam.code,
            description=exam.description, icon_url=exam.icon_url,
            category_id=exam.category_id, is_active=exam.is_active,
            display_order=exam.display_order, subjects=subjects,
        ))
    return results


# ═══════════════════════════════════════════════════════════
# GOALS
# ═══════════════════════════════════════════════════════════

@router.post("/goals/setup", response_model=GoalSetupResponse)
def setup_goal(
    payload: GoalSetupRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Create a new study goal with full configuration."""
    # Validate target date
    if payload.target_date <= date.today():
        raise HTTPException(400, "Target date must be in the future")

    # Validate subjects exist
    for sid in payload.subject_ids:
        syl = db.query(Syllabus).filter(Syllabus.id == sid).first()
        if not syl:
            raise HTTPException(400, f"Syllabus {sid} not found")

    # Validate time slots don't overlap
    parsed_slots = []
    for slot in payload.time_slots:
        parts_start = slot.start_time.split(":")
        parts_end = slot.end_time.split(":")
        s = dt_time(int(parts_start[0]), int(parts_start[1]))
        e = dt_time(int(parts_end[0]), int(parts_end[1]))
        s_mins = s.hour * 60 + s.minute
        e_mins = e.hour * 60 + e.minute
        if e_mins <= s_mins:
            raise HTTPException(400, f"End time must be after start time for slot '{slot.label}'")
        if (e_mins - s_mins) < 30:
            raise HTTPException(400, "Each time slot must be at least 30 minutes")
        parsed_slots.append({"label": slot.label, "start": s, "end": e, "s_mins": s_mins, "e_mins": e_mins})

    # Check overlaps
    for i, a in enumerate(parsed_slots):
        for b in parsed_slots[i + 1:]:
            if a["s_mins"] < b["e_mins"] and b["s_mins"] < a["e_mins"]:
                raise HTTPException(400, f"Time slots '{a['label']}' and '{b['label']}' overlap")

    # Create goal
    goal = StudyGoal(
        student_id=student.id,
        exam_id=payload.exam_id,
        custom_goal_name=payload.custom_goal_name,
        target_date=payload.target_date,
        daily_study_hours=payload.daily_study_hours,
        planning_mode=payload.planning_mode,
        pass_threshold=payload.pass_threshold,
        status="active",
    )
    db.add(goal)
    db.flush()

    # Add subjects
    for sid in payload.subject_ids:
        db.add(StudyGoalSubject(goal_id=goal.id, syllabus_id=sid))

    # Add time slots
    for slot in payload.time_slots:
        parts_s = slot.start_time.split(":")
        parts_e = slot.end_time.split(":")
        db.add(StudyTimeSlot(
            goal_id=goal.id,
            label=slot.label,
            start_time=dt_time(int(parts_s[0]), int(parts_s[1])),
            end_time=dt_time(int(parts_e[0]), int(parts_e[1])),
        ))

    db.flush()

    # Calculate feasibility
    study_units = collect_study_units(payload.subject_ids, db)
    slot_dicts = [{"label": s["label"], "start": s["start"], "end": s["end"]} for s in parsed_slots]
    feasibility = calculate_feasibility(study_units, payload.target_date, slot_dicts)

    db.commit()
    db.refresh(goal)

    return GoalSetupResponse(
        goal_id=goal.id,
        status=goal.status,
        feasibility=FeasibilityInfo(**feasibility),
        created_at=goal.created_at,
    )


@router.get("/goals", response_model=list[GoalListResponse])
def list_goals(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """List student's study goals."""
    goals = (
        db.query(StudyGoal)
        .filter(StudyGoal.student_id == student.id)
        .order_by(StudyGoal.created_at.desc())
        .all()
    )

    results = []
    today = date.today()

    for goal in goals:
        exam_name = None
        exam_icon = None
        if goal.exam:
            exam_name = goal.exam.name
            exam_icon = goal.exam.icon_url

        subjects = []
        for gs in goal.subjects:
            if gs.syllabus:
                subjects.append(GoalSubjectInfo(syllabus_id=gs.syllabus.id, title=gs.syllabus.title))

        days_remaining = max(0, (goal.target_date - today).days)

        results.append(GoalListResponse(
            id=goal.id,
            exam_name=exam_name,
            custom_goal_name=goal.custom_goal_name,
            exam_icon=exam_icon,
            target_date=goal.target_date,
            planning_mode=goal.planning_mode,
            status=goal.status,
            completion_pct=goal.completion_pct or 0,
            days_remaining=days_remaining,
            total_study_units=goal.total_study_units or 0,
            completed_units=goal.completed_units or 0,
            subjects=subjects,
            created_at=goal.created_at,
        ))

    return results


@router.get("/goals/{goal_id}")
def get_goal_detail(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get detailed info for a specific goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    exam_name = goal.exam.name if goal.exam else goal.custom_goal_name
    subjects = []
    for gs in goal.subjects:
        if gs.syllabus:
            subjects.append({"syllabus_id": gs.syllabus.id, "title": gs.syllabus.title})

    time_slots = []
    for ts in goal.time_slots:
        time_slots.append({
            "label": ts.label,
            "start_time": ts.start_time.strftime("%H:%M"),
            "end_time": ts.end_time.strftime("%H:%M"),
        })

    return {
        "id": goal.id,
        "exam_name": exam_name,
        "target_date": str(goal.target_date),
        "daily_study_hours": goal.daily_study_hours,
        "planning_mode": goal.planning_mode,
        "status": goal.status,
        "completion_pct": goal.completion_pct or 0,
        "total_study_units": goal.total_study_units or 0,
        "completed_units": goal.completed_units or 0,
        "total_study_minutes": goal.total_study_minutes or 0,
        "avg_mcq_score": goal.avg_mcq_score or 0,
        "pass_threshold": goal.pass_threshold,
        "days_remaining": max(0, (goal.target_date - date.today()).days),
        "subjects": subjects,
        "time_slots": time_slots,
        "created_at": goal.created_at.isoformat() if goal.created_at else None,
    }


@router.put("/goals/{goal_id}")
def update_goal(
    goal_id: int,
    payload: GoalUpdateRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Update goal settings."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    if payload.target_date is not None:
        if payload.target_date <= date.today():
            raise HTTPException(400, "Target date must be in the future")
        goal.target_date = payload.target_date
    if payload.daily_study_hours is not None:
        goal.daily_study_hours = payload.daily_study_hours
    if payload.planning_mode is not None:
        goal.planning_mode = payload.planning_mode
    if payload.status is not None:
        goal.status = payload.status
    if payload.pass_threshold is not None:
        goal.pass_threshold = payload.pass_threshold

    goal.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"message": "Goal updated", "goal_id": goal.id}


@router.delete("/goals/{goal_id}")
def archive_goal(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Archive (soft-delete) a goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    goal.status = "abandoned"
    goal.updated_at = datetime.now(timezone.utc)
    db.commit()

    return {"message": "Goal archived"}


# ═══════════════════════════════════════════════════════════
# CALENDAR / PLAN
# ═══════════════════════════════════════════════════════════

@router.post("/goals/{goal_id}/generate", response_model=GenerateStatusResponse)
def generate_calendar(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Generate or regenerate the study calendar for a goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    result = generate_plan_for_goal(goal, db)
    db.commit()

    return GenerateStatusResponse(
        status=result["status"],
        total_units=result["total_units"],
        message=result["message"],
    )


@router.get("/goals/{goal_id}/calendar", response_model=CalendarResponse)
def get_calendar(
    goal_id: int,
    from_date: date = Query(None, alias="from"),
    to_date: date = Query(None, alias="to"),
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get calendar entries for a goal within a date range."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    query = db.query(StudyCalendarEntry).filter(StudyCalendarEntry.goal_id == goal_id)

    if from_date:
        query = query.filter(StudyCalendarEntry.plan_date >= from_date)
    if to_date:
        query = query.filter(StudyCalendarEntry.plan_date <= to_date)

    entries = query.order_by(
        StudyCalendarEntry.plan_date, StudyCalendarEntry.slot_order
    ).all()

    entry_responses = [CalendarEntryResponse.model_validate(e) for e in entries]

    # Summary
    total = len(entries)
    completed = sum(1 for e in entries if e.status == "completed")
    pending = sum(1 for e in entries if e.status == "pending")
    skipped = sum(1 for e in entries if e.status == "skipped")
    revision = sum(1 for e in entries if e.is_revision)

    return CalendarResponse(
        goal_id=goal_id,
        entries=entry_responses,
        summary=CalendarSummary(
            total_entries=total, completed=completed,
            pending=pending, skipped=skipped, revision=revision,
        ),
    )


@router.get("/goals/{goal_id}/today", response_model=TodayPlanResponse)
def get_today_plan(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get today's study sessions for a goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    today = date.today()
    entries = (
        db.query(StudyCalendarEntry)
        .filter(
            StudyCalendarEntry.goal_id == goal_id,
            StudyCalendarEntry.plan_date == today,
        )
        .order_by(StudyCalendarEntry.slot_order)
        .all()
    )

    # Build time slot map
    slot_time_map = {}
    for ts in goal.time_slots:
        slot_time_map[ts.label] = f"{ts.start_time.strftime('%H:%M')} - {ts.end_time.strftime('%H:%M')}"

    sessions = []
    for entry in entries:
        # Get content details
        contents = []
        for cid in (entry.content_ids or []):
            content = db.query(SyllabusContent).filter(SyllabusContent.id == cid).first()
            if content:
                contents.append(ContentInfo(
                    id=content.id,
                    title=content.title,
                    content_type=content.content_type,
                    file_url=content.file_url or "",
                ))

        sessions.append(TodaySessionResponse(
            id=entry.id,
            slot_label=entry.slot_label,
            slot_time=slot_time_map.get(entry.slot_label, ""),
            topic_title=entry.topic_title,
            subject_name=entry.subject_name,
            chapter_name=entry.chapter_name,
            difficulty=entry.difficulty or "medium",
            duration_mins=entry.duration_mins,
            status=entry.status,
            is_revision=entry.is_revision or False,
            contents=contents,
            mcq_available=True,
            mcq_score=entry.mcq_score,
            mcq_passed=entry.mcq_passed,
        ))

    total = len(sessions)
    completed = sum(1 for s in sessions if s.status == "completed")

    return TodayPlanResponse(
        date=today,
        goal_id=goal_id,
        sessions=sessions,
        daily_progress={
            "total": total,
            "completed": completed,
            "completion_pct": round(completed / total * 100, 1) if total else 0,
        },
    )


# ═══════════════════════════════════════════════════════════
# SESSIONS
# ═══════════════════════════════════════════════════════════

@router.post("/sessions/{entry_id}/start", response_model=SessionStartResponse)
def start_session(
    entry_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Start a study session."""
    entry = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.id == entry_id,
            StudyGoal.student_id == student.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Session not found")

    if entry.status not in ("pending", "in_progress"):
        raise HTTPException(400, f"Session is already {entry.status}")

    entry.status = "in_progress"
    entry.started_at = datetime.now(timezone.utc)
    db.commit()

    # Get contents
    contents = []
    for cid in (entry.content_ids or []):
        content = db.query(SyllabusContent).filter(SyllabusContent.id == cid).first()
        if content:
            contents.append(ContentInfo(
                id=content.id, title=content.title,
                content_type=content.content_type, file_url=content.file_url or "",
            ))

    return SessionStartResponse(
        entry_id=entry.id,
        status=entry.status,
        started_at=entry.started_at,
        contents=contents,
        message=f"Let's study {entry.topic_title}!",
    )


@router.post("/sessions/{entry_id}/complete")
def complete_session(
    entry_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Complete a study session without MCQ (self-assessed)."""
    entry = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.id == entry_id,
            StudyGoal.student_id == student.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Session not found")

    entry.status = "completed"
    entry.completed_at = datetime.now(timezone.utc)

    # Update streak
    streak = update_streak(student.id, db)
    check_and_award_badges(student.id, entry.goal_id, db)

    # Update goal progress
    goal = db.query(StudyGoal).filter(StudyGoal.id == entry.goal_id).first()
    if goal:
        total = db.query(StudyCalendarEntry).filter(StudyCalendarEntry.goal_id == goal.id).count()
        completed = db.query(StudyCalendarEntry).filter(
            StudyCalendarEntry.goal_id == goal.id, StudyCalendarEntry.status == "completed"
        ).count()
        goal.completed_units = completed
        goal.completion_pct = round(completed / total * 100, 1) if total else 0
        goal.total_study_minutes = (goal.total_study_minutes or 0) + entry.duration_mins

    db.commit()

    return {
        "message": "Session completed",
        "streak": streak.current_streak,
        "completion_pct": goal.completion_pct if goal else 0,
    }


@router.post("/sessions/{entry_id}/skip")
def skip_session(
    entry_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Skip a study session."""
    entry = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.id == entry_id,
            StudyGoal.student_id == student.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Session not found")

    entry.status = "skipped"
    db.commit()
    return {"message": "Session skipped"}


# ═══════════════════════════════════════════════════════════
# MCQ
# ═══════════════════════════════════════════════════════════

@router.post("/mcq/generate", response_model=McqGenerateResponse)
def generate_mcq(
    payload: McqGenerateRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Generate or retrieve cached MCQs for a calendar entry."""
    entry = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.id == payload.calendar_entry_id,
            StudyGoal.student_id == student.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Calendar entry not found")

    mcq_bank = get_or_generate_mcq(entry, db)
    if not mcq_bank:
        raise HTTPException(500, "Failed to generate MCQs. Try again later.")

    db.commit()

    # Get goal pass threshold
    goal = db.query(StudyGoal).filter(StudyGoal.id == entry.goal_id).first()
    threshold = goal.pass_threshold if goal else 60.0

    questions = []
    for i, q in enumerate(mcq_bank.questions or []):
        questions.append(McqQuestionResponse(
            index=i,
            question=q.get("question", ""),
            options=q.get("options", []),
            difficulty=q.get("difficulty"),
        ))

    return McqGenerateResponse(
        mcq_bank_id=mcq_bank.id,
        topic=entry.topic_title,
        questions=questions,
        pass_threshold=threshold,
        time_limit_mins=10,
    )


@router.post("/mcq/submit", response_model=McqSubmitResponse)
def submit_mcq(
    payload: McqSubmitRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Submit MCQ answers and get results."""
    entry = (
        db.query(StudyCalendarEntry)
        .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
        .filter(
            StudyCalendarEntry.id == payload.calendar_entry_id,
            StudyGoal.student_id == student.id,
        )
        .first()
    )
    if not entry:
        raise HTTPException(404, "Calendar entry not found")

    mcq_bank = db.query(StudyMcqBank).filter(StudyMcqBank.id == payload.mcq_bank_id).first()
    if not mcq_bank:
        raise HTTPException(404, "MCQ bank not found")

    # Score
    answers_dicts = [{"question_index": a.question_index, "selected": a.selected} for a in payload.answers]
    score, results = score_mcq_attempt(mcq_bank, answers_dicts)

    # Get threshold
    goal = db.query(StudyGoal).filter(StudyGoal.id == entry.goal_id).first()
    threshold = goal.pass_threshold if goal else 60.0
    passed = score >= threshold

    # Save attempt
    attempt = StudyMcqAttempt(
        student_id=student.id,
        calendar_entry_id=entry.id,
        mcq_bank_id=mcq_bank.id,
        answers=answers_dicts,
        score=score,
        passed=passed,
    )
    db.add(attempt)

    # Update entry
    entry.mcq_score = score
    entry.mcq_passed = passed

    revision_date = None
    if passed:
        entry.status = "completed"
        entry.completed_at = datetime.now(timezone.utc)
        message = f"Great job! You passed with {score}%."

        # Update streak
        streak = update_streak(student.id, db)
        check_and_award_badges(student.id, entry.goal_id, db)

        # Update goal progress
        if goal:
            total = db.query(StudyCalendarEntry).filter(StudyCalendarEntry.goal_id == goal.id).count()
            completed = db.query(StudyCalendarEntry).filter(
                StudyCalendarEntry.goal_id == goal.id, StudyCalendarEntry.status == "completed"
            ).count()
            goal.completed_units = completed
            goal.completion_pct = round(completed / total * 100, 1) if total else 0
            goal.total_study_minutes = (goal.total_study_minutes or 0) + entry.duration_mins
    else:
        entry.status = "completed"  # Mark as completed even on fail
        entry.completed_at = datetime.now(timezone.utc)

        # Schedule revision 3 days from now
        revision_date = date.today() + timedelta(days=3)
        if goal and revision_date <= goal.target_date:
            existing_rev = db.query(StudyCalendarEntry).filter(
                StudyCalendarEntry.goal_id == entry.goal_id,
                StudyCalendarEntry.chapter_id == entry.chapter_id,
                StudyCalendarEntry.is_revision == True,
                StudyCalendarEntry.status == "pending",
            ).first()

            if not existing_rev:
                sorted_slots = sorted(goal.time_slots, key=lambda s: (s.start_time.hour, s.start_time.minute))
                slot_label = sorted_slots[0].label if sorted_slots else "AM"

                rev_entry = StudyCalendarEntry(
                    goal_id=entry.goal_id,
                    plan_date=revision_date,
                    slot_label=slot_label,
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
                db.add(rev_entry)

        message = f"You scored {score}%. A revision session has been scheduled. Review and try again!"

    db.commit()

    result_details = [McqResultDetail(**r) for r in results]
    session_status = "completed" if passed else "revision"

    return McqSubmitResponse(
        score=score,
        passed=passed,
        pass_threshold=threshold,
        results=result_details,
        session_status=session_status,
        revision_scheduled_date=revision_date if not passed else None,
        message=message,
    )


# ═══════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════

@router.get("/goals/{goal_id}/progress", response_model=ProgressResponse)
def get_progress(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get progress stats for a goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    today = date.today()
    all_entries = db.query(StudyCalendarEntry).filter(StudyCalendarEntry.goal_id == goal_id).all()

    total = len(all_entries)
    completed = [e for e in all_entries if e.status == "completed"]
    completed_count = len(completed)
    total_mins = sum(e.duration_mins for e in completed)
    mcq_scores = [e.mcq_score for e in all_entries if e.mcq_score is not None]
    avg_score = round(sum(mcq_scores) / len(mcq_scores), 1) if mcq_scores else 0
    days_remaining = max(0, (goal.target_date - today).days)

    # On track?
    if total > 0 and days_remaining > 0:
        pending = sum(1 for e in all_entries if e.status == "pending")
        on_track = pending <= days_remaining * len(goal.time_slots)
    else:
        on_track = completed_count >= total if total > 0 else True

    overall = OverallProgress(
        total_units=total,
        completed_units=completed_count,
        completion_pct=round(completed_count / total * 100, 1) if total else 0,
        total_hours_studied=round(total_mins / 60, 1),
        avg_mcq_score=avg_score,
        days_remaining=days_remaining,
        on_track=on_track,
    )

    # Weekly data (last 7 days)
    weekly_data = []
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        day_entries = [e for e in all_entries if e.plan_date == d]
        day_completed = [e for e in day_entries if e.status == "completed"]
        day_scores = [e.mcq_score for e in day_entries if e.mcq_score is not None]
        weekly_data.append(WeeklyDataPoint(
            date=d,
            completed=len(day_completed),
            total=len(day_entries),
            avg_score=round(sum(day_scores) / len(day_scores), 1) if day_scores else 0,
        ))

    # Subject breakdown
    subject_map = defaultdict(lambda: {"total": 0, "completed": 0, "scores": []})
    for e in all_entries:
        key = e.subject_name or "Unknown"
        subject_map[key]["total"] += 1
        if e.status == "completed":
            subject_map[key]["completed"] += 1
        if e.mcq_score is not None:
            subject_map[key]["scores"].append(e.mcq_score)

    subject_breakdown = []
    for subj, data in subject_map.items():
        subject_breakdown.append(SubjectBreakdown(
            subject=subj,
            total=data["total"],
            completed=data["completed"],
            avg_score=round(sum(data["scores"]) / len(data["scores"]), 1) if data["scores"] else 0,
        ))

    return ProgressResponse(
        goal_id=goal_id,
        overall=overall,
        weekly_data=weekly_data,
        subject_breakdown=subject_breakdown,
    )


# ═══════════════════════════════════════════════════════════
# STREAK & BADGES
# ═══════════════════════════════════════════════════════════

@router.get("/streak", response_model=StreakResponse)
def get_streak(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get current streak info."""
    streak = db.query(StudyStreak).filter(StudyStreak.student_id == student.id).first()

    if not streak:
        return StreakResponse(
            current_streak=0, longest_streak=0,
            is_active_today=False, streak_history=[],
        )

    now_ist = datetime.now(timezone.utc) + _IST_OFFSET
    today_ist = now_ist.date()
    is_active = streak.last_study_date == today_ist

    # Build 30-day history
    history = []
    for i in range(29, -1, -1):
        d = today_ist - timedelta(days=i)
        studied = (
            db.query(StudyCalendarEntry)
            .join(StudyGoal, StudyGoal.id == StudyCalendarEntry.goal_id)
            .filter(
                StudyGoal.student_id == student.id,
                StudyCalendarEntry.status == "completed",
                StudyCalendarEntry.plan_date == d,
            )
            .first()
        ) is not None
        history.append(StreakHistoryDay(date=d, studied=studied))

    return StreakResponse(
        current_streak=streak.current_streak,
        longest_streak=streak.longest_streak,
        last_study_date=streak.last_study_date,
        is_active_today=is_active,
        streak_history=history,
    )


@router.get("/badges", response_model=list[BadgeResponse])
def get_badges(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get all badges earned by the student."""
    badges = (
        db.query(StudyBadge)
        .filter(StudyBadge.student_id == student.id)
        .order_by(StudyBadge.earned_at.desc())
        .all()
    )
    return [BadgeResponse.model_validate(b) for b in badges]


# ═══════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════

@router.get("/goals/{goal_id}/report", response_model=GoalReportResponse)
def get_goal_report(
    goal_id: int,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get final completion report for a goal."""
    goal = db.query(StudyGoal).filter(
        StudyGoal.id == goal_id, StudyGoal.student_id == student.id
    ).first()
    if not goal:
        raise HTTPException(404, "Goal not found")

    all_entries = db.query(StudyCalendarEntry).filter(StudyCalendarEntry.goal_id == goal_id).all()
    completed = [e for e in all_entries if e.status == "completed"]
    total_mins = sum(e.duration_mins for e in completed)
    mcq_attempts = db.query(StudyMcqAttempt).filter(
        StudyMcqAttempt.student_id == student.id,
        StudyMcqAttempt.calendar_entry_id.in_([e.id for e in all_entries]),
    ).all()

    # Subject scores
    subject_map = defaultdict(lambda: {"scores": [], "mins": 0, "sessions": 0})
    for e in completed:
        key = e.subject_name or "Unknown"
        subject_map[key]["mins"] += e.duration_mins
        subject_map[key]["sessions"] += 1
        if e.mcq_score is not None:
            subject_map[key]["scores"].append(e.mcq_score)

    subject_scores = []
    for subj, data in subject_map.items():
        subject_scores.append(SubjectScoreReport(
            subject=subj,
            avg_score=round(sum(data["scores"]) / len(data["scores"]), 1) if data["scores"] else 0,
            hours=round(data["mins"] / 60, 1),
            sessions_completed=data["sessions"],
        ))

    # Streak stats
    streak = db.query(StudyStreak).filter(StudyStreak.student_id == student.id).first()
    study_days = len(set(e.plan_date for e in completed))

    # Badges for this goal
    badges = db.query(StudyBadge).filter(
        StudyBadge.student_id == student.id,
        StudyBadge.goal_id == goal_id,
    ).all()

    exam_name = goal.exam.name if goal.exam else goal.custom_goal_name
    mcq_scores = [a.score for a in mcq_attempts]

    return GoalReportResponse(
        goal_id=goal.id,
        exam_name=exam_name,
        status=goal.status,
        started_at=goal.created_at.date() if goal.created_at else date.today(),
        completed_at=goal.updated_at.date() if goal.status == "completed" and goal.updated_at else None,
        completion_pct=goal.completion_pct or 0,
        total_hours_studied=round(total_mins / 60, 1),
        total_sessions_completed=len(completed),
        total_mcq_attempted=len(mcq_attempts),
        avg_mcq_score=round(sum(mcq_scores) / len(mcq_scores), 1) if mcq_scores else 0,
        subject_scores=subject_scores,
        streak_stats=StreakStatsReport(
            longest_streak=streak.longest_streak if streak else 0,
            total_study_days=study_days,
        ),
        badges_earned=[BadgeResponse.model_validate(b) for b in badges],
    )
