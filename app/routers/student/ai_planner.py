"""
Student AI Study Planner Routes.

Endpoints:
    POST /chat           — Send message to AI planner, get response
    GET  /history        — Load chat history (last 50 messages)
    GET  /plan/today     — Get today's study plan
    POST /plan/generate  — Force-generate today's plan
    PUT  /preferences    — Update AI planner preferences
    GET  /preferences    — Get current preferences
    POST /plan/task-complete — Mark a task as completed
"""
from datetime import date, datetime, timezone, time as dt_time

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_onboarded_student
from app.models.user import User
from app.models.ai_study_planner import AiChatMessage, AiStudyPlan, AiStudentPreference, ChatRole
from app.schemas.ai_study_planner import (
    AiChatRequest, AiChatResponse, AiChatMessageResponse,
    AiPreferencesRequest, AiPreferencesResponse,
    AiStudyPlanResponse, StudyPlanTask, TaskCompleteRequest,
)
from app.services.ai_study_planner_service import generate_chat_response, generate_daily_plan

router = APIRouter()


# ── Chat ─────────────────────────────────────────────────────────────────────

@router.post("/chat", response_model=AiChatResponse)
def send_chat_message(
    payload: AiChatRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Send a message to the AI study planner and get a response."""
    # Save user message
    user_msg = AiChatMessage(
        student_id=student.id,
        role=ChatRole.USER.value,
        content=payload.message,
    )
    db.add(user_msg)
    db.flush()

    # Get recent chat history for context
    recent_messages = (
        db.query(AiChatMessage)
        .filter(AiChatMessage.student_id == student.id)
        .order_by(AiChatMessage.created_at.desc())
        .limit(10)
        .all()
    )
    chat_history = [
        {"role": msg.role, "content": msg.content}
        for msg in reversed(recent_messages)
    ]

    # Generate AI response
    ai_reply = generate_chat_response(
        student=student,
        user_message=payload.message,
        db=db,
        chat_history=chat_history,
    )

    # Save assistant message
    assistant_msg = AiChatMessage(
        student_id=student.id,
        role=ChatRole.ASSISTANT.value,
        content=ai_reply,
    )
    db.add(assistant_msg)
    db.commit()
    db.refresh(assistant_msg)

    return AiChatResponse(
        reply=ai_reply,
        message_id=assistant_msg.id,
        metadata=assistant_msg.metadata_json,
    )


@router.get("/history", response_model=list[AiChatMessageResponse])
def get_chat_history(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get the student's chat history (last 50 messages)."""
    messages = (
        db.query(AiChatMessage)
        .filter(AiChatMessage.student_id == student.id)
        .order_by(AiChatMessage.created_at.asc())
        .limit(50)
        .all()
    )
    return [AiChatMessageResponse.model_validate(m) for m in messages]


# ── Study Plan ───────────────────────────────────────────────────────────────

@router.get("/plan/today", response_model=AiStudyPlanResponse | None)
def get_today_plan(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get today's study plan (if generated)."""
    plan = (
        db.query(AiStudyPlan)
        .filter(
            AiStudyPlan.student_id == student.id,
            AiStudyPlan.plan_date == date.today(),
        )
        .first()
    )
    if not plan:
        return None

    tasks = [StudyPlanTask(**t) for t in (plan.tasks or [])]
    return AiStudyPlanResponse(
        id=plan.id,
        plan_date=plan.plan_date,
        tasks=tasks,
        summary=plan.summary,
        completion_pct=plan.completion_pct,
        generated_at=plan.generated_at,
    )


@router.post("/plan/generate", response_model=AiStudyPlanResponse)
def generate_today_plan(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Force-generate (or regenerate) today's study plan."""
    # Get preferences
    pref = db.query(AiStudentPreference).filter(
        AiStudentPreference.student_id == student.id
    ).first()
    study_hours = pref.daily_study_hours if pref else 2.0

    # Delete existing plan for today (regenerate)
    existing = (
        db.query(AiStudyPlan)
        .filter(AiStudyPlan.student_id == student.id, AiStudyPlan.plan_date == date.today())
        .first()
    )
    if existing:
        db.delete(existing)
        db.flush()

    # Generate plan
    plan_data = generate_daily_plan(student, db, study_hours)
    if not plan_data:
        raise HTTPException(status_code=500, detail="Failed to generate study plan")

    new_plan = AiStudyPlan(
        student_id=student.id,
        plan_date=date.today(),
        tasks=plan_data.get("tasks", []),
        summary=plan_data.get("summary"),
    )
    db.add(new_plan)
    db.commit()
    db.refresh(new_plan)

    tasks = [StudyPlanTask(**t) for t in (new_plan.tasks or [])]
    return AiStudyPlanResponse(
        id=new_plan.id,
        plan_date=new_plan.plan_date,
        tasks=tasks,
        summary=new_plan.summary,
        completion_pct=0.0,
        generated_at=new_plan.generated_at,
    )


@router.post("/plan/task-complete")
def mark_task_complete(
    payload: TaskCompleteRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Mark a task in today's plan as completed."""
    plan = (
        db.query(AiStudyPlan)
        .filter(AiStudyPlan.student_id == student.id, AiStudyPlan.plan_date == date.today())
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="No plan for today")

    tasks = plan.tasks or []
    if payload.task_index >= len(tasks):
        raise HTTPException(status_code=400, detail="Invalid task index")

    tasks[payload.task_index]["completed"] = True
    plan.tasks = tasks

    # Recalculate completion
    completed_count = sum(1 for t in tasks if t.get("completed"))
    plan.completion_pct = round(completed_count / len(tasks) * 100, 1) if tasks else 0.0

    db.commit()

    return {"message": "Task marked complete", "completion_pct": plan.completion_pct}


# ── Preferences ──────────────────────────────────────────────────────────────

@router.get("/preferences", response_model=AiPreferencesResponse)
def get_preferences(
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Get AI planner preferences."""
    pref = db.query(AiStudentPreference).filter(
        AiStudentPreference.student_id == student.id
    ).first()

    if not pref:
        # Return defaults
        return AiPreferencesResponse(
            daily_study_hours=2.0,
            reminder_time="08:00",
            evening_reminder=False,
            is_enabled=True,
        )

    return AiPreferencesResponse(
        daily_study_hours=pref.daily_study_hours,
        reminder_time=pref.reminder_time.strftime("%H:%M") if pref.reminder_time else "08:00",
        evening_reminder=pref.evening_reminder,
        is_enabled=pref.is_enabled,
    )


@router.put("/preferences", response_model=AiPreferencesResponse)
def update_preferences(
    payload: AiPreferencesRequest,
    student: User = Depends(get_onboarded_student),
    db: Session = Depends(get_db),
):
    """Update AI planner preferences (reminder time, study hours, etc.)."""
    pref = db.query(AiStudentPreference).filter(
        AiStudentPreference.student_id == student.id
    ).first()

    if not pref:
        pref = AiStudentPreference(student_id=student.id)
        db.add(pref)

    if payload.daily_study_hours is not None:
        pref.daily_study_hours = payload.daily_study_hours
    if payload.reminder_time is not None:
        # Parse "HH:MM" string to time object
        parts = payload.reminder_time.split(":")
        pref.reminder_time = dt_time(int(parts[0]), int(parts[1]))
    if payload.evening_reminder is not None:
        pref.evening_reminder = payload.evening_reminder
    if payload.is_enabled is not None:
        pref.is_enabled = payload.is_enabled

    pref.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(pref)

    return AiPreferencesResponse(
        daily_study_hours=pref.daily_study_hours,
        reminder_time=pref.reminder_time.strftime("%H:%M") if pref.reminder_time else "08:00",
        evening_reminder=pref.evening_reminder,
        is_enabled=pref.is_enabled,
    )
