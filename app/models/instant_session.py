import enum

from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class InstantSessionRequestStatus(str, enum.Enum):
    REQUESTED = "requested"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class InstantSessionRequest(Base):
    """
    Pending instant-session workflow.

    A request is not a billable video session. When accepted, it creates a
    normal VideoCallSession row and stores that id in session_id.
    """
    __tablename__ = "instant_session_requests"

    id = Column(Integer, primary_key=True, index=True)
    student_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    teacher_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    subscription_id = Column(Integer, ForeignKey("student_subscriptions.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("video_call_sessions.id"), nullable=True, index=True)

    status = Column(
        Enum(InstantSessionRequestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=InstantSessionRequestStatus.REQUESTED.value,
        index=True,
    )
    duration_mins = Column(Integer, nullable=False)
    subject_context = Column(Text, nullable=True)
    decline_reason = Column(Text, nullable=True)
    cancel_reason = Column(Text, nullable=True)

    requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    responded_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    student = relationship("User", foreign_keys=[student_id])
    teacher = relationship("User", foreign_keys=[teacher_id])
    session = relationship("VideoCallSession", foreign_keys=[session_id])
    subscription = relationship("StudentSubscription", foreign_keys=[subscription_id])

    @property
    def student_name(self) -> str | None:
        return self.student.name if self.student else None

    @property
    def teacher_name(self) -> str | None:
        return self.teacher.name if self.teacher else None
