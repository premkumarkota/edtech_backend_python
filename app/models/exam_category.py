from sqlalchemy import Column, Integer, String, Boolean, DateTime
from sqlalchemy.sql import func
from app.database import Base


class ExamCategory(Base):
    """
    Groups boards for students, e.g. 'Entrance Exams', 'Competitive Exams',
    'Academic Exams'. Created by Admin. A board (`categories` row) points here
    through `categories.exam_category_id`; boards without one are uncategorized.
    """
    __tablename__ = "exam_categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    order_index = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
