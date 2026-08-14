import enum
from sqlalchemy import (Column, Integer, String, Text, Boolean, Float,
                         DateTime, ForeignKey, Enum, CHAR, UniqueConstraint)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class QuizStatus(str, enum.Enum):
    DRAFT     = "draft"
    PUBLISHED = "published"
    ARCHIVED  = "archived"


class AttemptStatus(str, enum.Enum):
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    ABANDONED   = "abandoned"


class Quiz(Base):
    """Quiz shell created by admin per category."""
    __tablename__ = "quizzes"

    id            = Column(Integer, primary_key=True, index=True)
    category_id   = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    syllabus_id   = Column(Integer, ForeignKey("syllabus.id"), nullable=True, index=True)
    chapter_id    = Column(Integer, ForeignKey("chapters.id"), nullable=True, index=True)
    title         = Column(String(200), nullable=False)
    description   = Column(Text, nullable=True)
    duration_mins = Column(Integer, default=30)
    total_marks   = Column(Integer, default=0)
    pass_marks    = Column(Integer, default=0)
    # Assessment tier: 'chapter' (formative, from chapter content) or
    # 'mock' (summative, spans chapters/subject/term).
    quiz_type     = Column(String(10), default="mock", nullable=False,
                           server_default="mock", index=True)
    mock_scope    = Column(String(20), nullable=True)   # chapters | subject | term
    term          = Column(String(20), nullable=True)   # quarterly | half_yearly | annual
    # Chapter quiz: if True, a student must score >= pass_marks to complete the
    # chapter; failing forces a retake.
    require_pass  = Column(Boolean, default=False, nullable=False,
                           server_default="false")
    negative_marking = Column(Boolean, default=False, nullable=False,
                              server_default="false")
    status        = Column(
        Enum(QuizStatus, values_callable=lambda x: [e.value for e in x]),
        default=QuizStatus.DRAFT.value,
        nullable=False,
    )
    created_by    = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at    = Column(DateTime(timezone=True), server_default=func.now())
    updated_at    = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    category  = relationship("Category")
    syllabus  = relationship("Syllabus")
    chapter   = relationship("Chapter")
    creator   = relationship("User")
    questions = relationship("QuizQuestion", back_populates="quiz",
                             cascade="all, delete-orphan")
    attempts  = relationship("QuizAttempt", back_populates="quiz",
                             cascade="all, delete-orphan")


class QuizQuestion(Base):
    """Individual questions for a quiz (parsed from Excel upload)."""
    __tablename__ = "quiz_questions"

    id             = Column(Integer, primary_key=True, index=True)
    quiz_id        = Column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"),
                            nullable=False, index=True)
    question_text  = Column(Text, nullable=False)
    option_a       = Column(Text, nullable=False)
    option_b       = Column(Text, nullable=False)
    option_c       = Column(Text, nullable=True)
    option_d       = Column(Text, nullable=True)
    correct_option = Column(CHAR(1), nullable=False)   # 'A', 'B', 'C', or 'D'
    explanation    = Column(Text, nullable=True)
    marks          = Column(Integer, default=1)
    order_index    = Column(Integer, default=0)
    created_at     = Column(DateTime(timezone=True), server_default=func.now())

    # Relationship
    quiz = relationship("Quiz", back_populates="questions")


class QuizAttempt(Base):
    """Tracks a student's attempt at a quiz."""
    __tablename__ = "quiz_attempts"

    id              = Column(Integer, primary_key=True, index=True)
    quiz_id         = Column(Integer, ForeignKey("quizzes.id"), nullable=False, index=True)
    student_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status          = Column(
        Enum(AttemptStatus, values_callable=lambda x: [e.value for e in x]),
        default=AttemptStatus.IN_PROGRESS.value,
        nullable=False,
    )
    score           = Column(Integer, default=0)
    total_marks     = Column(Integer, default=0)
    percentage      = Column(Float, nullable=True)
    passed          = Column(Boolean, nullable=True)
    started_at      = Column(DateTime(timezone=True), server_default=func.now())
    completed_at    = Column(DateTime(timezone=True), nullable=True)
    time_taken_secs = Column(Integer, nullable=True)

    # Relationships
    quiz    = relationship("Quiz", back_populates="attempts")
    student = relationship("User")
    answers = relationship("QuizAnswer", back_populates="attempt",
                           cascade="all, delete-orphan")


class QuizAnswer(Base):
    """Per-question answer for a quiz attempt."""
    __tablename__ = "quiz_answers"

    id              = Column(Integer, primary_key=True, index=True)
    attempt_id      = Column(Integer, ForeignKey("quiz_attempts.id", ondelete="CASCADE"),
                             nullable=False, index=True)
    question_id     = Column(Integer, ForeignKey("quiz_questions.id", ondelete="CASCADE"), nullable=False)
    selected_option = Column(CHAR(1), nullable=True)  # 'A', 'B', 'C', 'D' or NULL (skipped)
    is_correct      = Column(Boolean, nullable=True)
    marks_awarded   = Column(Integer, default=0)

    # Relationships
    attempt  = relationship("QuizAttempt", back_populates="answers")
    question = relationship("QuizQuestion")


class MockTestChapter(Base):
    """Chapters covered by a mock test (quiz_type='mock', scope='chapters')."""
    __tablename__ = "mock_test_chapters"

    id         = Column(Integer, primary_key=True, index=True)
    quiz_id    = Column(Integer, ForeignKey("quizzes.id", ondelete="CASCADE"),
                        nullable=False, index=True)
    chapter_id = Column(Integer, ForeignKey("chapters.id", ondelete="CASCADE"),
                        nullable=False, index=True)


class ChapterProgress(Base):
    """Per-student chapter progress: content read + chapter-quiz pass state."""
    __tablename__ = "chapter_progress"
    __table_args__ = (
        UniqueConstraint("student_id", "chapter_id", name="uq_chapter_progress"),
    )

    id                   = Column(Integer, primary_key=True, index=True)
    student_id           = Column(Integer, ForeignKey("users.id"),
                                  nullable=False, index=True)
    chapter_id           = Column(Integer, ForeignKey("chapters.id"),
                                  nullable=False, index=True)
    content_read_at      = Column(DateTime(timezone=True), nullable=True)
    quiz_passed          = Column(Boolean, default=False, nullable=False)
    quiz_best_percentage = Column(Float, nullable=True)
    updated_at           = Column(DateTime(timezone=True),
                                  server_default=func.now(), onupdate=func.now())
