"""
AI Study Planner Service.

Collects student context (syllabus, quiz history, sessions, subscription)
and generates personalized study plans and chat responses via LLM.
"""
import logging
import json
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.syllabus import Syllabus
from app.models.quiz import Quiz, QuizAttempt, AttemptStatus
from app.models.session import VideoCallSession, SessionStatus
from app.models.subscription import StudentSubscription, SubscriptionStatus
from app.models.ai_study_planner import AiStudyPlan, AiChatMessage, AiStudentPreference, ChatRole
from app.services.ai_llm_service import chat_completion, chat_completion_json

logger = logging.getLogger(__name__)


def _collect_student_context(student: User, db: Session) -> dict:
    """
    Collect all relevant student data for AI context.
    Returns a dict with syllabus, quiz history, sessions, subscription info.
    """
    context = {
        "student_name": student.name or "Student",
        "category_id": student.category_id,
    }

    # 1. Syllabus / Courses available
    syllabi = (
        db.query(Syllabus)
        .filter(Syllabus.category_id == student.category_id, Syllabus.is_active == True)  # noqa: E712
        .all()
    )
    context["subjects"] = []
    for s in syllabi:
        subject_info = {
            "id": s.id,
            "title": s.title,
            "description": s.description or "",
        }
        # Get chapters
        if hasattr(s, 'chapters') and s.chapters:
            subject_info["chapters"] = [
                {"id": ch.id, "title": ch.title, "order": ch.order_index}
                for ch in sorted(s.chapters, key=lambda x: x.order_index or 0)
            ]
        context["subjects"].append(subject_info)

    # 2. Quiz history
    attempts = (
        db.query(QuizAttempt)
        .filter(QuizAttempt.student_id == student.id)
        .order_by(QuizAttempt.started_at.desc())
        .limit(10)
        .all()
    )
    context["quiz_history"] = []
    for a in attempts:
        quiz = db.query(Quiz).filter(Quiz.id == a.quiz_id).first()
        context["quiz_history"].append({
            "quiz_title": quiz.title if quiz else "Unknown",
            "score_pct": round((a.score / a.total_marks * 100) if a.total_marks else 0, 1),
            "status": a.status,
            "date": str(a.started_at.date()) if a.started_at else "",
        })

    # 3. Available quizzes (not yet attempted)
    attempted_quiz_ids = [a.quiz_id for a in attempts]
    available_quizzes = (
        db.query(Quiz)
        .filter(
            Quiz.category_id == student.category_id,
            Quiz.status == "published",
            Quiz.id.notin_(attempted_quiz_ids) if attempted_quiz_ids else True,
        )
        .limit(5)
        .all()
    )
    context["available_quizzes"] = [
        {"id": q.id, "title": q.title, "duration_mins": q.duration_minutes}
        for q in available_quizzes
    ]

    # 4. Session history
    sessions = (
        db.query(VideoCallSession)
        .filter(VideoCallSession.student_id == student.id)
        .order_by(VideoCallSession.scheduled_at.desc())
        .limit(5)
        .all()
    )
    context["recent_sessions"] = [
        {
            "status": s.status,
            "scheduled_at": str(s.scheduled_at) if s.scheduled_at else "",
            "duration_mins": s.actual_duration_mins or s.scheduled_duration_mins,
        }
        for s in sessions
    ]

    # 5. Subscription status
    sub = (
        db.query(StudentSubscription)
        .filter(
            StudentSubscription.student_id == student.id,
            StudentSubscription.status == SubscriptionStatus.ACTIVE.value,
        )
        .first()
    )
    if sub:
        context["subscription"] = {
            "minutes_remaining": max(0, sub.video_call_minutes_total - sub.video_call_minutes_used),
            "expires_at": str(sub.expires_at) if sub.expires_at else "",
        }
    else:
        context["subscription"] = None

    # 6. Today's existing plan (if any)
    today_plan = (
        db.query(AiStudyPlan)
        .filter(AiStudyPlan.student_id == student.id, AiStudyPlan.plan_date == date.today())
        .first()
    )
    if today_plan:
        context["today_plan"] = {
            "tasks": today_plan.tasks or [],
            "completion_pct": today_plan.completion_pct,
        }

    return context


_SYSTEM_PROMPT = """You are MyMentor AI Study Planner — a friendly, encouraging study assistant for students.

Your role:
- Help students plan their daily/weekly study schedule
- Suggest which subjects, chapters, or quizzes to focus on
- Provide motivational support and study tips
- Track their progress and adapt recommendations

Guidelines:
- Be concise and actionable (bullet points preferred)
- When suggesting content, ALWAYS include the specific IDs so the app can create deep links
- Format suggestions as: [type:id] Title — e.g., [lesson:5] Chapter 3: Thermodynamics
- Available types: lesson (syllabus_id), quiz (quiz_id), session (suggest booking)
- Consider the student's available study time and subscription minutes
- If they have weak quiz scores in a subject, prioritize revision of that subject
- Be encouraging but realistic about workload
- Respond in the same language the student uses (default: English)

IMPORTANT: Keep responses under 300 words. Be direct and helpful."""


def generate_chat_response(
    student: User,
    user_message: str,
    db: Session,
    chat_history: list[dict] = None,
) -> str:
    """
    Generate an AI response to the student's chat message.
    Includes student context in the system prompt.
    """
    context = _collect_student_context(student, db)

    # Build context summary for system prompt
    context_summary = f"""
Student: {context['student_name']}
Subjects available: {json.dumps([s['title'] for s in context['subjects']])}
Quiz history (recent): {json.dumps(context['quiz_history'][:5])}
Available quizzes: {json.dumps(context['available_quizzes'])}
Recent sessions: {len(context['recent_sessions'])} completed
Subscription: {json.dumps(context['subscription'])}
Today's plan: {json.dumps(context.get('today_plan', 'No plan yet'))}
"""

    messages = [
        {"role": "system", "content": _SYSTEM_PROMPT + "\n\n--- STUDENT CONTEXT ---\n" + context_summary},
    ]

    # Add recent chat history (last 10 messages for context)
    if chat_history:
        for msg in chat_history[-10:]:
            messages.append({"role": msg["role"], "content": msg["content"]})

    messages.append({"role": "user", "content": user_message})

    response = chat_completion(messages=messages, temperature=0.7, max_tokens=1000)

    if not response:
        return "I'm having trouble connecting right now. Please try again in a moment."

    return response


_PLAN_SYSTEM_PROMPT = """You are MyMentor AI Study Planner. Generate a daily study plan as JSON.

Output MUST be valid JSON with this exact structure:
{
  "tasks": [
    {
      "type": "lesson|quiz|revision|session",
      "id": <integer or null>,
      "title": "Task title",
      "description": "Brief description of what to do",
      "duration_mins": <estimated minutes>
    }
  ],
  "summary": "One sentence summary of today's plan for push notification"
}

Rules:
- Total task durations should match the student's daily_study_hours
- Prioritize weak areas (low quiz scores)
- Include a mix of learning new content + revision + assessment
- Max 5 tasks per day
- IDs must reference real content from the student context
- If no syllabus content is available, suggest general study tips"""


def generate_daily_plan(student: User, db: Session, study_hours: float = 2.0) -> Optional[dict]:
    """
    Generate a daily study plan for the student.
    Returns parsed JSON dict with tasks and summary.
    """
    context = _collect_student_context(student, db)

    context_str = f"""
Student: {context['student_name']}
Daily study time: {study_hours} hours ({int(study_hours * 60)} minutes)
Subjects: {json.dumps(context['subjects'], default=str)}
Quiz history: {json.dumps(context['quiz_history'])}
Available quizzes: {json.dumps(context['available_quizzes'])}
Subscription minutes remaining: {context['subscription']['minutes_remaining'] if context['subscription'] else 'No subscription'}
"""

    messages = [
        {"role": "system", "content": _PLAN_SYSTEM_PROMPT},
        {"role": "user", "content": f"Generate today's study plan for this student:\n{context_str}"},
    ]

    result = chat_completion_json(messages=messages, temperature=0.5, max_tokens=1500)

    if not result:
        # Fallback: return a basic plan
        return {
            "tasks": [
                {
                    "type": "revision",
                    "id": None,
                    "title": "Review your notes",
                    "description": "Spend time reviewing recent materials",
                    "duration_mins": int(study_hours * 60),
                }
            ],
            "summary": "Time to review and consolidate what you've learned!",
        }

    return result
