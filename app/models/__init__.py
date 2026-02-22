# Import order matters for SQLAlchemy FK resolution.
# Always import referenced tables BEFORE tables that reference them.

from app.models.category import Category            # No dependencies
from app.models.user import User, UserRole          # Depends on Category
from app.models.student_profile import StudentProfile  # Depends on User
from app.models.teacher_profile import TeacherProfile, TeacherStatus  # Depends on User
from app.models.syllabus import Syllabus            # Depends on Category, User
from app.models.quiz import (                       # Depends on Category, User
    Quiz, QuizQuestion,
    QuizAttempt, QuizAnswer,
    QuizStatus, AttemptStatus,
)

__all__ = [
    "Category",
    "User", "UserRole",
    "StudentProfile",
    "TeacherProfile", "TeacherStatus",
    "Syllabus",
    "Quiz", "QuizQuestion",
    "QuizAttempt", "QuizAnswer",
    "QuizStatus", "AttemptStatus",
]
