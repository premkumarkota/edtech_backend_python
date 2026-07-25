"""
Notification model — persists every push notification sent to users.
"""
from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey,
)
from sqlalchemy.orm import relationship
from datetime import datetime, timezone

from app.database import Base


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    body = Column(Text, nullable=False)
    notif_type = Column(String(50), nullable=False, default="general")
    # Category for filtering: sessions, learning, study_planner, offers, system
    category = Column(String(30), nullable=False, default="system")
    data = Column(Text, nullable=True)  # JSON string of extra payload
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    user = relationship("User", backref="notifications")
