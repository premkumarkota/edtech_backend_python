from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
from app.schemas.syllabus import (
    SyllabusSummary,
    SyllabusDetailStudent,
    ChapterStudentResponse,
    SyllabusContentResponse,
)

router = APIRouter()


def _layout_chapter_for_student(ch: Chapter) -> ChapterStudentResponse:
    """First video = hero; remaining non-video items = documents; other videos = extra."""
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

    return ChapterStudentResponse(
        id=ch.id,
        syllabus_id=ch.syllabus_id,
        title=ch.title,
        description=ch.description,
        order_index=ch.order_index or 0,
        created_at=ch.created_at,
        contents=[SyllabusContentResponse.model_validate(x) for x in items],
        hero_video=SyllabusContentResponse.model_validate(hero) if hero else None,
        document_contents=[SyllabusContentResponse.model_validate(x) for x in docs],
        extra_videos=[SyllabusContentResponse.model_validate(x) for x in extra_videos],
    )


def _syllabus_to_student_detail(s: Syllabus) -> SyllabusDetailStudent:
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
        chapters=[_layout_chapter_for_student(ch) for ch in chapters_in_order],
    )

@router.get("/", response_model=List[SyllabusSummary])
def get_my_syllabus(
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """List all subjects available for the student's category."""
    if not student.category_id:
        return []

    results = (
        db.query(Syllabus)
        .filter(Syllabus.category_id == student.category_id, Syllabus.is_active == True)
        .order_by(Syllabus.created_at.desc())
        .all()
    )
    # Add chapter count
    for s in results:
        s.chapter_count = db.query(Chapter).filter(Chapter.syllabus_id == s.id).count()
    return results

@router.get("/{syllabus_id}", response_model=SyllabusDetailStudent)
def get_syllabus_detail(
    syllabus_id: int,
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Full syllabus with hero_video + document_contents for Udemy-style chapter screens."""
    s = (
        db.query(Syllabus)
        .options(
            joinedload(Syllabus.chapters).joinedload(Chapter.contents),
        )
        .filter(Syllabus.id == syllabus_id, Syllabus.is_active == True)
        .first()
    )

    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")

    if s.category_id != student.category_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return _syllabus_to_student_detail(s)
