"""
Shared Udemy-style chapter layout used by student and admin syllabus detail APIs.

Rule: ordered by order_index → first VIDEO is hero lecture; remaining non-video
items are downloadable materials; subsequent videos are supplemental.
"""

from typing import List, Optional

from app.models.syllabus import Chapter, Syllabus, SyllabusContent
from app.schemas.syllabus import (
    ChapterStudentResponse,
    SyllabusContentResponse,
    SyllabusDetailStudent,
)


def chapter_to_layout_response(
    ch: Chapter, include_unpublished: bool = False
) -> ChapterStudentResponse:
    items = sorted(
        ch.contents,
        key=lambda c: (c.order_index or 0, c.id or 0),
    )
    videos: List[SyllabusContent] = [
        x for x in items if (x.content_type or "").lower() == "video"
    ]
    docs: List[SyllabusContent] = [
        x for x in items if (x.content_type or "").lower() != "video"
    ]
    hero: Optional[SyllabusContent] = videos[0] if videos else None
    extra_videos = videos[1:] if len(videos) > 1 else []

    published = getattr(ch, "content_published", True)
    # Students only receive the content when it's published. Admin views
    # (include_unpublished=True) always see it so they can review drafts.
    description = ch.description if (include_unpublished or published) else None

    return ChapterStudentResponse(
        id=ch.id,
        syllabus_id=ch.syllabus_id,
        title=ch.title,
        description=description,
        content_published=published,
        order_index=ch.order_index or 0,
        created_at=ch.created_at,
        contents=[SyllabusContentResponse.model_validate(x) for x in items],
        hero_video=SyllabusContentResponse.model_validate(hero)
        if hero
        else None,
        document_contents=[SyllabusContentResponse.model_validate(x) for x in docs],
        extra_videos=[SyllabusContentResponse.model_validate(x) for x in extra_videos],
    )


def syllabus_to_detail_with_layout(
    s: Syllabus, include_unpublished: bool = False
) -> SyllabusDetailStudent:
    chapters_in_order = sorted(
        s.chapters,
        key=lambda ch: (ch.order_index or 0, ch.id or 0),
    )
    return SyllabusDetailStudent(
        id=s.id,
        category_id=s.category_id,
        title=s.title,
        description=s.description,
        thumbnail_url=s.thumbnail_url,
        is_active=s.is_active,
        created_at=s.created_at,
        chapter_count=len(s.chapters),
        chapters=[
            chapter_to_layout_response(ch, include_unpublished=include_unpublished)
            for ch in chapters_in_order
        ],
    )
