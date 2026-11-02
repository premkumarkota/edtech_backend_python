"""
Admin — Mock Tests (AI-generated exam-style assessments).

A mock test is a Quiz with quiz_type='mock' spanning selected chapters, a whole
subject, or a term (quarterly/half-yearly/annual). Questions come from AI or
from an uploaded Excel sheet; both paths share _resolve_scope so the scope
label, chapter links and student visibility are identical.

POST   /api/admin/mock-tests/ai-generate     Generate a mock test with AI (draft)
POST   /api/admin/mock-tests/upload          Create a mock test from an Excel sheet (draft)
GET    /api/admin/mock-tests/excel-template  Download the blank .xlsx template
GET    /api/admin/mock-tests                 List mock tests
Reuse for detail/publish/delete/replace questions:
  GET    /api/admin/quiz/{id}
  PATCH  /api/admin/quiz/{id}/publish
  POST   /api/admin/quiz/{id}/upload    (replaces all questions, clears attempts)
  DELETE /api/admin/quiz/{id}
"""
from dataclasses import dataclass

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.quiz import Quiz, QuizQuestion, QuizStatus, MockTestChapter
from app.models.syllabus import Syllabus, Chapter
from app.models.category import Category
from app.schemas.quiz import MockTestGenerate, QuizResponse
from app.services.ai_quiz_service import generate_mock_test, AIQuizError
from app.services.excel_parser import parse_quiz_excel, build_quiz_excel_template
from app.public_errors import http_from_ai_error, public_validation_error

router = APIRouter()

_CONTENT_CHARS_PER_CHAPTER = 1800  # trim each chapter's content for the prompt
_MAX_EXCEL_BYTES = 5 * 1024 * 1024
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@dataclass
class _Scope:
    subjects: List[Syllabus]
    chapters: List[Chapter]
    term: Optional[str]
    mock_scope: str       # chapters | subject | term
    scope_label: str
    subject_label: str


def _resolve_scope(
    db: Session,
    category_id: int,
    syllabus_ids: List[int],
    chapter_ids: List[int],
    term: Optional[str],
) -> _Scope:
    """Validate board → subjects → chapters and derive the scope labels."""
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    if not syllabus_ids:
        raise HTTPException(status_code=422, detail="Select at least one subject")
    subjects = (
        db.query(Syllabus)
        .filter(
            Syllabus.id.in_(syllabus_ids),
            Syllabus.category_id == category_id,
        )
        .all()
    )
    if not subjects:
        raise HTTPException(status_code=404, detail="Subjects not found in this category")
    subject_ids = [s.id for s in subjects]

    all_chapters = (
        db.query(Chapter)
        .filter(Chapter.syllabus_id.in_(subject_ids))
        .order_by(Chapter.syllabus_id, Chapter.order_index, Chapter.id)
        .all()
    )
    if chapter_ids:
        wanted = set(chapter_ids)
        chapters = [ch for ch in all_chapters if ch.id in wanted]
    else:
        chapters = all_chapters
    if not chapters:
        raise HTTPException(status_code=422, detail="No chapters found for the selected subjects")

    subject_titles = [s.title for s in subjects]
    subject_label = (
        ", ".join(subject_titles)
        if len(subject_titles) <= 2
        else f"{len(subject_titles)} Subjects"
    )

    term = (term or "").strip() or None
    if term:
        mock_scope = "term"
        scope_label = term.replace("_", " ").title()
    elif len(chapters) < len(all_chapters):
        mock_scope = "chapters"
        scope_label = f"{len(chapters)} Chapters"
    elif len(subjects) > 1:
        mock_scope = "subject"
        scope_label = "Multiple Subjects"
    else:
        mock_scope = "subject"
        scope_label = "Full Subject"

    return _Scope(subjects, chapters, term, mock_scope, scope_label, subject_label)


def _save_mock_test(
    db: Session,
    admin: User,
    category_id: int,
    scope: _Scope,
    title: str,
    questions_data: List[dict],
    duration_mins: int,
    pass_marks: int,
    negative_marking: bool,
) -> QuizResponse:
    """Persist a draft mock test with its questions and chapter links."""
    total = sum(q["marks"] for q in questions_data)
    pass_marks = (
        min(pass_marks, total)
        if pass_marks and pass_marks > 0
        else max(1, round(total * 0.33))  # exam-style pass ~33%
    )
    quiz = Quiz(
        category_id=category_id,
        syllabus_id=scope.subjects[0].id,   # primary subject (for student grouping)
        quiz_type="mock",
        mock_scope=scope.mock_scope,
        term=scope.term,
        title=title[:200],
        description=f"Mock test — {scope.subject_label} · {scope.scope_label}",
        duration_mins=max(1, duration_mins),
        total_marks=total,
        pass_marks=pass_marks,
        negative_marking=negative_marking,
        status=QuizStatus.DRAFT,
        created_by=admin.id,
    )
    db.add(quiz)
    db.flush()
    db.add_all([QuizQuestion(quiz_id=quiz.id, **q) for q in questions_data])
    db.add_all([MockTestChapter(quiz_id=quiz.id, chapter_id=ch.id) for ch in scope.chapters])
    db.commit()
    db.refresh(quiz)

    r = QuizResponse.from_orm(quiz)
    r.question_count = len(questions_data)
    return r


def _id_list(raw: Optional[str]) -> List[int]:
    """'3, 5,8' → [3, 5, 8] (multipart forms send lists as comma-separated text)."""
    out = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            raise HTTPException(status_code=422, detail=f"Invalid id: {part!r}")
        out.append(int(part))
    return out


@router.get("", response_model=List[QuizResponse])
def list_mock_tests(
    category_id: Optional[int] = None,
    syllabus_id: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Quiz).filter(Quiz.quiz_type == "mock")
    if category_id:
        q = q.filter(Quiz.category_id == category_id)
    if syllabus_id:
        q = q.filter(Quiz.syllabus_id == syllabus_id)
    quizzes = q.order_by(Quiz.created_at.desc()).all()
    out = []
    for quiz in quizzes:
        r = QuizResponse.from_orm(quiz)
        r.question_count = len(quiz.questions)
        out.append(r)
    return out


@router.post("/ai-generate", response_model=QuizResponse)
def ai_generate_mock_test(
    payload: MockTestGenerate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    scope = _resolve_scope(
        db, payload.category_id, payload.syllabus_ids, payload.chapter_ids, payload.term,
    )
    outlines = [
        {"title": ch.title, "content": (ch.description or "")[:_CONTENT_CHARS_PER_CHAPTER]}
        for ch in scope.chapters
    ]

    try:
        result = generate_mock_test(
            scope_label=scope.scope_label,
            chapter_outlines=outlines,
            num_questions=payload.num_questions,
            difficulty=payload.difficulty,
            marks_per_question=1,
            subject=scope.subject_label,
            language=payload.language,
        )
    except AIQuizError as e:
        raise http_from_ai_error(e)

    questions_data = result["questions"]
    if not questions_data:
        raise HTTPException(status_code=502, detail="AI returned no questions. Try again.")
    title = (
        payload.title or result["title"] or f"{scope.subject_label} — {scope.scope_label}"
    ).strip()

    return _save_mock_test(
        db, admin, payload.category_id, scope, title, questions_data,
        payload.duration_mins, payload.pass_marks, payload.negative_marking,
    )


@router.get("/excel-template")
def download_excel_template(admin: User = Depends(require_admin)):
    """Blank question sheet in the format /upload expects."""
    return Response(
        content=build_quiz_excel_template(),
        media_type=_XLSX_MIME,
        headers={"Content-Disposition": 'attachment; filename="mock_test_template.xlsx"'},
    )


@router.post("/upload", response_model=QuizResponse)
async def upload_mock_test_excel(
    file: UploadFile = File(...),
    category_id: int = Form(...),
    syllabus_ids: str = Form(...),          # comma-separated subject ids
    chapter_ids: str = Form(""),            # comma-separated; empty => all chapters
    term: Optional[str] = Form(None),
    title: Optional[str] = Form(None),
    duration_mins: int = Form(60),
    pass_marks: int = Form(0),
    negative_marking: bool = Form(False),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Create a draft mock test from an Excel sheet (same columns as chapter
    quizzes, see excel_parser). Scope rules match ai-generate. The file is
    fully validated before anything is written.
    """
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise HTTPException(status_code=422, detail="Upload an .xlsx file (Excel 2007 or newer).")
    contents = await file.read()
    if len(contents) > _MAX_EXCEL_BYTES:
        raise HTTPException(status_code=413, detail="File is larger than 5 MB.")

    scope = _resolve_scope(db, category_id, _id_list(syllabus_ids), _id_list(chapter_ids), term)

    try:
        questions_data = parse_quiz_excel(contents)
    except ValueError as e:
        raise public_validation_error(e)

    name = (title or "").strip()
    if not name:
        stem = (file.filename or "").rsplit(".", 1)[0].replace("_", " ").strip()
        name = stem or f"{scope.subject_label} — {scope.scope_label}"

    return _save_mock_test(
        db, admin, category_id, scope, name, questions_data,
        duration_mins, pass_marks, negative_marking,
    )
