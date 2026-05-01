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
