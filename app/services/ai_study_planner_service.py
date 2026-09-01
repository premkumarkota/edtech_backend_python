"""
AI Study Planner Service.

Collects student context (syllabus, quiz history, planner MCQs, sessions)
and generates personalized study plans and chat responses via LLM.
"""
import logging
import json
import re
from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy.orm import Session, joinedload

from app.models.user import User
from app.models.syllabus import Syllabus
from app.models.quiz import Quiz, QuizAttempt, AttemptStatus
from app.models.session import VideoCallSession, SessionStatus
from app.models.subscription import StudentSubscription, SubscriptionStatus
from app.models.ai_study_planner import AiStudyPlan, AiChatMessage, AiStudentPreference, ChatRole
from app.models.study_planner_v2 import StudyGoal, StudyMcqAttempt, StudyCalendarEntry
from app.services.ai_llm_service import chat_completion, chat_completion_json

logger = logging.getLogger(__name__)

# Scores below this are treated as weak in both Mock Tests and planner quizzes.
_WEAK_SCORE_THRESHOLD = 60.0
_SOURCE_MOCK = "mock_test"
_SOURCE_CHAPTER = "chapter_quiz"
_SOURCE_PLANNER = "planner_quiz"


def _enum_value(value) -> str:
    return getattr(value, "value", value) or ""


def _quiz_score_pct(attempt: QuizAttempt) -> float:
    if attempt.percentage is not None:
        return round(float(attempt.percentage), 1)
    if attempt.total_marks:
        return round((attempt.score or 0) / attempt.total_marks * 100, 1)
    return 0.0


def _quiz_source(quiz: Optional[Quiz]) -> str:
    quiz_type = (getattr(quiz, "quiz_type", None) or "mock").lower()
    return _SOURCE_CHAPTER if quiz_type == "chapter" else _SOURCE_MOCK


def build_performance_insights(
    records: list[dict],
    threshold: float = _WEAK_SCORE_THRESHOLD,
) -> dict:
    """Merge mock-test and planner-quiz scores into subject + topic insights.

    Each record: subject, chapter?, topic?, score_pct, source, date?
    source: mock_test | chapter_quiz | planner_quiz
    """
    by_subject_source: dict[tuple[str, str], list[float]] = defaultdict(list)
    by_subject: dict[str, dict] = defaultdict(
        lambda: {"scores": [], "sources": set()}
    )

    for raw in records:
        subject = (raw.get("subject") or "Unknown").strip() or "Unknown"
        source = raw.get("source") or "unknown"
        try:
            score = float(raw.get("score_pct") or 0)
        except (TypeError, ValueError):
            score = 0.0
        by_subject_source[(subject, source)].append(score)
        by_subject[subject]["scores"].append(score)
        by_subject[subject]["sources"].add(source)

    def _avg(scores: list[float]) -> Optional[float]:
        if not scores:
            return None
        return round(sum(scores) / len(scores), 1)

    subject_averages = []
    for subject, data in by_subject.items():
        mock_scores = (
            by_subject_source.get((subject, _SOURCE_MOCK), [])
            + by_subject_source.get((subject, _SOURCE_CHAPTER), [])
        )
        planner_scores = by_subject_source.get((subject, _SOURCE_PLANNER), [])
        item = {
            "subject": subject,
            "avg_score": _avg(data["scores"]) or 0.0,
            "attempts": len(data["scores"]),
            "mock_test_avg": _avg(mock_scores),
            "mock_test_attempts": len(mock_scores),
            "planner_quiz_avg": _avg(planner_scores),
            "planner_quiz_attempts": len(planner_scores),
            "sources": sorted(data["sources"]),
        }
        subject_averages.append(item)

    subject_averages.sort(key=lambda x: x["avg_score"])

    weak_areas = []
    for item in subject_averages:
        mock = item["mock_test_avg"]
        planner = item["planner_quiz_avg"]
        is_weak = (
            item["avg_score"] < threshold
            or (mock is not None and mock < threshold)
            or (planner is not None and planner < threshold)
        )
        if not is_weak:
            continue
        reasons = []
        if mock is not None and mock < threshold:
            reasons.append(f"Mock Tests avg {mock}%")
        if planner is not None and planner < threshold:
            reasons.append(f"AI Planner quizzes avg {planner}%")
        if not reasons:
            reasons.append(f"Overall avg {item['avg_score']}%")
        weak_areas.append({**item, "why": "; ".join(reasons)})

    seen_topics: set[tuple] = set()
    topic_weak_areas = []
    for raw in records:
        if raw.get("source") != _SOURCE_PLANNER:
            continue
        try:
            score = float(raw.get("score_pct") or 0)
        except (TypeError, ValueError):
            continue
        if score >= threshold:
            continue
        key = (raw.get("subject"), raw.get("chapter"), raw.get("topic"))
        if key in seen_topics:
            continue
        seen_topics.add(key)
        topic_weak_areas.append({
            "subject": raw.get("subject") or "Unknown",
            "chapter": raw.get("chapter"),
            "topic": raw.get("topic"),
            "score_pct": score,
            "source": _SOURCE_PLANNER,
        })
    topic_weak_areas.sort(key=lambda x: x["score_pct"])

    return {
        "subject_averages": subject_averages,
        "weak_areas": weak_areas,
        "topic_weak_areas": topic_weak_areas[:15],
    }


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

    # 2. Mock Tests / chapter quiz history (admin quizzes)
    attempts = (
        db.query(QuizAttempt)
        .options(
            joinedload(QuizAttempt.quiz).joinedload(Quiz.syllabus),
            joinedload(QuizAttempt.quiz).joinedload(Quiz.chapter),
        )
        .filter(QuizAttempt.student_id == student.id)
        .order_by(QuizAttempt.started_at.desc())
        .limit(20)
        .all()
    )
    performance_records: list[dict] = []
    context["quiz_history"] = []
    for a in attempts:
        quiz = a.quiz
        source = _quiz_source(quiz)
        subject = None
        chapter = None
        if quiz is not None:
            if quiz.syllabus is not None:
                subject = quiz.syllabus.title
            if quiz.chapter is not None:
                chapter = quiz.chapter.title
            if not subject:
                subject = quiz.title
        score_pct = _quiz_score_pct(a)
        row = {
            "quiz_id": a.quiz_id,
            "quiz_title": quiz.title if quiz else "Unknown",
            "quiz_type": (quiz.quiz_type if quiz else "mock") or "mock",
            "source": source,
            "subject": subject or "Unknown",
            "chapter": chapter,
            "score_pct": score_pct,
            "status": _enum_value(a.status),
            "date": str(a.started_at.date()) if a.started_at else "",
        }
        context["quiz_history"].append(row)
        performance_records.append(row)

    # 2b. AI Planner quiz / MCQ performance (study planner v2)
    _attach_planner_performance(context, student, db, performance_records)
    insights = build_performance_insights(performance_records)
    context["subject_averages"] = insights["subject_averages"]
    context["weak_areas"] = insights["weak_areas"]
    context["topic_weak_areas"] = insights["topic_weak_areas"]

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
        {"id": q.id, "title": q.title, "duration_mins": q.duration_mins}
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


def _attach_planner_performance(
    context: dict,
    student: User,
    db: Session,
    performance_records: list[dict],
) -> None:
    """Pull Study Planner v2 MCQ attempts into chat/plan context."""
    context["planner_goals"] = []
    context["planner_mcq_performance"] = []
    try:
        goals = (
            db.query(StudyGoal)
            .filter(StudyGoal.student_id == student.id)
            .order_by(StudyGoal.updated_at.desc())
            .limit(5)
            .all()
        )
        context["planner_goals"] = [
            {
                "id": g.id,
                "name": g.custom_goal_name or "Study goal",
                "status": g.status,
                "completion_pct": g.completion_pct or 0,
                "avg_mcq_score": g.avg_mcq_score or 0,
                "target_date": str(g.target_date) if g.target_date else "",
            }
            for g in goals
        ]

        mcq_attempts = (
            db.query(StudyMcqAttempt)
            .options(joinedload(StudyMcqAttempt.calendar_entry))
            .filter(StudyMcqAttempt.student_id == student.id)
            .order_by(StudyMcqAttempt.attempted_at.desc())
            .limit(40)
            .all()
        )
        rows = []
        for a in mcq_attempts:
            entry = a.calendar_entry
            subject = (entry.subject_name if entry else None) or "Unknown"
            chapter = entry.chapter_name if entry else None
            topic = None
            if entry is not None:
                topic = entry.subtopic_title or entry.topic_title
            row = {
                "source": _SOURCE_PLANNER,
                "subject": subject,
                "chapter": chapter,
                "topic": topic,
                "score_pct": round(float(a.score or 0), 1),
                "passed": bool(a.passed),
                "date": str(a.attempted_at.date()) if a.attempted_at else "",
                "goal_id": entry.goal_id if entry else None,
            }
            rows.append(row)
            performance_records.append(row)
        context["planner_mcq_performance"] = rows[:20]
    except Exception:
        logger.exception("Failed to attach planner quiz performance to AI context")


def _format_student_context_for_llm(context: dict) -> str:
    """Serialize collected context so chat and daily plans share the same facts."""
    return f"""
Student: {context['student_name']}
Subjects available: {json.dumps([s['title'] for s in context['subjects']])}
Mock Tests / chapter quizzes (recent): {json.dumps(context['quiz_history'][:10], default=str)}
AI Planner quizzes (recent): {json.dumps(context.get('planner_mcq_performance') or [], default=str)}
Active study goals: {json.dumps(context.get('planner_goals') or [], default=str)}
Performance by subject (Mock Tests + AI Planner): {json.dumps(context.get('subject_averages') or [], default=str)}
Weak areas (use this as the source of truth): {json.dumps(context.get('weak_areas') or [], default=str)}
Weak planner topics: {json.dumps(context.get('topic_weak_areas') or [], default=str)}
Available mock quizzes: {json.dumps(context['available_quizzes'], default=str)}
Recent tutor sessions: {len(context['recent_sessions'])} recorded
Subscription: {json.dumps(context['subscription'], default=str)}
Today's v1 plan: {json.dumps(context.get('today_plan', 'No plan yet'), default=str)}
"""


def _active_planner_goal(student: User, db: Session) -> Optional[StudyGoal]:
    return (
        db.query(StudyGoal)
        .filter(
            StudyGoal.student_id == student.id,
            StudyGoal.status == "active",
        )
        .order_by(StudyGoal.updated_at.desc())
        .first()
    )


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
- Be encouraging but realistic about workload
- Respond in the same language the student uses (default: English)

WEAK AREAS (critical):
- Mock Tests and AI Planner quizzes are SEPARATE data sources. You MUST use BOTH.
- When the student asks about weak areas, progress, or what to improve:
  1. Start from "Weak areas (use this as the source of truth)" and "Performance by subject".
  2. List EVERY weak subject. For each, show Mock Test avg AND AI Planner quiz avg when present.
  3. Then list weak planner topics/chapters if any.
  4. If one source has no attempts, say so explicitly and still report the other.
  5. Do NOT answer from Mock Tests alone when planner quiz data is present.
- Prioritize revision of subjects/topics below 60%.

IMPORTANT: Keep responses under 350 words. Be direct and helpful."""


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
    context_summary = _format_student_context_for_llm(context)

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
- Prioritize weak areas from BOTH Mock Tests and AI Planner quizzes
- Use weak_areas / topic_weak_areas in the student context as the source of truth
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
    context_str = _format_student_context_for_llm(context)
    context_str = f"Daily study time: {study_hours} hours ({int(study_hours * 60)} minutes)\n{context_str}"

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


# ═══════════════════════════════════════════════════════════════════════════════
# Smart Action Extraction
# ═══════════════════════════════════════════════════════════════════════════════

# Intent keywords → (label, route, icon, args_factory)
_INTENT_MAP = [
    (r"\b(weak\s*area|weakness|need more practice|struggling|where.*(weak|improve))\b",
     "View Planner Progress", "/study-planner/progress", "chart", None),
    (r"\b(progress|how\s*am\s*i\s*doing|my\s*stats)\b",
     "View Planner Progress", "/study-planner/progress", "chart", None),
    (r"\b(quiz|quizzes|test|mock|exam|assess|practice)\b",
     "Browse All Quizzes", "/available-tests", "quiz", None),
    (r"\b(learn|study|subject|subjects|lesson|lessons|chapter|syllabus|something new)\b",
     "Explore Subjects", "/learn", "book", None),
    (r"\b(tutor|mentor|teacher|book.?session)\b",
     "Find a Tutor", "/sessions", "video-call", None),
    (r"\b(plan|schedule|today.?s?\s*plan)\b",
     "View Today's Plan", "/ai-planner", "planner", {"tab": "plan"}),
    (r"\b(subscri|premium|upgrade|pricing|pro\b)",
     "View Plans", "/subscription", "crown", None),
    (r"\b(leaderboard|ranking|rank|top\s*students)\b",
     "View Leaderboard", "/rankings", "prize", None),
    (r"\b(result|score|how\s*did\s*i|my\s*score|attempt)\b",
     "View My Results", "/rankings", "prize", None),
    (r"\b(session|upcoming|my\s*session|join)\b",
     "My Sessions", "/sessions", "calendar", None),
    (r"\b(profile|my\s*profile|update\s*details|edit\s*profile)\b",
     "My Profile", "/profile", "user", None),
    (r"\b(setting|preference|reminder|study\s*time)\b",
     "AI Settings", "/ai-preferences", "settings", None),
    (r"\b(notification|alert)\b",
     "Notifications", "/notifications", "bell", None),
    (r"\b(video|watch|lecture)\b",
     "Watch Lessons", "/lesson-contents", "video", None),
    (r"\b(home|go\s*back|dashboard)\b",
     "Go Home", "/dashboard", "home", None),
]


def _truncate_label(text: str, max_len: int = 40) -> str:
    """Shorten a label for action chip display."""
    text = re.sub(r"\*+", "", text).strip()
    text = re.split(r"\s+(?:to|for|and|—|-)\s+", text, maxsplit=1)[0].strip()
    if len(text) > max_len:
        text = text[:max_len - 1].rstrip() + "…"
    return text


def extract_smart_actions(
    ai_reply: str,
    user_message: str,
    db: Session,
    student: User,
) -> list[dict]:
    """
    Parse AI reply for [quiz:ID], [lesson:ID], [session] references,
    validate IDs exist in DB, and add intent-based navigation actions.
    Returns list of action dicts (max 3).
    """
    actions: list[dict] = []
    seen_routes: set[str] = set()

    def add(action: dict):
        if len(actions) >= 3:
            return
        key = f"{action['route']}:{action.get('args', {}).get('quizId', '')}:{action.get('args', {}).get('syllabusId', '')}"
        if key in seen_routes:
            return
        seen_routes.add(key)
        actions.append(action)

    # 1. Parse [quiz:ID] — validate quiz exists
    for m in re.finditer(r"\[quiz:(\d+)\]\s*([^\n\[]*)", ai_reply):
        quiz_id = int(m.group(1))
        quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
        if quiz:
            label = _truncate_label(m.group(2)) or quiz.title
            add({
                "type": "quiz",
                "label": f"Start: {label}",
                "route": "/quiz-take",
                "args": {"quizId": quiz_id},
                "icon": "quiz",
            })

    # 2. Parse [lesson:ID] — validate syllabus exists
    for m in re.finditer(r"\[lesson:(\d+)\]\s*([^\n\[]*)", ai_reply):
        lesson_id = int(m.group(1))
        syllabus = db.query(Syllabus).filter(Syllabus.id == lesson_id).first()
        if syllabus:
            label = _truncate_label(m.group(2)) or syllabus.title
            add({
                "type": "lesson",
                "label": f"Open: {label}",
                "route": "/course-content",
                "args": {"syllabusId": lesson_id, "title": label},
                "icon": "book",
            })

    # 3. Parse [session] — book a session
    if "[session]" in ai_reply:
        add({
            "type": "session",
            "label": "Book a Session",
            "route": "/sessions",
            "args": {},
            "icon": "video-call",
        })

    # 4. Intent-based actions from user message + AI reply (fill remaining slots)
    intent_source = f"{user_message} {ai_reply}".lower()
    active_goal = None
    try:
        active_goal = _active_planner_goal(student, db)
    except Exception:
        logger.exception("Could not load planner goal for chat actions")

    for pattern, label, route, icon, args in _INTENT_MAP:
        if len(actions) >= 3:
            break
        if re.search(pattern, intent_source, re.IGNORECASE):
            # Don't duplicate if we already have a parsed action for same route
            if any(a["route"] == route for a in actions):
                continue
            resolved_args = dict(args or {})
            if route == "/study-planner/progress" and active_goal is not None:
                resolved_args["goalId"] = active_goal.id
            add({
                "type": "navigate",
                "label": label,
                "route": route,
                "args": resolved_args,
                "icon": icon,
            })

    return actions


def clean_ai_reply(reply: str) -> str:
    """Strip [type:id] markers from the AI reply for clean display."""
    reply = re.sub(r"\[quiz:\d+\]\s*", "", reply)
    reply = re.sub(r"\[lesson:\d+\]\s*", "", reply)
    reply = re.sub(r"\[session\]\s*", "", reply)
    return reply.strip()
