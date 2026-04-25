import enum
from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class SessionStatus(str, enum.Enum):
    BOOKED      = "booked"
    IN_PROGRESS = "in_progress"
    COMPLETED   = "completed"
    CANCELLED   = "cancelled"
    NO_SHOW     = "no_show"


class VideoCallSession(Base):
    """
    Tracks the full lifecycle of a video call session between
    a student and a teacher.

    Key business rules:
    - Created when student books (status=booked)
    - Minutes deducted from subscription ONLY when status=completed
    - Teacher earning created at session end
    """
    __tablename__ = "video_call_sessions"

    id              = Column(Integer, primary_key=True, index=True)
    student_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    teacher_id      = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("student_subscriptions.id"), nullable=False, index=True)

    status = Column(
        Enum(SessionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=SessionStatus.BOOKED.value,
    )

    # Agora channel name — unique per session
    agora_channel_name = Column(String(200), unique=True, nullable=True)

    # Timing
    scheduled_at           = Column(DateTime(timezone=True), nullable=False)
    started_at             = Column(DateTime(timezone=True), nullable=True)
    ended_at               = Column(DateTime(timezone=True), nullable=True)

    # Duration (actual is source of billing truth)
    scheduled_duration_mins = Column(Integer, nullable=False, default=30)
    actual_duration_mins    = Column(Integer, nullable=True)   # Set when session ends

    # Reminder flag — prevents duplicate 15-min notifications
    reminder_sent = Column(Boolean, default=False, nullable=False, server_default="false")

    # Cancellation info
    cancelled_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    cancel_reason = Column(Text, nullable=True)
    cancelled_at  = Column(DateTime(timezone=True), nullable=True)
    is_late_cancel = Column(Boolean, default=False, nullable=False, server_default="false")
    penalty_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    refund_minutes = Column(Integer, nullable=False, default=0, server_default="0")
    no_show_marked_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    no_show_reason = Column(Text, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    student      = relationship("User", foreign_keys=[student_id])
    teacher      = relationship("User", foreign_keys=[teacher_id])
    subscription = relationship("StudentSubscription", back_populates="sessions")
    earning      = relationship("TeacherEarning", back_populates="session", uselist=False)

    @property
    def duration_mins(self) -> int:
        return self.scheduled_duration_mins

    @property
    def student_name(self) -> str | None:
        return self.student.name if self.student else None

    @property
    def teacher_name(self) -> str | None:
        return self.teacher.name if self.teacher else None

    def __repr__(self):
        return f"<VideoCallSession(id={self.id}, student={self.student_id}, teacher={self.teacher_id}, status={self.status})>"
