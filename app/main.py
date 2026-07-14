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
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Learning git",
    description="Learning Management System — Admin, Student & Teacher APIs",
    version="2.0.0",
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

# Student App APIs
app.include_router(
    student_auth.router,
    prefix="/api/student/auth",
    tags=["Student - Authentication"],
)

# Teacher App APIs
app.include_router(
    teacher_auth.router,
    prefix="/api/teacher/auth",
    tags=["Teacher - Authentication"],
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
