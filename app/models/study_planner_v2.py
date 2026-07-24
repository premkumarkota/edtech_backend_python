"""
Study Planner V2 Models.

Goal-based study planning with calendar generation, MCQ testing,
progress tracking, streaks, and achievements.
"""
import enum
from datetime import datetime, timezone, time as dt_time, date

from sqlalchemy import (
    Column, Integer, String, Text, Float, Boolean, Date,
    DateTime, Time, ForeignKey, JSON, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


# ═══════════════════════════════════════════════════════════
# ADMIN-MANAGED: Goal/Exam Templates
# ═══════════════════════════════════════════════════════════

class GoalExam(Base):
    """Predefined exam/goal templates managed by admin."""
    __tablename__ = "goal_exams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    code = Column(String(30), unique=True, nullable=False)
    description = Column(Text, nullable=True)
    icon_url = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    display_order = Column(Integer, default=0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    category = relationship("Category", foreign_keys=[category_id])
    subjects = relationship("GoalExamSubject", back_populates="exam", cascade="all, delete-orphan")


class GoalExamSubject(Base):
    """Maps syllabus subjects to an exam template."""
    __tablename__ = "goal_exam_subjects"
    __table_args__ = (UniqueConstraint("exam_id", "syllabus_id", name="uq_exam_syllabus"),)

    id = Column(Integer, primary_key=True, index=True)
    exam_id = Column(Integer, ForeignKey("goal_exams.id", ondelete="CASCADE"), nullable=False)
    syllabus_id = Column(Integer, ForeignKey("syllabus.id", ondelete="CASCADE"), nullable=False)
    is_required = Column(Boolean, default=False)
    weightage = Column(Float, default=1.0)

    exam = relationship("GoalExam", back_populates="subjects")
    syllabus = relationship("Syllabus", foreign_keys=[syllabus_id])


# ═══════════════════════════════════════════════════════════
# STUDENT-OWNED: Study Goals
# ═══════════════════════════════════════════════════════════

class GoalStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class PlanningMode(str, enum.Enum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class StudyGoal(Base):
    """A student's study goal with configuration and progress tracking."""
    __tablename__ = "study_goals"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    exam_id = Column(Integer, ForeignKey("goal_exams.id"), nullable=True)
    custom_goal_name = Column(String(100), nullable=True)
    target_date = Column(Date, nullable=False)
    daily_study_hours = Column(Float, nullable=False, default=2.0)
    planning_mode = Column(String(10), nullable=False, default="daily")
    status = Column(String(20), nullable=False, default="active")
    total_study_units = Column(Integer, default=0)
    completed_units = Column(Integer, default=0)
    completion_pct = Column(Float, default=0.0)
    total_study_minutes = Column(Integer, default=0)
    avg_mcq_score = Column(Float, default=0.0)
    pass_threshold = Column(Float, default=60.0)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    student = relationship("User", foreign_keys=[student_id])
    exam = relationship("GoalExam", foreign_keys=[exam_id])
    subjects = relationship("StudyGoalSubject", back_populates="goal", cascade="all, delete-orphan")
    time_slots = relationship("StudyTimeSlot", back_populates="goal", cascade="all, delete-orphan")
    calendar_entries = relationship("StudyCalendarEntry", back_populates="goal", cascade="all, delete-orphan")
    badges = relationship("StudyBadge", back_populates="goal", cascade="all, delete-orphan")


class StudyGoalSubject(Base):
    """Subjects selected by student for a goal."""
    __tablename__ = "study_goal_subjects"
    __table_args__ = (UniqueConstraint("goal_id", "syllabus_id", name="uq_goal_syllabus"),)

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("study_goals.id", ondelete="CASCADE"), nullable=False)
    syllabus_id = Column(Integer, ForeignKey("syllabus.id", ondelete="CASCADE"), nullable=False)

    goal = relationship("StudyGoal", back_populates="subjects")
    syllabus = relationship("Syllabus", foreign_keys=[syllabus_id])


# ═══════════════════════════════════════════════════════════
# TIME SLOTS
# ═══════════════════════════════════════════════════════════

class StudyTimeSlot(Base):
    """Time slots configured by student for a goal."""
    __tablename__ = "study_time_slots"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("study_goals.id", ondelete="CASCADE"), nullable=False)
    label = Column(String(10), nullable=False, default="AM")
    start_time = Column(Time, nullable=False)
    end_time = Column(Time, nullable=False)

    goal = relationship("StudyGoal", back_populates="time_slots")


# ═══════════════════════════════════════════════════════════
# CALENDAR ENTRIES
# ═══════════════════════════════════════════════════════════

class EntryStatus(str, enum.Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    REVISION = "revision"


class StudyCalendarEntry(Base):
    """Individual study session in the generated calendar."""
    __tablename__ = "study_calendar_entries"

    id = Column(Integer, primary_key=True, index=True)
    goal_id = Column(Integer, ForeignKey("study_goals.id", ondelete="CASCADE"), nullable=False)
    plan_date = Column(Date, nullable=False)
    slot_label = Column(String(10), nullable=False)
    slot_order = Column(Integer, default=0)
    syllabus_id = Column(Integer, ForeignKey("syllabus.id"), nullable=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    topic_title = Column(String(200), nullable=False)
    subject_name = Column(String(100), nullable=True)
    chapter_name = Column(String(200), nullable=True)
    difficulty = Column(String(10), default="medium")
    duration_mins = Column(Integer, nullable=False)
    content_ids = Column(JSON, default=list)
    status = Column(String(20), default="pending")
    is_revision = Column(Boolean, default=False)
    original_date = Column(Date, nullable=True)
    mcq_score = Column(Float, nullable=True)
    mcq_passed = Column(Boolean, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    goal = relationship("StudyGoal", back_populates="calendar_entries")
    syllabus = relationship("Syllabus", foreign_keys=[syllabus_id])
    chapter = relationship("Chapter", foreign_keys=[chapter_id])
    mcq_attempts = relationship("StudyMcqAttempt", back_populates="calendar_entry", cascade="all, delete-orphan")


# ═══════════════════════════════════════════════════════════
# MCQ
# ═══════════════════════════════════════════════════════════

class StudyMcqBank(Base):
    """Cached AI-generated MCQs for a chapter/topic."""
    __tablename__ = "study_mcq_banks"
    __table_args__ = (UniqueConstraint("chapter_id", "topic_title", name="uq_mcq_chapter_topic"),)

    id = Column(Integer, primary_key=True, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id"), nullable=True)
    topic_title = Column(String(200), nullable=False)
    questions = Column(JSON, nullable=False)
    generated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class StudyMcqAttempt(Base):
    """Student's MCQ attempt for a calendar entry."""
    __tablename__ = "study_mcq_attempts"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    calendar_entry_id = Column(Integer, ForeignKey("study_calendar_entries.id", ondelete="CASCADE"), nullable=False)
    mcq_bank_id = Column(Integer, ForeignKey("study_mcq_banks.id"), nullable=True)
    answers = Column(JSON, default=list)
    score = Column(Float, nullable=False, default=0.0)
    passed = Column(Boolean, nullable=False, default=False)
    attempted_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    student = relationship("User", foreign_keys=[student_id])
    calendar_entry = relationship("StudyCalendarEntry", back_populates="mcq_attempts")
    mcq_bank = relationship("StudyMcqBank", foreign_keys=[mcq_bank_id])


# ═══════════════════════════════════════════════════════════
# STREAKS
# ═══════════════════════════════════════════════════════════

class StudyStreak(Base):
    """Tracks student's study streak across all goals."""
    __tablename__ = "study_streaks"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), unique=True, nullable=False)
    current_streak = Column(Integer, default=0)
    longest_streak = Column(Integer, default=0)
    last_study_date = Column(Date, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    student = relationship("User", foreign_keys=[student_id])


# ═══════════════════════════════════════════════════════════
# BADGES / ACHIEVEMENTS
# ═══════════════════════════════════════════════════════════

class StudyBadge(Base):
    """Achievement badges earned by students."""
    __tablename__ = "study_badges"
    __table_args__ = (UniqueConstraint("student_id", "badge_type", "goal_id", name="uq_student_badge_goal"),)

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    goal_id = Column(Integer, ForeignKey("study_goals.id", ondelete="SET NULL"), nullable=True)
    badge_type = Column(String(50), nullable=False)
    badge_name = Column(String(100), nullable=False)
    badge_icon = Column(String(50), nullable=True)
    earned_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    student = relationship("User", foreign_keys=[student_id])
    goal = relationship("StudyGoal", back_populates="badges")
