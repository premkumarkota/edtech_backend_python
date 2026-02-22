from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime

# --- Content ---
class SyllabusContentBase(BaseModel):
    title: str
    description: Optional[str] = None
    content_type: str  # video, pdf, ppt
    file_url: str
    video_duration: Optional[int] = None
    order_index: int = 0

class SyllabusContentCreate(SyllabusContentBase):
    chapter_id: int

class SyllabusContentResponse(SyllabusContentBase):
    id: int
    chapter_id: int
    created_at: datetime
    class Config:
        from_attributes = True

# --- Chapter ---
class ChapterBase(BaseModel):
    title: str
    description: Optional[str] = None
    order_index: int = 0

class ChapterCreate(ChapterBase):
    syllabus_id: int

class ChapterResponse(ChapterBase):
    id: int
    syllabus_id: int
    contents: List[SyllabusContentResponse] = []
    created_at: datetime
    class Config:
        from_attributes = True

# --- Syllabus (Subject/Course) ---
class SyllabusBase(BaseModel):
    category_id: int
    title: str
    description: Optional[str] = None
    thumbnail_url: Optional[str] = None

class SyllabusCreate(SyllabusBase):
    pass

class SyllabusSummary(SyllabusBase):
    id: int
    is_active: bool
    created_at: datetime
    chapter_count: int = 0
    class Config:
        from_attributes = True

class SyllabusDetail(SyllabusSummary):
    chapters: List[ChapterResponse] = []
    class Config:
        from_attributes = True

# Backward compatibility or generic
class SyllabusResponse(SyllabusSummary):
    pass
