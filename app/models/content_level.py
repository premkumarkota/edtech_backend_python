from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from sqlalchemy.sql import func

from app.database import Base


class ContentLevel(Base):
    """
    Admin-managed audience levels for AI content generation (e.g. "Class 6-8",
    "B.Tech", "UPSC Prelims"). Shown in the admin Level dropdown when
    generating chapter content; `ai_guidance` is passed to the AI so it
    calibrates depth/vocabulary for that audience.
    """
    __tablename__ = "content_levels"

    id          = Column(Integer, primary_key=True, index=True)
    name        = Column(String(100), unique=True, nullable=False)
    ai_guidance = Column(Text, nullable=True)
    order_index = Column(Integer, nullable=False, default=0, server_default="0")
    is_active   = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
