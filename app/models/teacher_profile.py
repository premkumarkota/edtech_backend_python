import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class TeacherStatus(str, enum.Enum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class TeacherProfile(Base):
    """
    Extended teacher-specific profile info.
    1:1 with users where role = 'teacher'.
    Tracks verification status (pending/approved/rejected).
    """
    __tablename__ = "teacher_profiles"

    id               = Column(Integer, primary_key=True, index=True)
    user_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                              unique=True, nullable=False)
    institution_name = Column(String(200), nullable=True)
    location         = Column(String(200), nullable=True)
    bio              = Column(Text, nullable=True)
    experience_years = Column(Integer, nullable=True)
    document_url     = Column(String(500), nullable=True)  # Verification PDF/image

    status = Column(
        Enum(TeacherStatus, values_callable=lambda x: [e.value for e in x]),
        default=TeacherStatus.PENDING.value,
        nullable=False,
    )

    reviewed_by      = Column(Integer, ForeignKey("users.id"), nullable=True)
    reviewed_at      = Column(DateTime(timezone=True), nullable=True)
    rejection_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationships
    user     = relationship("User", foreign_keys=[user_id], back_populates="teacher_profile")
    reviewer = relationship("User", foreign_keys=[reviewed_by])
