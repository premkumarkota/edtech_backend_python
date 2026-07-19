"""
Pydantic schemas for AI Study Planner endpoints.
"""
from datetime import datetime, date, time as dt_time
from typing import Optional, List
from pydantic import BaseModel, Field


# ── Preferences ──────────────────────────────────────────────────���───────────

class AiPreferencesRequest(BaseModel):
    daily_study_hours: Optional[float] = Field(None, ge=0.5, le=12.0)
    reminder_time: Optional[str] = None  # "HH:MM" format
    evening_reminder: Optional[bool] = None
    is_enabled: Optional[bool] = None


class AiPreferencesResponse(BaseModel):
    daily_study_hours: float
    reminder_time: str  # "HH:MM"
    evening_reminder: bool
    is_enabled: bool

    class Config:
        from_attributes = True


# ── Chat ─────────────────────────────────────────────────────────────────────

class AiChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


class AiChatMessageResponse(BaseModel):
    id: int
    role: str
    content: str
    metadata_json: Optional[dict] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AiSmartAction(BaseModel):
    """A tappable action button rendered below an AI message."""
    type: str  # "quiz", "lesson", "session", "navigate"
    label: str
    route: str  # Flutter named route e.g. "/quiz-take"
    args: Optional[dict] = None  # route arguments e.g. {"quizId": 5}
    icon: str = "default"  # icon key for frontend icon mapping


class AiChatResponse(BaseModel):
    """Response from AI chat — the assistant's reply."""
    reply: str
    message_id: int
    actions: List[AiSmartAction] = []
    metadata: Optional[dict] = None  # deep links, suggested actions


# ── Study Plan ───────────────────────────────────────────────────────────────

class StudyPlanTask(BaseModel):
    type: str  # "lesson", "quiz", "session", "revision"
    id: Optional[int] = None  # syllabus_id, quiz_id, etc.
    title: str
    description: Optional[str] = None
    completed: bool = False
    deep_link: Optional[str] = None  # route path in app


class AiStudyPlanResponse(BaseModel):
    id: int
    plan_date: date
    tasks: List[StudyPlanTask]
    summary: Optional[str] = None
    completion_pct: float
    generated_at: datetime

    class Config:
        from_attributes = True


class TaskCompleteRequest(BaseModel):
    task_index: int = Field(..., ge=0)
