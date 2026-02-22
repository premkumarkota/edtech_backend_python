from sqlalchemy import Column, Integer, String, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class StudentProfile(Base):
    """
    Extended student-specific profile info.
    1:1 with users where role = 'student'.
    """
    __tablename__ = "student_profiles"

    id             = Column(Integer, primary_key=True, index=True)
    user_id        = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"),
                            unique=True, nullable=False)
    dob            = Column(String(20), nullable=True)
    age            = Column(Integer, nullable=True)
    school_college = Column(String(200), nullable=True)
    location       = Column(String(200), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Relationship back to user
    user = relationship("User", back_populates="student_profile")
