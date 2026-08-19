"""
Admin — Quiz Management
POST   /api/admin/quiz/                   Create quiz shell
POST   /api/admin/quiz/ai-generate        Generate a quiz with AI (Claude) — draft
POST   /api/admin/quiz/{id}/upload        Upload Excel to add questions
GET    /api/admin/quiz/                   List all quizzes
GET    /api/admin/quiz/{id}               Get quiz with questions
PATCH  /api/admin/quiz/{id}/publish       Publish quiz
PATCH  /api/admin/quiz/{id}/unpublish     Unpublish quiz
DELETE /api/admin/quiz/{id}               Delete quiz
"""

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.quiz import Quiz, QuizQuestion, QuizStatus, QuizAttempt, QuizAnswer
from app.models.category import Category
from app.schemas.quiz import QuizCreate, QuizResponse, QuizQuestionWithAnswer, AIQuizGenerate
from app.services.excel_parser import parse_quiz_excel
from app.services.ai_quiz_service import generate_quiz, AIQuizError

router = APIRouter()


@router.post("/ai-generate", response_model=QuizResponse)
def ai_generate_quiz(
    payload: AIQuizGenerate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Generate a quiz with AI for a category + chapter/topic.

    The generated quiz is saved as a DRAFT (never auto-published) so an admin can
    review the questions and answers before publishing it to students.
    """
    cat = db.query(Category).filter(Category.id == payload.category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    topic = (payload.topic or "").strip()
    if not topic:
        raise HTTPException(status_code=422, detail="Chapter / topic is required")

    subject = (payload.subject or "").strip()

    try:
        result = generate_quiz(
            category_name=cat.name,
            subject=subject,
            topic=topic,
            difficulty=(payload.difficulty or "medium").strip(),
            num_questions=payload.num_questions,
            marks_per_question=payload.marks_per_question,
        )
    except AIQuizError as e:
        # 502: upstream AI failure (bad key, API error, empty result)
        raise HTTPException(status_code=502, detail=str(e))

    questions_data = result["questions"]
    if not questions_data:
        raise HTTPException(status_code=502, detail="AI returned no questions. Try again.")

    # Link to a subject (syllabus) when provided & valid for this category,
    # so the student app can group quizzes subject-wise.
    syllabus_id = None
    if payload.syllabus_id:
        from app.models.syllabus import Syllabus
        syl = db.query(Syllabus).filter(
            Syllabus.id == payload.syllabus_id,
            Syllabus.category_id == payload.category_id,
        ).first()
        if syl:
            syllabus_id = syl.id

    quiz = Quiz(
        category_id=payload.category_id,
        syllabus_id=syllabus_id,
        title=result["title"][:200],
        description=(
            f"AI-generated quiz on '{topic}'"
            + (f" — {subject}" if subject else "")
            + f" ({payload.difficulty})."
        ),
        duration_mins=payload.duration_mins,
        pass_marks=payload.pass_marks,
        total_marks=sum(q["marks"] for q in questions_data),
        status=QuizStatus.DRAFT,
        created_by=admin.id,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)

    questions = [QuizQuestion(quiz_id=quiz.id, **q) for q in questions_data]
    db.add_all(questions)
    db.commit()

    response = QuizResponse.from_orm(quiz)
    response.question_count = len(questions)
    return response


@router.post("", response_model=QuizResponse)
def create_quiz(
    payload: QuizCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new quiz shell (no questions yet)."""
    cat = db.query(Category).filter(Category.id == payload.category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    quiz = Quiz(
        category_id=payload.category_id,
        syllabus_id=payload.syllabus_id,
        chapter_id=payload.chapter_id,
        title=payload.title,
        description=payload.description,
        duration_mins=payload.duration_mins,
        pass_marks=payload.pass_marks,
        total_marks=0,
        status=QuizStatus.DRAFT,
        created_by=admin.id,
    )
    db.add(quiz)
    db.commit()
    db.refresh(quiz)

    response = QuizResponse.from_orm(quiz)
    response.question_count = 0
    return response


@router.post("/{quiz_id}/upload")
async def upload_quiz_excel(
    quiz_id: int,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Upload an Excel file (.xlsx) to add questions to a quiz.
    Replaces all existing questions if the quiz already has questions.
    """
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # If published, automatically move back to DRAFT for modification
    if quiz.status == QuizStatus.PUBLISHED:
        quiz.status = QuizStatus.DRAFT
        # We don't raise 400 anymore, we allow the override as requested

    # Read file
    contents = await file.read()

    # Parse Excel
    try:
        questions_data = parse_quiz_excel(contents)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Delete existing questions and all related data (attempts, answers) to allow a clean replace
    # We delete answers first, then attempts, then questions to respect FK constraints
    db.query(QuizAnswer).filter(QuizAnswer.question_id.in_(
        db.query(QuizQuestion.id).filter(QuizQuestion.quiz_id == quiz_id)
    )).delete(synchronize_session=False)
    
    db.query(QuizAttempt).filter(QuizAttempt.quiz_id == quiz_id).delete(synchronize_session=False)
    db.query(QuizQuestion).filter(QuizQuestion.quiz_id == quiz_id).delete(synchronize_session=False)

    # Bulk insert new questions
    questions = [QuizQuestion(quiz_id=quiz_id, **q) for q in questions_data]
    db.add_all(questions)

    # Update quiz total_marks
    quiz.total_marks = sum(q["marks"] for q in questions_data)
    db.commit()

    return {
        "message": f"Successfully uploaded {len(questions)} questions",
        "quiz_id": quiz_id,
        "questions_added": len(questions),
        "total_marks": quiz.total_marks,
    }


@router.post("/{quiz_id}/questions/manual")
async def add_manual_question(
    quiz_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Manually add a single question to the quiz."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    
    # Calculate next order_index
    max_idx = db.query(QuizQuestion.order_index).filter(QuizQuestion.quiz_id == quiz_id).order_by(QuizQuestion.order_index.desc()).first()
    next_idx = (max_idx[0] + 1) if max_idx else 0

    question = QuizQuestion(
        quiz_id=quiz_id,
        question_text=payload.get("question_text"),
        option_a=payload.get("option_a"),
        option_b=payload.get("option_b"),
        option_c=payload.get("option_c"),
        option_d=payload.get("option_d"),
        correct_option=payload.get("correct_option"),
        marks=payload.get("marks", 1),
        explanation=payload.get("explanation"),
        order_index=next_idx
    )
    db.add(question)

    # Update quiz total marks
    quiz.total_marks += question.marks
    db.commit()

    return {"status": "success", "question_id": question.id}


@router.patch("/questions/{question_id}")
def update_question(
    question_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Edit a single question. Recomputes the quiz total marks."""
    q = db.query(QuizQuestion).filter(QuizQuestion.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")

    for field in ("question_text", "option_a", "option_b", "option_c",
                  "option_d", "explanation"):
        if field in payload:
            setattr(q, field, payload[field])
    if payload.get("correct_option"):
        c = str(payload["correct_option"]).strip().upper()
        if c not in ("A", "B", "C", "D"):
            raise HTTPException(status_code=422, detail="correct_option must be A, B, C or D")
        q.correct_option = c
    if "marks" in payload:
        try:
            q.marks = max(1, int(payload["marks"]))
        except (ValueError, TypeError):
            pass

    db.commit()

    quiz = db.query(Quiz).filter(Quiz.id == q.quiz_id).first()
    if quiz:
        quiz.total_marks = sum(qq.marks for qq in quiz.questions)
        db.commit()
    return {"status": "success", "question_id": question_id}


@router.delete("/questions/{question_id}")
def delete_question(
    question_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a single question. Recomputes the quiz total marks."""
    q = db.query(QuizQuestion).filter(QuizQuestion.id == question_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="Question not found")
    quiz_id = q.quiz_id
    # Remove any answers referencing this question, then the question.
    db.query(QuizAnswer).filter(QuizAnswer.question_id == question_id).delete(
        synchronize_session=False)
    db.delete(q)
    db.commit()

    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if quiz:
        quiz.total_marks = sum(qq.marks for qq in quiz.questions)
        db.commit()
    return {"status": "success"}


@router.get("", response_model=List[QuizResponse])
def list_quizzes(
    category_id: int = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all quizzes with question count."""
    q = db.query(Quiz)
    if category_id:
        q = q.filter(Quiz.category_id == category_id)
    quizzes = q.order_by(Quiz.created_at.desc()).all()

    results = []
    for quiz in quizzes:
        r = QuizResponse.from_orm(quiz)
        r.question_count = len(quiz.questions)
        results.append(r)
    return results


@router.get("/{quiz_id}")
def get_quiz(
    quiz_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get quiz details including all questions (with correct answers — admin only)."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    questions = [QuizQuestionWithAnswer.from_orm(q) for q in quiz.questions]

    return {
        "id": quiz.id,
        "title": quiz.title,
        "description": quiz.description,
        "category_id": quiz.category_id,
        "duration_mins": quiz.duration_mins,
        "total_marks": quiz.total_marks,
        "pass_marks": quiz.pass_marks,
        "status": quiz.status,
        "question_count": len(questions),
        "questions": [q.dict() for q in questions],
        "created_at": quiz.created_at,
    }


@router.patch("/{quiz_id}/publish")
def publish_quiz(
    quiz_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Publish a quiz so students can see it."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    if not quiz.questions:
        raise HTTPException(status_code=400, detail="Cannot publish quiz with no questions")

    quiz.status = QuizStatus.PUBLISHED
    db.commit()
    return {"message": "Quiz published successfully", "quiz_id": quiz_id}


@router.patch("/{quiz_id}/unpublish")
def unpublish_quiz(
    quiz_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Unpublish quiz (move back to draft)."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    quiz.status = QuizStatus.DRAFT
    db.commit()
    return {"message": "Quiz unpublished", "quiz_id": quiz_id}


@router.delete("/{quiz_id}")
def delete_quiz(
    quiz_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Delete a quiz and all its questions."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")
    db.delete(quiz)
    db.commit()
    return {"message": "Quiz deleted successfully"}
