from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Enum
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
import enum

class ContentType(enum.Enum):
    VIDEO = "video"
    PDF = "pdf"
    TEXT = "text"
    PPT = "ppt"

class Syllabus(Base):
    """
    Represent a Subject or Course (e.g., Mathematics, Physics) within a Category.
    """
    __tablename__ = "syllabus"

    id          = Column(Integer, primary_key=True, index=True)
    category_id = Column(Integer, ForeignKey("categories.id"), nullable=False, index=True)
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    thumbnail_url = Column(String(500), nullable=True)
    is_active   = Column(Boolean, default=True)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())
    updated_at  = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # Relationships
    category = relationship("Category")
    chapters = relationship("Chapter", back_populates="syllabus", cascade="all, delete-orphan")

class Chapter(Base):
    """
    Represents a Chapter within a Syllabus (e.g., Chapter 1: Sets).
    """
    __tablename__ = "chapters"

    id          = Column(Integer, primary_key=True, index=True)
    syllabus_id = Column(Integer, ForeignKey("syllabus.id"), nullable=False)
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    order_index = Column(Integer, default=0)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    syllabus = relationship("Syllabus", back_populates="chapters")
    contents = relationship("SyllabusContent", back_populates="chapter", cascade="all, delete-orphan")

class SyllabusContent(Base):
    """
    Individual learning materials (Videos, PDFs) within a Chapter.
    """
    __tablename__ = "syllabus_contents"

    id          = Column(Integer, primary_key=True, index=True)
    chapter_id  = Column(Integer, ForeignKey("chapters.id"), nullable=False)
    title       = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    content_type= Column(String(50), nullable=False) # video, pdf, ppt
    file_url    = Column(String(500), nullable=False)
    video_duration= Column(Integer, nullable=True) # in seconds
    order_index = Column(Integer, default=0)
    created_at  = Column(DateTime(timezone=True), server_default=func.now())

    # Relationships
    chapter = relationship("Chapter", back_populates="contents")
