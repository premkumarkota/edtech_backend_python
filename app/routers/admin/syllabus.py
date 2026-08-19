from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from sqlalchemy.exc import ProgrammingError, SQLAlchemyError
from typing import List, Optional, Callable
import logging

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
from app.models.category import Category
from app.models.quiz import Quiz, QuizQuestion, QuizStatus, MockTestChapter, ChapterProgress
from app.models.study_planner_v2 import (
    GoalExamSubject,
    StudyGoalSubject,
    StudyCalendarEntry,
    StudyMcqBank,
    StudyMcqAttempt,
)
from app.schemas.syllabus import (
    SyllabusSummary,
    SyllabusCreate,
    ChapterResponse,
    SyllabusContentResponse,
    ChapterBase,
    SyllabusDetailStudent,
    ChapterStudentResponse,
)
from app.schemas.syllabus_admin_requests import (
    ReorderChapterContentsRequest,
    SyllabusContentPatch,
    ChapterPatch,
    AIChapterContentRequest,
)
from app.services.ai_content_service import generate_content, refine_content, AIContentError
from app.schemas.quiz import ChapterQuizGenerate, QuizResponse, QuizQuestionWithAnswer
from app.services.ai_quiz_service import generate_from_content, AIQuizError
from app.services.syllabus_layout import syllabus_to_detail_with_layout, chapter_to_layout_response
from app.services.storage_service import (
    upload_file,
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_IMAGE_TYPES,
    ALLOWED_VIDEO_TYPES,
    SYLLABUS_CONTENT_MAX_DOC_MB,
    SYLLABUS_CONTENT_MAX_VIDEO_MB,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _run_optional_detach(db: Session, label: str, fn: Callable[[], None]) -> None:
    """Run a cleanup that may fail if a later table is missing on older DBs."""
    try:
        with db.begin_nested():
            fn()
    except ProgrammingError:
        logger.warning("Optional detach skipped (%s) — table may not exist", label, exc_info=True)


def _detach_chapter_dependents(db: Session, chapter_ids: list[int]) -> None:
    """Clear / delete rows that block chapter (and thus syllabus) deletion."""
    if not chapter_ids:
        return

    db.query(Quiz).filter(Quiz.chapter_id.in_(chapter_ids)).update(
        {Quiz.chapter_id: None},
        synchronize_session=False,
    )

    def _progress() -> None:
        db.query(ChapterProgress).filter(
            ChapterProgress.chapter_id.in_(chapter_ids)
        ).delete(synchronize_session=False)

    def _mock_links() -> None:
        db.query(MockTestChapter).filter(
            MockTestChapter.chapter_id.in_(chapter_ids)
        ).delete(synchronize_session=False)

    def _calendar() -> None:
        db.query(StudyCalendarEntry).filter(
            StudyCalendarEntry.chapter_id.in_(chapter_ids)
        ).update(
            {StudyCalendarEntry.chapter_id: None},
            synchronize_session=False,
        )

    def _mcq_banks() -> None:
        bank_ids = [
            row.id
            for row in db.query(StudyMcqBank.id)
            .filter(StudyMcqBank.chapter_id.in_(chapter_ids))
            .all()
        ]
        if not bank_ids:
            return
        db.query(StudyMcqAttempt).filter(
            StudyMcqAttempt.mcq_bank_id.in_(bank_ids)
        ).delete(synchronize_session=False)
        db.query(StudyMcqBank).filter(StudyMcqBank.id.in_(bank_ids)).delete(
            synchronize_session=False
        )

    _run_optional_detach(db, "chapter_progress", _progress)
    _run_optional_detach(db, "mock_test_chapters", _mock_links)
    _run_optional_detach(db, "study_calendar_entries", _calendar)
    _run_optional_detach(db, "study_mcq_banks", _mcq_banks)

    db.query(SyllabusContent).filter(
        SyllabusContent.chapter_id.in_(chapter_ids)
    ).delete(synchronize_session=False)


def _detach_syllabus_dependents(db: Session, syllabus_id: int) -> list[int]:
    """Unlink quizzes / study-planner rows, return chapter ids for this syllabus."""
    chapter_ids = [
        row.id
        for row in db.query(Chapter.id).filter(Chapter.syllabus_id == syllabus_id).all()
    ]

    db.query(Quiz).filter(Quiz.syllabus_id == syllabus_id).update(
        {Quiz.syllabus_id: None, Quiz.chapter_id: None},
        synchronize_session=False,
    )
    db.query(GoalExamSubject).filter(
        GoalExamSubject.syllabus_id == syllabus_id
    ).delete(synchronize_session=False)
    db.query(StudyGoalSubject).filter(
        StudyGoalSubject.syllabus_id == syllabus_id
    ).delete(synchronize_session=False)
    db.query(StudyCalendarEntry).filter(
        StudyCalendarEntry.syllabus_id == syllabus_id
    ).update(
        {StudyCalendarEntry.syllabus_id: None},
        synchronize_session=False,
    )

    _detach_chapter_dependents(db, chapter_ids)
    if chapter_ids:
        db.query(Chapter).filter(Chapter.id.in_(chapter_ids)).delete(
            synchronize_session=False
        )
    return chapter_ids

# --- Syllabus (Subject/Course) ---
@router.post("", response_model=SyllabusSummary)
async def create_syllabus(
    category_id: int = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    thumbnail: Optional[UploadFile] = File(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    thumbnail_url = None
    if thumbnail:
        thumbnail_url = await upload_file(
            thumbnail,
            folder="syllabus/thumbnails",
            allowed_types=ALLOWED_IMAGE_TYPES,
            max_size_mb=5,
        )

    syllabus = Syllabus(
        category_id=category_id,
        title=title,
        description=description,
        thumbnail_url=thumbnail_url
    )
    db.add(syllabus)
    db.commit()
    db.refresh(syllabus)
    return syllabus

@router.get("", response_model=List[SyllabusSummary])
def list_syllabus(
    category_id: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Syllabus)
    if category_id:
        q = q.filter(Syllabus.category_id == category_id)
    
    # We want to add chapter count
    results = q.order_by(Syllabus.created_at.desc()).all()
    for s in results:
        s.chapter_count = db.query(Chapter).filter(Chapter.syllabus_id == s.id).count()
    return results

@router.get("/{syllabus_id}", response_model=SyllabusDetailStudent)
def get_syllabus_detail(
    syllabus_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Same enriched shape as the student app (hero_video, document_contents,
    extra_videos) so the admin panel can preview Udemy-style grouping.
    """
    s = (
        db.query(Syllabus)
        .options(
            joinedload(Syllabus.chapters).joinedload(Chapter.contents),
        )
        .filter(Syllabus.id == syllabus_id)
        .first()
    )
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    return syllabus_to_detail_with_layout(s, include_unpublished=True)

@router.put("/{syllabus_id}", response_model=SyllabusSummary)
async def update_syllabus(
    syllabus_id: int,
    category_id: Optional[int] = Form(None),
    title: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    thumbnail: Optional[UploadFile] = File(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    syllabus = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not syllabus:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    if category_id is not None:
        syllabus.category_id = category_id
    if title is not None:
        syllabus.title = title
    if description is not None:
        syllabus.description = description
    if thumbnail:
        thumbnail_url = await upload_file(
            thumbnail,
            folder="syllabus/thumbnails",
            allowed_types=ALLOWED_IMAGE_TYPES,
            max_size_mb=5,
        )
        syllabus.thumbnail_url = thumbnail_url
    db.commit()
    db.refresh(syllabus)
    return syllabus

@router.delete("/{syllabus_id}")
def delete_syllabus(syllabus_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    s = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    try:
        _detach_syllabus_dependents(db, syllabus_id)
        db.query(Syllabus).filter(Syllabus.id == syllabus_id).delete(
            synchronize_session=False
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Failed to delete syllabus %s", syllabus_id)
        orig = getattr(e, "orig", e)
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete this subject because other records still reference it. "
                f"{orig}"
            ),
        ) from e
    return {"message": "Deleted"}

# --- Chapters ---
@router.post("/{syllabus_id}/chapters", response_model=ChapterResponse)
def create_chapter(
    syllabus_id: int,
    payload: ChapterBase,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    chapter = Chapter(
        syllabus_id=syllabus_id,
        title=payload.title,
        description=payload.description,
        order_index=payload.order_index
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter

@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    try:
        _detach_chapter_dependents(db, [chapter_id])
        db.query(Chapter).filter(Chapter.id == chapter_id).delete(
            synchronize_session=False
        )
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("Failed to delete chapter %s", chapter_id)
        orig = getattr(e, "orig", e)
        raise HTTPException(
            status_code=409,
            detail=(
                "Cannot delete this chapter because other records still reference it. "
                f"{orig}"
            ),
        ) from e
    return {"message": "Deleted"}


@router.patch("/chapters/{chapter_id}", response_model=ChapterResponse)
def update_chapter(
    chapter_id: int,
    payload: ChapterPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update a chapter's title and/or content (description)."""
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if payload.title is not None:
        c.title = payload.title
    if payload.description is not None:
        c.description = payload.description
        # Editing content sends it back to draft — must be re-published.
        c.content_published = False
    db.commit()
    db.refresh(c)
    return c


@router.post("/chapters/{chapter_id}/publish-content", response_model=ChapterResponse)
def publish_chapter_content(
    chapter_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Publish a chapter's content so students can see it."""
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    if not (c.description or "").strip():
        raise HTTPException(status_code=400, detail="No content to publish")
    c.content_published = True
    db.commit()
    db.refresh(c)
    return c


@router.post("/chapters/{chapter_id}/unpublish-content", response_model=ChapterResponse)
def unpublish_chapter_content(
    chapter_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Hide a chapter's content from students (revert to draft)."""
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    c.content_published = False
    db.commit()
    db.refresh(c)
    return c


# --- Chapter Quiz (generated from the chapter's content) ---

def _chapter_quiz_payload(db: Session, chapter_id: int) -> dict:
    quiz = (
        db.query(Quiz)
        .filter(Quiz.chapter_id == chapter_id, Quiz.quiz_type == "chapter")
        .order_by(Quiz.created_at.desc())
        .first()
    )
    if not quiz:
        return {"quiz": None, "questions": []}
    r = QuizResponse.from_orm(quiz)
    r.question_count = len(quiz.questions)
    qs = sorted(quiz.questions, key=lambda x: x.order_index)
    return {
        "quiz": r,
        "questions": [QuizQuestionWithAnswer.from_orm(q).dict() for q in qs],
    }


@router.get("/chapters/{chapter_id}/quiz")
def get_chapter_quiz(
    chapter_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return the chapter's quiz (with questions + answers) or {quiz: null}."""
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return _chapter_quiz_payload(db, chapter_id)


@router.post("/chapters/{chapter_id}/ai-quiz")
def ai_generate_chapter_quiz(
    chapter_id: int,
    payload: ChapterQuizGenerate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Generate a chapter quiz FROM the chapter's content (draft). Replaces any
    existing chapter quiz for this chapter.
    """
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")
    content = (c.description or "").strip()
    if not content:
        raise HTTPException(
            status_code=422,
            detail="Generate the chapter content first — the quiz is created from it.",
        )

    syllabus = db.query(Syllabus).filter(Syllabus.id == c.syllabus_id).first()
    if not syllabus:
        raise HTTPException(status_code=400, detail="Chapter has no subject")
    subject_name = syllabus.title

    try:
        result = generate_from_content(
            content=content,
            num_questions=payload.num_questions,
            difficulty=payload.difficulty,
            marks_per_question=payload.marks_per_question,
            subject=subject_name,
            chapter=c.title,
        )
    except AIQuizError as e:
        raise HTTPException(status_code=502, detail=str(e))

    questions_data = result["questions"]
    if not questions_data:
        raise HTTPException(status_code=502, detail="AI returned no questions. Try again.")
    total = sum(q["marks"] for q in questions_data)
    pass_marks = (
        payload.pass_marks
        if payload.pass_marks and payload.pass_marks > 0
        else max(1, round(total * 0.6))
    )

    # One chapter quiz per chapter — remove the old one (cascade deletes its
    # questions/attempts).
    for old in (
        db.query(Quiz)
        .filter(Quiz.chapter_id == chapter_id, Quiz.quiz_type == "chapter")
        .all()
    ):
        db.delete(old)
    db.flush()

    quiz = Quiz(
        category_id=syllabus.category_id,
        syllabus_id=c.syllabus_id,
        chapter_id=chapter_id,
        title=result["title"][:200],
        description=f"Chapter quiz for {c.title}",
        quiz_type="chapter",
        duration_mins=max(5, len(questions_data)),
        total_marks=total,
        pass_marks=pass_marks,
        require_pass=payload.require_pass,
        status=QuizStatus.DRAFT,
        created_by=admin.id,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)
    db.add_all([QuizQuestion(quiz_id=quiz.id, **q) for q in questions_data])
    db.commit()
    return _chapter_quiz_payload(db, chapter_id)


@router.post("/chapters/{chapter_id}/ai-content", response_model=ChapterResponse)
def ai_generate_chapter_content(
    chapter_id: int,
    payload: AIChapterContentRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """
    Generate rich Markdown study content for a chapter with AI and save it to the
    chapter's description. Category & subject are derived from the chapter.
    """
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")

    topic = (payload.topic or "").strip()
    instruction = (payload.instruction or "").strip()
    current = (c.description or "").strip()
    level = (payload.level or "General").strip()

    # Refine existing content when an instruction is given and content exists;
    # otherwise generate fresh content (which needs a topic).
    is_refine = bool(instruction and current)
    if not is_refine and not topic:
        raise HTTPException(status_code=422, detail="Topic is required")

    syllabus = db.query(Syllabus).filter(Syllabus.id == c.syllabus_id).first()
    subject_name = syllabus.title if syllabus else ""
    category_name = ""
    if syllabus:
        cat = db.query(Category).filter(Category.id == syllabus.category_id).first()
        category_name = cat.name if cat else ""

    try:
        if is_refine:
            content = refine_content(
                current_content=current,
                instruction=instruction,
                level=level,
                subject=subject_name,
                chapter=c.title,
            )
        else:
            content = generate_content(
                category_name=category_name,
                subject=subject_name,
                chapter=c.title,
                topic=topic,
                level=level,
            )
    except AIContentError as e:
        raise HTTPException(status_code=502, detail=str(e))

    c.description = content
    c.content_published = False  # draft — admin must review & publish
    db.commit()
    db.refresh(c)
    return c

# --- Content ---
@router.post("/chapters/{chapter_id}/content", response_model=SyllabusContentResponse)
async def create_content(
    chapter_id: int,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    content_type: str = Form(...), # video, pdf, ppt
    file: UploadFile = File(...),
    order_index: Optional[int] = Form(default=None),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")

    ct = (content_type or "").strip().lower()
    if ct == "video":
        allowed = ALLOWED_VIDEO_TYPES
        max_mb = SYLLABUS_CONTENT_MAX_VIDEO_MB
    elif ct in ("pdf", "ppt", "pptx", "document", "deck"):
        # Store as pdf / ppt in DB — map generic labels to document MIME whitelist
        if ct == "pptx":
            ct = "ppt"
        if ct == "document":
            ct = "pdf"
        if ct == "deck":
            ct = "ppt"
        allowed = ALLOWED_DOCUMENT_TYPES
        max_mb = SYLLABUS_CONTENT_MAX_DOC_MB
    else:
        raise HTTPException(
            status_code=400,
            detail="content_type must be one of: video, pdf, ppt",
        )

    placement = order_index
    if placement is None:
        mx = (
            db.query(func.max(SyllabusContent.order_index))
            .filter(SyllabusContent.chapter_id == chapter_id)
            .scalar()
        )
        placement = (mx if mx is not None else -1) + 1

    # Upload file (typed allowlist + syllabus-specific size caps)
    file_url = await upload_file(
        file,
        folder=f"syllabus/content/{chapter_id}",
        allowed_types=allowed,
        max_size_mb=max_mb,
    )
    
    content = SyllabusContent(
        chapter_id=chapter_id,
        title=title,
        description=description,
        content_type=ct,
        file_url=file_url,
        order_index=placement,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content

@router.patch("/content/{content_id}", response_model=SyllabusContentResponse)
def patch_content(
    content_id: int,
    payload: SyllabusContentPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ct = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not ct:
        raise HTTPException(status_code=404, detail="Content not found")
    if payload.title is not None:
        t = payload.title.strip()
        if not t:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        ct.title = t
    if payload.description is not None:
        ct.description = payload.description
    if payload.order_index is not None:
        ct.order_index = payload.order_index
    db.commit()
    db.refresh(ct)
    return ct


@router.put(
    "/chapters/{chapter_id}/contents/reorder",
    response_model=ChapterStudentResponse,
)
def reorder_chapter_contents(
    chapter_id: int,
    payload: ReorderChapterContentsRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")

    ids = payload.ordered_content_ids
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=400, detail="ordered_content_ids must not contain duplicates"
        )
    by_id = {c.id: c for c in ch.contents}
    if set(ids) != set(by_id.keys()):
        raise HTTPException(
            status_code=400,
            detail=(
                "ordered_content_ids must list every content row in this chapter "
                "exactly once, in playback order."
            ),
        )

    for idx, cid in enumerate(ids):
        by_id[cid].order_index = idx
    db.commit()
    db.refresh(ch)
    for row in ch.contents:
        db.refresh(row)

    # Re-fetch for clean ordering (relationship collection may be unordered)
    ch2 = (
        db.query(Chapter)
        .options(joinedload(Chapter.contents))
        .filter(Chapter.id == chapter_id)
        .first()
    )
    return chapter_to_layout_response(ch2, include_unpublished=True)


@router.delete("/content/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    ct = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not ct: raise HTTPException(404)
    db.delete(ct)
    db.commit()
    return {"message": "Deleted"}
