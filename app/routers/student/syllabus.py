from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter
from app.schemas.syllabus import SyllabusSummary, SyllabusDetailStudent
from app.services.syllabus_layout import syllabus_to_detail_with_layout

router = APIRouter()

@router.get("/", response_model=List[SyllabusSummary])
def get_my_syllabus(
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
    category_id: Optional[int] = Query(None, description="Browse a different category"),
):
    """List all subjects for a category. Falls back to student's own category."""
    effective_category_id = category_id or student.category_id
    if not effective_category_id:
        return []

    results = (
        db.query(Syllabus)
        .filter(Syllabus.category_id == effective_category_id, Syllabus.is_active == True)
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

    return syllabus_to_detail_with_layout(s)
