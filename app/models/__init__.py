# Import order matters for SQLAlchemy FK resolution.
# Always import referenced tables BEFORE tables that reference them.

from app.models.category import Category                              # No dependencies
from app.models.user import User, UserRole                            # Depends on Category
from app.models.platform_config import PlatformConfig                 # Depends on User (updated_by FK)
from app.models.student_profile import StudentProfile                 # Depends on User
from app.models.teacher_profile import TeacherProfile, TeacherStatus  # Depends on User
from app.models.syllabus import Syllabus                       # Depends on Category, User
from app.models.quiz import (                                  # Depends on Category, User
    Quiz, QuizQuestion,
    QuizAttempt, QuizAnswer,
    QuizStatus, AttemptStatus,
)
# ── New: Subscription & Payment System ────────────────────────────
from app.models.subscription import (
    SubscriptionPlan, StudentSubscription, SubscriptionStatus
)
from app.models.payment import RazorpayPayment                 # Depends on User, StudentSubscription
from app.models.payout import (                                # Depends on User
    TeacherRate, TeacherEarning, PayoutBatch, TeacherPayout
)
from app.models.session import VideoCallSession, SessionStatus  # Depends on User, StudentSubscription
from app.models.availability import (                          # Depends on User
    TeacherAvailability, TeacherAvailabilityOverride
)

__all__ = [
    # Core
    "Category",
    "User", "UserRole",
    "PlatformConfig",
    "StudentProfile",
    "TeacherProfile", "TeacherStatus",
    "Syllabus",
    "Quiz", "QuizQuestion",
    "QuizAttempt", "QuizAnswer",
    "QuizStatus", "AttemptStatus",
    # Subscription & Payment
    "SubscriptionPlan", "StudentSubscription", "SubscriptionStatus",
    "RazorpayPayment",
    "TeacherRate", "TeacherEarning", "PayoutBatch", "TeacherPayout",
    "VideoCallSession", "SessionStatus",
    "TeacherAvailability", "TeacherAvailabilityOverride",
]
