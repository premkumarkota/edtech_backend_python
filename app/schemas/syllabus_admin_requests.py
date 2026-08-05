"""Request bodies for admin syllabus chapter content management."""

from pydantic import BaseModel, Field
from typing import List, Optional


class ReorderChapterContentsRequest(BaseModel):
    """Exactly the set of content IDs belonging to this chapter, in playback order."""

    ordered_content_ids: List[int] = Field(default_factory=list)


class SyllabusContentPatch(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    order_index: Optional[int] = None


class ChapterPatch(BaseModel):
    """Update a chapter's title and/or content (description)."""
    title: Optional[str] = None
    description: Optional[str] = None


class AIChapterContentRequest(BaseModel):
    """Generate rich chapter content (Markdown) with AI."""
    topic: str
    level: str = "General"   # e.g. "Class 6-8", "Class 11-12", "B.Tech"
