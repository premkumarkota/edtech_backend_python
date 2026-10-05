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


def _chapter_status(read: bool, quiz, prog, attempts: int) -> str:
    """not_started | quiz_pending | retake | completed"""
    if not read and attempts == 0:
        return "not_started"
    if quiz is None:
        return "completed" if read else "not_started"
    if attempts == 0:
        return "quiz_pending"
    passed = bool(prog and prog.quiz_passed)
    if quiz.require_pass and not passed:
        return "retake"
    return "completed"


@router.get("/{syllabus_id}/progress")
def syllabus_progress(
    syllabus_id: int,
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """
    Per-chapter completion for the chapter list: read state, chapter-quiz best
    score, pass mark and whether the student must retake.
    """
    from sqlalchemy import func
    from app.models.quiz import QuizAttempt, AttemptStatus

    chapter_ids = [
        cid for (cid,) in db.query(Chapter.id).filter(Chapter.syllabus_id == syllabus_id)
    ]
    if not chapter_ids:
        return {"chapters": [], "completed_count": 0, "total_count": 0}

    progress = {
        p.chapter_id: p
        for p in db.query(ChapterProgress).filter(
            ChapterProgress.student_id == student.id,
            ChapterProgress.chapter_id.in_(chapter_ids),
        )
    }

    # Latest published chapter quiz per chapter.
    quizzes = {}
    for q in (
        db.query(Quiz)
        .filter(
            Quiz.chapter_id.in_(chapter_ids),
            Quiz.quiz_type == "chapter",
            Quiz.status == QuizStatus.PUBLISHED,
        )
        .order_by(Quiz.created_at.asc())
    ):
        quizzes[q.chapter_id] = q

    attempts = {}
    last_pct = {}
    if quizzes:
        quiz_ids = [q.id for q in quizzes.values()]
        rows = (
            db.query(QuizAttempt.quiz_id, func.count(QuizAttempt.id))
            .filter(
                QuizAttempt.student_id == student.id,
                QuizAttempt.quiz_id.in_(quiz_ids),
                QuizAttempt.status == AttemptStatus.COMPLETED,
            )
            .group_by(QuizAttempt.quiz_id)
        )
        attempts = {qid: n for qid, n in rows}
        for a in (
            db.query(QuizAttempt)
            .filter(
                QuizAttempt.student_id == student.id,
                QuizAttempt.quiz_id.in_(quiz_ids),
                QuizAttempt.status == AttemptStatus.COMPLETED,
            )
            .order_by(QuizAttempt.completed_at.asc())
        ):
            last_pct[a.quiz_id] = a.percentage

    chapters = []
    completed = 0
    for cid in chapter_ids:
        prog = progress.get(cid)
        quiz = quizzes.get(cid)
        read = bool(prog and prog.content_read_at)
        n = attempts.get(quiz.id, 0) if quiz else 0
        state = _chapter_status(read, quiz, prog, n)
        if state == "completed":
            completed += 1
        pass_pct = None
        if quiz and quiz.total_marks:
            pass_pct = round(quiz.pass_marks / quiz.total_marks * 100, 1)
        chapters.append({
            "chapter_id": cid,
            "status": state,
            "content_read": read,
            "has_quiz": quiz is not None,
            "quiz_id": quiz.id if quiz else None,
            "quiz_attempts": n,
            "best_percentage": prog.quiz_best_percentage if prog else None,
            "last_percentage": last_pct.get(quiz.id) if quiz else None,
            "pass_percentage": pass_pct,
            "quiz_passed": bool(prog and prog.quiz_passed),
            "require_pass": bool(quiz and quiz.require_pass),
        })

    return {
        "chapters": chapters,
        "completed_count": completed,
        "total_count": len(chapter_ids),
    }
