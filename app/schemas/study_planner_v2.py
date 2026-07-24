"""
Pydantic schemas for Study Planner V2 endpoints.
"""
from datetime import datetime, date, time as dt_time
from typing import Optional, List
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# EXAM (Admin)
# ═══════════════════════════════════════════════════════════

class ExamSubjectResponse(BaseModel):
    syllabus_id: int
    title: str
    is_required: bool = False
    weightage: float = 1.0

    class Config:
        from_attributes = True


class ExamResponse(BaseModel):
    id: int
    name: str
    code: str
    description: Optional[str] = None
    icon_url: Optional[str] = None
    category_id: Optional[int] = None
    is_active: bool = True
    display_order: int = 0
    subjects: List[ExamSubjectResponse] = []

    class Config:
        from_attributes = True


class ExamCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    code: str = Field(..., min_length=1, max_length=30)
    description: Optional[str] = None
    icon_url: Optional[str] = None
    category_id: Optional[int] = None
    display_order: int = 0


class ExamUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    icon_url: Optional[str] = None
    is_active: Optional[bool] = None
    display_order: Optional[int] = None


class ExamSubjectMapRequest(BaseModel):
    syllabus_id: int
    is_required: bool = False
    weightage: float = 1.0


# ═══════════════════════════════════════════════════════════
# GOAL SETUP
# ═══════════════════════════════════════════════════════════

class TimeSlotInput(BaseModel):
    label: str = Field(..., max_length=10)
    start_time: str  # "HH:MM"
    end_time: str    # "HH:MM"


class GoalSetupRequest(BaseModel):
    exam_id: Optional[int] = None
    custom_goal_name: Optional[str] = None
    subject_ids: List[int] = Field(..., min_length=1)
    target_date: date
    daily_study_hours: float = Field(..., ge=0.5, le=16.0)
    time_slots: List[TimeSlotInput] = Field(..., min_length=1)
    planning_mode: str = Field(default="daily", pattern=r"^(daily|weekly|monthly)$")
    pass_threshold: float = Field(default=60.0, ge=0, le=100)


class FeasibilityInfo(BaseModel):
    is_feasible: bool
    message: str
    daily_topics_avg: float
    total_hours_required: float
    total_days: int
    buffer_days: int


class GoalSetupResponse(BaseModel):
    goal_id: int
    status: str
    feasibility: FeasibilityInfo
    created_at: datetime

    class Config:
        from_attributes = True


class GoalSubjectInfo(BaseModel):
    syllabus_id: int
    title: str


class GoalListResponse(BaseModel):
    id: int
    exam_name: Optional[str] = None
    custom_goal_name: Optional[str] = None
    exam_icon: Optional[str] = None
    target_date: date
    planning_mode: str
    status: str
    completion_pct: float
    days_remaining: int
    total_study_units: int
    completed_units: int
    subjects: List[GoalSubjectInfo] = []
    created_at: datetime

    class Config:
        from_attributes = True


class GoalUpdateRequest(BaseModel):
    target_date: Optional[date] = None
    daily_study_hours: Optional[float] = Field(None, ge=0.5, le=16.0)
    planning_mode: Optional[str] = None
    status: Optional[str] = None
    pass_threshold: Optional[float] = Field(None, ge=0, le=100)


# ═══════════════════════════════════════════════════════════
# CALENDAR
# ═══════════════════════════════════════════════════════════

class CalendarEntryResponse(BaseModel):
    id: int
    plan_date: date
    slot_label: str
    slot_order: int = 0
    topic_title: str
    subject_name: Optional[str] = None
    chapter_name: Optional[str] = None
    syllabus_id: Optional[int] = None
    chapter_id: Optional[int] = None
    difficulty: str = "medium"
    duration_mins: int
    status: str = "pending"
    is_revision: bool = False
    original_date: Optional[date] = None
    mcq_score: Optional[float] = None
    mcq_passed: Optional[bool] = None
    content_ids: List[int] = []
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CalendarSummary(BaseModel):
    total_entries: int
    completed: int
    pending: int
    skipped: int
    revision: int


class CalendarResponse(BaseModel):
    goal_id: int
    entries: List[CalendarEntryResponse]
    summary: CalendarSummary


class ContentInfo(BaseModel):
    id: int
    title: str
    content_type: str
    file_url: str


class TodaySessionResponse(BaseModel):
    id: int
    slot_label: str
    slot_time: str
    topic_title: str
    subject_name: Optional[str] = None
    chapter_name: Optional[str] = None
    difficulty: str = "medium"
    duration_mins: int
    status: str = "pending"
    is_revision: bool = False
    contents: List[ContentInfo] = []
    mcq_available: bool = True
    mcq_score: Optional[float] = None
    mcq_passed: Optional[bool] = None


class TodayPlanResponse(BaseModel):
    date: date
    goal_id: int
    sessions: List[TodaySessionResponse]
    daily_progress: dict


# ═══════════════════════════════════════════════════════════
# SESSION & MCQ
# ═══════════════════════════════════════════════════════════

class SessionStartResponse(BaseModel):
    entry_id: int
    status: str
    started_at: datetime
    contents: List[ContentInfo] = []
    message: str = ""


class McqGenerateRequest(BaseModel):
    calendar_entry_id: int


class McqQuestionResponse(BaseModel):
    index: int
    question: str
    options: List[str]
    difficulty: Optional[str] = None


class McqGenerateResponse(BaseModel):
    mcq_bank_id: int
    topic: str
    questions: List[McqQuestionResponse]
    pass_threshold: float = 60.0
    time_limit_mins: int = 10


class McqAnswerInput(BaseModel):
    question_index: int
    selected: str  # "A", "B", "C", "D"


class McqSubmitRequest(BaseModel):
    calendar_entry_id: int
    mcq_bank_id: int
    answers: List[McqAnswerInput]


class McqResultDetail(BaseModel):
    question_index: int
    question: str
    selected: str
    correct: str
    is_correct: bool
    explanation: Optional[str] = None


class McqSubmitResponse(BaseModel):
    score: float
    passed: bool
    pass_threshold: float
    results: List[McqResultDetail]
    session_status: str
    revision_scheduled_date: Optional[date] = None
    message: str = ""


# ═══════════════════════════════════════════════════════════
# PROGRESS
# ═══════════════════════════════════════════════════════════

class WeeklyDataPoint(BaseModel):
    date: date
    completed: int
    total: int
    avg_score: float


class SubjectBreakdown(BaseModel):
    subject: str
    total: int
    completed: int
    avg_score: float


class OverallProgress(BaseModel):
    total_units: int
    completed_units: int
    completion_pct: float
    total_hours_studied: float
    avg_mcq_score: float
    days_remaining: int
    on_track: bool


class ProgressResponse(BaseModel):
    goal_id: int
    overall: OverallProgress
    weekly_data: List[WeeklyDataPoint] = []
    subject_breakdown: List[SubjectBreakdown] = []


# ═══════════════════════════════════════════════════════════
# STREAK & BADGES
# ═══════════════════════════════════════════════════════════

class StreakHistoryDay(BaseModel):
    date: date
    studied: bool


class StreakResponse(BaseModel):
    current_streak: int
    longest_streak: int
    last_study_date: Optional[date] = None
    is_active_today: bool = False
    streak_history: List[StreakHistoryDay] = []


class BadgeResponse(BaseModel):
    badge_type: str
    badge_name: str
    badge_icon: Optional[str] = None
    goal_id: Optional[int] = None
    earned_at: datetime

    class Config:
        from_attributes = True


# ═══════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════

class SubjectScoreReport(BaseModel):
    subject: str
    avg_score: float
    hours: float
    sessions_completed: int


class StreakStatsReport(BaseModel):
    longest_streak: int
    total_study_days: int


class GoalReportResponse(BaseModel):
    goal_id: int
    exam_name: Optional[str] = None
    status: str
    started_at: date
    completed_at: Optional[date] = None
    completion_pct: float
    total_hours_studied: float
    total_sessions_completed: int
    total_mcq_attempted: int
    avg_mcq_score: float
    subject_scores: List[SubjectScoreReport] = []
    streak_stats: StreakStatsReport
    badges_earned: List[BadgeResponse] = []


class GenerateStatusResponse(BaseModel):
    status: str
    total_units: int
    message: str
