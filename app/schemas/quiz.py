from pydantic import BaseModel, validator
from typing import Optional, List
from datetime import datetime


# ── Quiz ──────────────────────────────────────────────────────────

class QuizCreate(BaseModel):
    category_id: int
    syllabus_id: Optional[int] = None
    chapter_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    duration_mins: int = 30
    pass_marks: int = 0


class QuizResponse(BaseModel):
    id: int
    category_id: int
    syllabus_id: Optional[int] = None
    chapter_id: Optional[int] = None
    title: str
    description: Optional[str] = None
    duration_mins: int
    total_marks: int
    pass_marks: int
    status: str
    created_by: Optional[int] = None
    question_count: int = 0
    created_at: datetime

    class Config:
        from_attributes = True


# ── QuizQuestion ──────────────────────────────────────────────────

class QuizQuestionResponse(BaseModel):
    id: int
    quiz_id: int
    question_text: str
    option_a: str
    option_b: str
    option_c: Optional[str] = None
    option_d: Optional[str] = None
    marks: int
    order_index: int
    # Note: correct_option NOT exposed to students (only for admin/result)

    class Config:
        from_attributes = True


class QuizQuestionWithAnswer(QuizQuestionResponse):
    """Extended version sent after attempt is submitted (includes correct answer)."""
    correct_option: str
    explanation: Optional[str] = None


# ── Quiz Attempt ──────────────────────────────────────────────────

class AnswerSubmit(BaseModel):
    question_id: int
    selected_option: str  # 'A', 'B', 'C', or 'D'

    @validator("selected_option")
    def validate_option(cls, v):
        if v.upper() not in ("A", "B", "C", "D"):
            raise ValueError("selected_option must be A, B, C, or D")
        return v.upper()


class QuizSubmit(BaseModel):
    answers: List[AnswerSubmit]
    time_taken_secs: Optional[int] = None


class QuizAttemptResponse(BaseModel):
    id: int
    quiz_id: int
    student_id: int
    status: str
    score: int
    total_marks: int
    percentage: Optional[float] = None
    passed: Optional[bool] = None
    started_at: datetime
    completed_at: Optional[datetime] = None
    time_taken_secs: Optional[int] = None

    class Config:
        from_attributes = True


class QuizResultResponse(QuizAttemptResponse):
    """Full result including per-question breakdown."""
    quiz_title: str = ""
    answers: List[dict] = []


class LeaderboardEntry(BaseModel):
    user_id: int
    name: str
    profile_image_url: Optional[str] = None
    score: int
    percentage: float
    time_taken_secs: Optional[int]
    rank: int

    class Config:
        from_attributes = True


class GlobalRankEntry(BaseModel):
    user_id: int
    name: str
    profile_image_url: Optional[str] = None
    total_points: int
    rank: int

    class Config:
        from_attributes = True
