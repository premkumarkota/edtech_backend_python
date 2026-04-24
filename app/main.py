import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

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
)

# ── Routers ──────────────────────────────────────────────────────────────────
from app.routers.admin import auth as admin_auth
from app.routers.admin import users as admin_users
from app.routers.admin import categories as admin_categories
from app.routers.admin import teachers as admin_teachers
from app.routers.admin import syllabus as admin_syllabus
from app.routers.admin import quiz as admin_quiz
from app.routers.admin import plans as admin_plans
from app.routers.admin import subscriptions as admin_subscriptions
from app.routers.admin import payouts as admin_payouts
from app.routers.admin import config as admin_config
from app.routers.admin import stats as admin_stats

from app.routers.common import categories as common_categories

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
from app.routers.payments import router as payments_webhook
from app.services.reminder_scheduler import start_scheduler, stop_scheduler


# ── Lifespan (startup / shutdown) ────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        print("INFO: Database tables created/verified successfully.")
    except Exception as e:
        print(f"WARNING: DB error on startup: {e}")

    start_scheduler()
    yield
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
                   tags=["Admin - Categories"])

app.include_router(admin_teachers.router,
                   prefix="/api/admin/teachers",
                   tags=["Admin - Teacher Verification"])

app.include_router(admin_syllabus.router,
                   prefix="/api/admin/syllabus",
                   tags=["Admin - Syllabus"])

app.include_router(admin_quiz.router,
                   prefix="/api/admin/quiz",
                   tags=["Admin - Quiz Management"])

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

app.include_router(admin_stats.router,
                   prefix="/api/admin/stats",
                   tags=["Admin - Dashboard Stats"])


# ═══════════════════════════════════════════════════════════════
# COMMON APIs
# ═══════════════════════════════════════════════════════════════

app.include_router(common_categories.router,
                   prefix="/api/common/categories",
                   tags=["Common - Categories"])

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

app.include_router(student_profile.router,
                   prefix="/api/student/profile",
                   tags=["Student - Profile"])

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
