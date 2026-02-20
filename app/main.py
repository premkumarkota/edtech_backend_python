from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.database import engine, Base
from app.models.user import User  # Import to register model with SQLAlchemy

from app.routers.admin import auth as admin_auth
from app.routers.admin import users as admin_users
from app.routers.student import auth as student_auth
from app.routers.teacher import auth as teacher_auth


# Create all database tables
# We move this to a lifespan event so the app doesn't crash on import if DB is missing
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        Base.metadata.create_all(bind=engine)
        print("INFO: Database tables created successfully.")
    except Exception as e:
        print(f"WARNING: Database connection failed during startup. The app will run, but database features will fail. Error: {e}")
    yield

app = FastAPI(
    title="EdTech LMS API",
    description="Learning Management System — Admin, Student & Teacher APIs",
    version="2.0.0",
    lifespan=lifespan,
)

# CORS — Allow Flutter apps to connect
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== MOUNT ROUTERS ====================

# Admin Portal APIs
app.include_router(
    admin_auth.router,
    prefix="/api/admin/auth",
    tags=["Admin - Authentication"],
)
app.include_router(
    admin_users.router,
    prefix="/api/admin/users",
    tags=["Admin - User Management"],
)

from app.routers.admin import categories as admin_categories
app.include_router(
    admin_categories.router,
    prefix="/api/admin/categories",
    tags=["Admin - Categories"],
)

from app.routers.admin import teachers as admin_teachers
app.include_router(
    admin_teachers.router,
    prefix="/api/admin/teachers",
    tags=["Admin - Teacher Actions"],
)

# Temporary DB Fix
from app.routers.admin import db_fix
app.include_router(
    db_fix.router,
    prefix="/api/admin/db-fix",
    tags=["Admin - Setup"],
)

# Common APIs
from app.routers.common import categories as common_categories
app.include_router(
    common_categories.router,
    prefix="/api/common/categories",
    tags=["Common - Categories"],
)

# Student App APIs
app.include_router(
    student_auth.router,
    prefix="/api/student/auth",
    tags=["Student - Authentication"],
)

from app.routers.student import home as student_home
app.include_router(
    student_home.router,
    prefix="/api/student/home",
    tags=["Student - Home (Teachers)"],
)

# Teacher App APIs
app.include_router(
    teacher_auth.router,
    prefix="/api/teacher/auth",
    tags=["Teacher - Authentication"],
)

# Profile APIs (Token Required)
from app.routers.student import profile as student_profile
from app.routers.teacher import profile as teacher_profile

app.include_router(
    student_profile.router,
    prefix="/api/student/profile",
    tags=["Student - Profile"],
)

app.include_router(
    teacher_profile.router,
    prefix="/api/teacher/profile",
    tags=["Teacher - Profile"],
)


# ==================== HEALTH CHECK ====================

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
