
from sqlalchemy import Column, Integer, String, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class Category(Base):
    """
    Boards like 'NEET(UG)', 'CBSE 10th', 'B.Tech', 'SSC' (shown as "Boards"
    in the admin panel and apps; the table keeps its original name).
    Created by Admin. Optionally grouped under an ExamCategory.
    """
    __tablename__ = "categories"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    image_url = Column(String(500), nullable=True)
    is_active = Column(Boolean, default=True)
    exam_category_id = Column(
        Integer, ForeignKey("exam_categories.id", ondelete="SET NULL"),
        nullable=True, index=True,
    )
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    exam_category = relationship("ExamCategory", lazy="joined")

    @property
    def exam_category_name(self):
        return self.exam_category.name if self.exam_category else None
