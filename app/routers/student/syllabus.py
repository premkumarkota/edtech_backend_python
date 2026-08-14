from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter
from app.models.quiz import Quiz, QuizStatus, ChapterProgress
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


@router.post("/chapters/{chapter_id}/mark-read")
def mark_chapter_read(
    chapter_id: int,
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Mark the chapter content as read — this unlocks the chapter quiz."""
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")
    prog = db.query(ChapterProgress).filter(
        ChapterProgress.student_id == student.id,
        ChapterProgress.chapter_id == chapter_id,
    ).first()
    if not prog:
        prog = ChapterProgress(student_id=student.id, chapter_id=chapter_id)
        db.add(prog)
    if not prog.content_read_at:
        prog.content_read_at = datetime.now(timezone.utc)
    db.commit()
    return {"content_read": True}


@router.get("/chapters/{chapter_id}/quiz-state")
def chapter_quiz_state(
    chapter_id: int,
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """
    Everything the chapter screen needs to render the quiz section:
    read gate, the published chapter quiz, and this student's pass state.
    """
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")

    prog = db.query(ChapterProgress).filter(
        ChapterProgress.student_id == student.id,
        ChapterProgress.chapter_id == chapter_id,
    ).first()

    quiz = (
        db.query(Quiz)
        .filter(
            Quiz.chapter_id == chapter_id,
            Quiz.quiz_type == "chapter",
            Quiz.status == QuizStatus.PUBLISHED,
        )
        .order_by(Quiz.created_at.desc())
        .first()
    )
    quiz_info = None
    if quiz:
        quiz_info = {
            "id": quiz.id,
            "title": quiz.title,
            "question_count": len(quiz.questions),
            "total_marks": quiz.total_marks,
            "pass_marks": quiz.pass_marks,
            "require_pass": quiz.require_pass,
            "duration_mins": quiz.duration_mins,
        }

    return {
        "content_read": bool(prog and prog.content_read_at),
        "quiz": quiz_info,
        "quiz_passed": bool(prog and prog.quiz_passed),
        "best_percentage": (prog.quiz_best_percentage if prog else None),
    }
