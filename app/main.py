import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import logging

logger = logging.getLogger(__name__)

from app.public_errors import public_server_error

from app.config import settings
from app.database import engine, Base

# ── Import ALL models in FK-dependency order so Base.metadata has them all ──
from app.models import (  # noqa: F401
    Category, User, UserRole,
    PlatformConfig,
    StudentProfile, TeacherProfile, TeacherStatus,
    Syllabus,
    Quiz, QuizQuestion, QuizAttempt, QuizAnswer,
    TeacherAvailability, TeacherAvailabilityOverride,
    InstantSessionRequest, InstantSessionRequestStatus,
    TeacherBankDetails, WithdrawalRequest,
)

# ── Routers ──────────────────────────────────────────────────────────────────
from app.routers.admin import auth as admin_auth
from app.routers.admin import users as admin_users
from app.routers.admin import categories as admin_categories
from app.routers.admin import exam_categories as admin_exam_categories
from app.routers.admin import teachers as admin_teachers
from app.routers.admin import syllabus as admin_syllabus
from app.routers.admin import quiz as admin_quiz
from app.routers.admin import mock_tests as admin_mock_tests
from app.routers.admin import study_planner as admin_study_planner
from app.routers.admin import plans as admin_plans
from app.routers.admin import subscriptions as admin_subscriptions
from app.routers.admin import payouts as admin_payouts
from app.routers.admin import config as admin_config
from app.routers.admin import content_levels as admin_content_levels
from app.routers.admin import stats as admin_stats
from app.routers.admin import sessions as admin_sessions
from app.routers.admin import withdrawals as admin_withdrawals

from app.routers.common import categories as common_categories
from app.routers.common import legal as common_legal

from app.routers.student import auth as student_auth
from app.routers.student import home as student_home
from app.routers.student import syllabus as student_syllabus
from app.routers.student import quiz as student_quiz
from app.routers.student import subscription as student_subscription
from app.routers.student import sessions as student_sessions
from app.dependencies.auth import security

from app.routers.student import profile as student_profile
from app.routers.teacher import profile as teacher_profile
from app.routers.teacher import availability as teacher_availability

from app.routers.teacher import auth as teacher_auth
from app.routers.teacher import home as teacher_home
from app.routers.teacher import sessions as teacher_sessions
from app.routers.teacher import withdrawals as teacher_withdrawals
from app.routers.payments import router as payments_webhook
from app.routers.student import ai_planner as student_ai_planner
from app.routers.student import study_planner as student_study_planner
from app.routers.student import notifications as student_notifications
from app.routers.admin import exams as admin_exams
from app.routers.admin import streaks as admin_streaks
from app.routers.student import streak as student_streak
from app.services.reminder_scheduler import start_scheduler, stop_scheduler
from app.services.ai_plan_scheduler import start_ai_plan_scheduler, stop_ai_plan_scheduler
from app.services.study_reschedule_service import start_study_planner_scheduler, stop_study_planner_scheduler

# When True (default), V2 study planner owns session/streak reminders; V1 AI plan
# daily push is disabled to avoid duplicate notifications.
_V2_CANONICAL = os.environ.get("STUDY_PLANNER_V2_CANONICAL", "1").strip().lower() in (
    "1", "true", "yes",
)


def _ensure_chapter_live_copy():
    """
    Idempotent schema patch for the chapter draft/live split (create_all
    doesn't add columns to existing tables). Backfills the live copy for
    chapters that were already published, so a later draft edit can't leak.
    Mirrors alembic revision j4k5l6m7n8o9.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            conn.execute(_sql(
                "ALTER TABLE chapters ADD COLUMN IF NOT EXISTS published_description TEXT"
            ))
            conn.execute(_sql(
                "UPDATE chapters SET published_description = description "
                "WHERE content_published = true AND published_description IS NULL"
            ))
    except Exception as e:  # never block startup on this
        print(f"WARNING: chapter live-copy patch failed: {e}")


def _ensure_student_consent_columns():
    """
    Idempotent patch for onboarding consent timestamps on student_profiles.
    Mirrors alembic revision l6m7n8o9p0q1.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            for col, typ in (("terms_accepted_at", "TIMESTAMPTZ"),
                             ("guardian_consent_at", "TIMESTAMPTZ"),
                             ("consent_version", "VARCHAR(20)")):
                conn.execute(_sql(
                    f"ALTER TABLE student_profiles ADD COLUMN IF NOT EXISTS {col} {typ}"
                ))
    except Exception as e:  # never block startup on this
        print(f"WARNING: student consent patch failed: {e}")


def _ensure_streak_columns():
    """
    Idempotent patch for learning streaks: new study_streaks columns
    (create_all already makes student_learning_days). Mirrors alembic n8o9p0q1r2s3.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            for col, typ in (("freezes", "INTEGER NOT NULL DEFAULT 0"),
                             ("broken_streak", "INTEGER NOT NULL DEFAULT 0"),
                             ("broken_on", "DATE"),
                             ("repair_used_on", "DATE"),
                             ("last_reminder_on", "DATE")):
                conn.execute(_sql(
                    f"ALTER TABLE study_streaks ADD COLUMN IF NOT EXISTS {col} {typ}"
                ))
    except Exception as e:  # never block startup on this
        print(f"WARNING: streak patch failed: {e}")


def _ensure_teacher_terms_columns():
    """Teacher Terms acceptance on teacher_profiles. Mirrors alembic o9p0q1r2s3t4."""
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            for col, typ in (("teacher_terms_accepted_at", "TIMESTAMPTZ"),
                             ("legal_version", "VARCHAR(20)")):
                conn.execute(_sql(
                    f"ALTER TABLE teacher_profiles ADD COLUMN IF NOT EXISTS {col} {typ}"
                ))
    except Exception as e:  # never block startup on this
        print(f"WARNING: teacher terms patch failed: {e}")


def _ensure_quiz_retake_columns():
    """
    Idempotent patch for fresh retake questions: quiz_questions.attempt_id
    (owner attempt of an AI retake question) + quiz_attempts.question_ids.
    Mirrors alembic revision m7n8o9p0q1r2.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            conn.execute(_sql(
                "ALTER TABLE quiz_questions ADD COLUMN IF NOT EXISTS attempt_id INTEGER "
                "REFERENCES quiz_attempts(id) ON DELETE CASCADE"
            ))
            conn.execute(_sql(
                "CREATE INDEX IF NOT EXISTS ix_quiz_questions_attempt_id "
                "ON quiz_questions (attempt_id)"
            ))
            conn.execute(_sql(
                "ALTER TABLE quiz_attempts ADD COLUMN IF NOT EXISTS question_ids JSON"
            ))
    except Exception as e:  # never block startup on this
        print(f"WARNING: quiz retake patch failed: {e}")


_DEFAULT_EXAM_CATEGORIES = ("Entrance Exams", "Competitive Exams", "Academic Exams")


def _ensure_exam_categories():
    """
    Idempotent schema patch for board categories: adds
    categories.exam_category_id (create_all made exam_categories already) and
    seeds the three default categories the first time. Mirrors alembic
    revision k5l6m7n8o9p0.
    """
    if engine.dialect.name != "postgresql":
        return
    from sqlalchemy import text as _sql
    try:
        with engine.begin() as conn:
            conn.execute(_sql(
                "ALTER TABLE categories ADD COLUMN IF NOT EXISTS exam_category_id INTEGER "
                "REFERENCES exam_categories(id) ON DELETE SET NULL"
            ))
            conn.execute(_sql(
                "CREATE INDEX IF NOT EXISTS ix_categories_exam_category_id "
                "ON categories (exam_category_id)"
            ))
            empty = conn.execute(_sql("SELECT COUNT(*) FROM exam_categories")).scalar() == 0
            if empty:
                for i, name in enumerate(_DEFAULT_EXAM_CATEGORIES, start=1):
                    conn.execute(_sql(
                        "INSERT INTO exam_categories (name, order_index, is_active) "
                        "VALUES (:n, :o, true) ON CONFLICT (name) DO NOTHING"
                    ), {"n": name, "o": i})
    except Exception as e:  # never block startup on this
        print(f"WARNING: exam-categories patch failed: {e}")


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        print("INFO: Database tables created/verified successfully.")
        _ensure_chapter_live_copy()
        _ensure_exam_categories()
        _ensure_student_consent_columns()
        _ensure_quiz_retake_columns()
        _ensure_streak_columns()
        _ensure_teacher_terms_columns()
    except Exception as e:
        print(f"WARNING: DB error on startup: {e}")

    start_scheduler()
    if _V2_CANONICAL:
        print("INFO: V1 AI study plan reminders disabled (STUDY_PLANNER_V2_CANONICAL=1).")
    else:
        start_ai_plan_scheduler()
    start_study_planner_scheduler()
    yield
    stop_study_planner_scheduler()
    if not _V2_CANONICAL:
        stop_ai_plan_scheduler()
    stop_scheduler()


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="EdTech LMS API",
    description="Learning Management System — Admin, Student & Teacher APIs",
    version="2.0.0",
    lifespan=lifespan,
    swagger_ui_parameters={"persistAuthorization": True}
)

# ── CORS ──────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "http://localhost:51552",
        "http://127.0.0.1:51552",
        "http://localhost:5000",
        "http://localhost:3000",
        "https://edtech-backend-1088198692751.asia-south1.run.app",
        "https://admin.mymentorservices.com",
        "http://admin.mymentorservices.com",
    ],
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1)(:\d+)?",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Serve local uploads (dev only) ────────────────────────────────────────────
UPLOADS_DIR = os.path.join(os.path.dirname(__file__), "..", "uploads")
if not os.path.exists(UPLOADS_DIR):
    os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/files", StaticFiles(directory=UPLOADS_DIR), name="uploads")


@app.exception_handler(IntegrityError)
async def integrity_error_handler(request: Request, exc: IntegrityError):
    """
    A database constraint blocked the change (e.g. deleting a row other rows
    still reference). Answer 409 with a readable message instead of a 500.
    Registered for this specific type, so it runs inside CORSMiddleware and
    the browser sees the real response (the catch-all Exception handler runs
    outside it, which the admin panel reports as "API unreachable").
    """
    logger.warning(f"IntegrityError on {request.method} {request.url.path}: {exc.orig}")
    return JSONResponse(
        status_code=409,
        content={"detail": "This change conflicts with other records that depend on it. "
                           "Remove or move those first, then try again."},
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Never leak tracebacks or SDK dumps to clients."""
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
        raise exc
    http = public_server_error(exc, action="complete this request")
    return JSONResponse(status_code=http.status_code, content={"detail": http.detail})

# ═══════════════════════════════════════════════════════════════
# ADMIN PORTAL APIs
# ═══════════════════════════════════════════════════════════════

app.include_router(admin_auth.router,
                   prefix="/api/admin/auth",
                   tags=["Admin - Authentication Main"])

app.include_router(admin_users.router,
                   prefix="/api/admin/users",
                   tags=["Admin - User Management"])

app.include_router(admin_categories.router,
                   prefix="/api/admin/categories",
                   tags=["Admin - Boards"])

app.include_router(admin_exam_categories.router,
                   prefix="/api/admin/exam-categories",
                   tags=["Admin - Board Categories"])

app.include_router(admin_teachers.router,
                   prefix="/api/admin/teachers",
                   tags=["Admin - Teacher Verification"])

app.include_router(admin_syllabus.router,
                   prefix="/api/admin/syllabus",
                   tags=["Admin - Syllabus"])

app.include_router(admin_mock_tests.router,
                   prefix="/api/admin/mock-tests",
                   tags=["Admin - Mock Tests"])

app.include_router(admin_quiz.router,
                   prefix="/api/admin/quiz",
                   tags=["Admin - Quiz Management"])

app.include_router(admin_study_planner.router,
                   prefix="/api/admin/study-planner",
                   tags=["Admin - Study Planner"])

app.include_router(admin_plans.router,
                   prefix="/api/admin/plans",
                   tags=["admin-plans"])
app.include_router(admin_subscriptions.router,
                   prefix="/api/admin/subscriptions",
                   tags=["Admin - Subscription Plans"])

app.include_router(admin_payouts.router,
                   prefix="/api/admin",
                   tags=["Admin - Payouts & Rates"])

app.include_router(admin_config.router,
                   prefix="/api/admin/config",
                   tags=["Admin - Platform Config"])
app.include_router(admin_content_levels.router,
                   prefix="/api/admin/content-levels",
                   tags=["Admin - Content Levels"])

app.include_router(admin_stats.router,
                   prefix="/api/admin/stats",
                   tags=["Admin - Dashboard Stats"])

app.include_router(admin_sessions.router,
                   prefix="/api/admin/sessions",
                   tags=["Admin - Session Monitoring"])

app.include_router(admin_withdrawals.router,
                   prefix="/api/admin/withdrawals",
                   tags=["Admin - Teacher Withdrawals"])

app.include_router(admin_exams.router,
                   prefix="/api/admin/exams",
                   tags=["Admin - Exam Management"])

app.include_router(admin_streaks.router,
                   prefix="/api/admin/streaks",
                   tags=["Admin - Learning Streaks"])


# ═══════════════════════════════════════════════════════════════
# COMMON APIs
# ═══════════════════════════════════════════════════════════════

app.include_router(common_categories.router,
                   prefix="/api/common/categories",
                   tags=["Common - Categories"])

app.include_router(common_legal.router,
                   tags=["Public - Legal"])

# ═══════════════════════════════════════════════════════════════
# STUDENT APP APIs
# ═══════════════════════════════════════════════════════════════

app.include_router(student_auth.router,
                   prefix="/api/student/auth",
                   tags=["Student - Authentication"])

app.include_router(student_home.router,
                   prefix="/api/student/home",
                   tags=["Student - Home Feed"])

app.include_router(student_syllabus.router,
                   prefix="/api/student/syllabus",
                   tags=["Student - Syllabus"])

app.include_router(student_quiz.router,
                   prefix="/api/student/quiz",
                   tags=["Student - Quiz"])

app.include_router(student_subscription.router,
                   prefix="/api/student/subscription",
                   tags=["Student - Subscription & Payment"])

app.include_router(student_sessions.router,
                   prefix="/api/student/sessions",
                   tags=["Student - Video Call Sessions"])

app.include_router(student_ai_planner.router,
                   prefix="/api/student/ai",
                   tags=["Student - AI Study Planner"])

app.include_router(student_study_planner.router,
                   prefix="/api/student/ai/v2",
                   tags=["Student - Study Planner V2"])

app.include_router(student_profile.router,
                   prefix="/api/student/profile",
                   tags=["Student - Profile"])

app.include_router(student_notifications.router,
                   prefix="/api/student/notifications",
                   tags=["Student - Notifications"])

app.include_router(student_streak.router,
                   prefix="/api/student/streak",
                   tags=["Student - Learning Streaks"])

# ═══════════════════════════════════════════════════════════════
# TEACHER APP APIs
# ═══════════════════════════════════════════════════════════════

app.include_router(teacher_auth.router,
                   prefix="/api/teacher/auth",
                   tags=["Teacher - Authentication"])

app.include_router(teacher_home.router,
                   prefix="/api/teacher/home",
                   tags=["Teacher - Home"])

app.include_router(teacher_sessions.router,
                   prefix="/api/teacher",
                   tags=["Teacher - Sessions & Earnings"])

app.include_router(teacher_profile.router,
                   prefix="/api/teacher/profile",
                   tags=["Teacher - Profile"])

app.include_router(teacher_withdrawals.router,
                   prefix="/api/teacher/withdrawals",
                   tags=["Teacher - Withdrawals"])

app.include_router(teacher_availability.router,
                   prefix="/api/teacher/availability",
                   tags=["Teacher - Availability"])

# ═══════════════════════════════════════════════════════════════
# PAYMENT WEBHOOK (no JWT — HMAC signature verified)
# ═══════════════════════════════════════════════════════════════

app.include_router(payments_webhook,
                   prefix="/api/payments/webhook",
                   tags=["Payments - Webhook"])


# ═══════════════════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════════════════

@app.get("/", tags=["Health"])
def home():
    return {"message": "EdTech LMS API is running!", "version": "2.0.0"}


@app.get("/test-db", tags=["Health"])
def test_database():
    """Test database connection"""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            return {"status": "success", "message": "Database connected!"}
    except Exception as e:
        return {"status": "error", "message": str(e)}
