import enum
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Enum, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class UserRole(str, enum.Enum):
    """User roles in the system"""
    ADMIN   = "admin"
    STUDENT = "student"
    TEACHER = "teacher"


class User(Base):
    """
    Unified User model.
    - Admin: logs in via email + password (from admin portal)
    - Student: logs in via Firebase OTP (from student app)
    - Teacher: logs in via Firebase OTP (from teacher app)
    """
    __tablename__ = "users"

    id                   = Column(Integer, primary_key=True, index=True)
    name                 = Column(String(100), nullable=True)
    email                = Column(String(255), unique=True, nullable=True, index=True)
    phone_number         = Column(String(20), unique=True, nullable=True, index=True)
    hashed_password      = Column(String(255), nullable=True)   # Only for admin
    role                 = Column(
        Enum(UserRole, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    is_active            = Column(Boolean, default=True)

    # Firebase (for student & teacher apps)
    firebase_uid         = Column(String(128), unique=True, nullable=True, index=True)

    # Onboarding
    onboarding_completed = Column(Boolean, default=False)

    # Profile
    profile_image_url    = Column(String(500), nullable=True)

    # Legacy flat fields (kept for backward compat — profile tables are preferred)
    dob            = Column(String(50), nullable=True)
    age            = Column(Integer, nullable=True)
    school_college = Column(String(200), nullable=True)
    location       = Column(String(200), nullable=True)
    document_url   = Column(String(500), nullable=True)
    is_verified    = Column(Boolean, default=False)

    # FK to primary category
    category_id    = Column(Integer, ForeignKey("categories.id"), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # ── Relationships ──────────────────────────────────────────
    student_profile = relationship(
        "StudentProfile", back_populates="user",
        uselist=False, cascade="all, delete-orphan"
    )
    teacher_profile = relationship(
        "TeacherProfile", foreign_keys="TeacherProfile.user_id",
        back_populates="user",
        uselist=False, cascade="all, delete-orphan"
    )

    def __repr__(self):
        return f"<User(id={self.id}, name={self.name}, role={self.role})>"
