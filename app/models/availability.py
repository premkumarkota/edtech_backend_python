"""
Teacher Availability Models

TeacherAvailability          — weekly recurring schedule (Mon 9am–12pm, etc.)
TeacherAvailabilityOverride  — exceptions (block a specific date / day off)
TeacherDateSlot              — one-time slots for a specific date
"""
from sqlalchemy import (
    Column, Integer, Boolean, ForeignKey, Time, Date, UniqueConstraint
)
from sqlalchemy.sql import func
from sqlalchemy import DateTime
from app.database import Base





class TeacherAvailability(Base):
    """
    Weekly recurring time blocks.
    day_of_week: 0=Monday … 6=Sunday
    Each block generates (end_time - start_time) / slot_duration_mins bookable slots.
    """
    __tablename__ = "teacher_availability"

    id                  = Column(Integer, primary_key=True, index=True)
    teacher_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    day_of_week         = Column(Integer, nullable=False)        # 0=Mon … 6=Sun
    start_time          = Column(Time, nullable=False)           # e.g. 09:00
    end_time            = Column(Time, nullable=False)           # e.g. 12:00
    slot_duration_mins  = Column(Integer, default=60)            # each bookable slot length
    is_active           = Column(Boolean, default=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("teacher_id", "day_of_week", "start_time",
                         name="uq_teacher_day_start"),
    )


class TeacherAvailabilityOverride(Base):
    """
    Date-level exceptions.
    is_blocked=True  → teacher is NOT available on this date (holiday/day off)
    """
    __tablename__ = "teacher_availability_overrides"

    id          = Column(Integer, primary_key=True, index=True)
    teacher_id  = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date        = Column(Date, nullable=False)
    is_blocked  = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("teacher_id", "date", name="uq_teacher_override_date"),
    )


class TeacherDateSlot(Base):
    """
    One-time availability slot for a specific calendar date.
    Unlike TeacherAvailability (recurring weekly), these apply once only.
    """
    __tablename__ = "teacher_date_slots"

    id                  = Column(Integer, primary_key=True, index=True)
    teacher_id          = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date                = Column(Date, nullable=False)
    start_time          = Column(Time, nullable=False)
    end_time            = Column(Time, nullable=False)
    slot_duration_mins  = Column(Integer, default=60)
    is_active           = Column(Boolean, default=True)
    created_at          = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("teacher_id", "date", "start_time",
                         name="uq_teacher_date_slot_start"),
    )
