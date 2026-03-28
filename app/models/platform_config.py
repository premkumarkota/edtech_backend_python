from sqlalchemy import Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


class PlatformConfig(Base):
    """
    Key-value store for platform-wide admin settings.

    Current keys:
      max_teacher_rate_per_minute — ceiling on teacher proposed/approved rates (INR/min)

    Adding new platform settings: just INSERT a new row with a new key.
    No schema change needed.
    """
    __tablename__ = "platform_config"

    id          = Column(Integer, primary_key=True, index=True)
    key         = Column(String(100), unique=True, nullable=False)
    value       = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    updated_by  = Column(Integer, ForeignKey("users.id"), nullable=True)
    updated_at  = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    admin = relationship("User", foreign_keys=[updated_by])
