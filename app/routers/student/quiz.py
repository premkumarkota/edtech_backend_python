from fastapi import APIRouter, Depends, HTTPException, Body, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.models.quiz import Quiz, QuizQuestion, QuizAttempt, QuizAnswer, QuizStatus, AttemptStatus, ChapterProgress
from app.services.quiz_marks import effective_pass_marks, chapter_quiz_passed
from app.services.streak_service import safe_record as record_streak
from app.services.quiz_retake_service import (
    attempt_questions, has_completed_attempt, prepare_retake_questions, scoring_questions,
)
from app.services.subscription_service import (
    consume_mock_test, mock_test_access, MOCK_SUBSCRIPTION_REQUIRED,
)
from app.schemas.quiz import (
    QuizResponse, QuizQuestionResponse, QuizAttemptResponse,
    QuizSubmit, QuizResultResponse, LeaderboardEntry, GlobalRankEntry,
    QuizStartResponse,
)

router = APIRouter()

@router.get("", response_model=List[QuizResponse])
def list_quizzes(
    syllabus_id: int = None,
    chapter_id: int = None,
    category_id: Optional[int] = Query(None, description="Browse a different category"),
    student: User = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    List published quizzes.

    - No `syllabus_id` (Tests tab): mock tests of the category being browsed.
    - With `syllabus_id` (subject "Practice"): everything for that subject —
      its chapter quizzes (latest published per chapter) plus mock tests that
      cover it (primary subject OR any of its chapters via mock_test_chapters).
      The subject decides the category, so this works while browsing another
      board.

    Each item carries the caller's `attempted` / `best_percentage`, and mock
    tests carry entitlement `locked` / `lock_reason`.
    """
    from app.models.syllabus import Syllabus, Chapter
    from app.models.quiz import MockTestChapter

    if syllabus_id:
        subject = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
        if not subject:
            raise HTTPException(status_code=404, detail="Subject not found")
        chapter_objs = db.query(Chapter).filter(
            Chapter.syllabus_id == syllabus_id
        ).all()
        chapter_titles = {c.id: c.title for c in chapter_objs}
        chapter_ids = list(chapter_titles)
        # Chapters whose written material isn't live yet don't offer a quiz.
        pending_ids = {
            c.id for c in chapter_objs
            if (c.description or "").strip() and not (c.live_description or "").strip()
        }
        if chapter_id:
            chapter_ids = [c for c in chapter_ids if c == chapter_id]

        mock_q = db.query(Quiz).filter(
            Quiz.status == QuizStatus.PUBLISHED,
            Quiz.quiz_type == "mock",
        )
        covering = (
            db.query(MockTestChapter.quiz_id)
            .filter(MockTestChapter.chapter_id.in_(chapter_ids))
            if chapter_ids else None
        )
        if chapter_id:
            mocks = mock_q.filter(Quiz.id.in_(covering)).all() if covering is not None else []
        elif covering is not None:
            mocks = mock_q.filter(
                (Quiz.syllabus_id == syllabus_id) | (Quiz.id.in_(covering))
            ).all()
        else:
            mocks = mock_q.filter(Quiz.syllabus_id == syllabus_id).all()

        chapter_quizzes = []
        if chapter_ids:
            latest = {}
            for qz in (
                db.query(Quiz)
                .filter(
                    Quiz.status == QuizStatus.PUBLISHED,
                    Quiz.quiz_type == "chapter",
                    Quiz.chapter_id.in_(chapter_ids),
                )
                .order_by(Quiz.created_at.asc())
            ):
                latest[qz.chapter_id] = qz  # keep newest per chapter
            # Chapter order: follow the subject's chapter list.
            chapter_quizzes = [latest[c] for c in chapter_ids
                               if c in latest and c not in pending_ids]

        quizzes = chapter_quizzes + sorted(
            mocks, key=lambda q: q.created_at, reverse=True
        )
    else:
        effective_category_id = category_id or student.category_id
        if not effective_category_id:
            raise HTTPException(status_code=400, detail="Complete onboarding first.")
        query = db.query(Quiz).filter(
            Quiz.category_id == effective_category_id,
            Quiz.status == QuizStatus.PUBLISHED,
            Quiz.quiz_type == "mock",   # chapter quizzes live inside the chapter
        )
        if chapter_id:
            query = query.filter(Quiz.chapter_id == chapter_id)
        quizzes = query.order_by(Quiz.created_at.desc()).all()
        chapter_titles = {}

    # Caller's attempt summary for all listed quizzes in one query.
    quiz_ids = [q.id for q in quizzes]
    best = {}
    if quiz_ids:
        for qid, pct in (
            db.query(QuizAttempt.quiz_id, func.max(QuizAttempt.percentage))
            .filter(
                QuizAttempt.student_id == student.id,
                QuizAttempt.quiz_id.in_(quiz_ids),
                QuizAttempt.status == AttemptStatus.COMPLETED,
            )
            .group_by(QuizAttempt.quiz_id)
        ):
            best[qid] = pct

    results = []
    for quiz in quizzes:
        r = QuizResponse.from_orm(quiz)
        r.question_count = len(quiz.questions)
        r.attempted = quiz.id in best
        r.best_percentage = (round(best[quiz.id], 1)
                             if best.get(quiz.id) is not None else None)
        if quiz.quiz_type == "chapter":
            r.chapter_title = chapter_titles.get(quiz.chapter_id)
        if quiz.quiz_type == "mock":
            access = mock_test_access(student.id, quiz.id, db)
            r.locked = not access["allowed"]
            r.lock_reason = access["reason"]
        results.append(r)
    return results

@router.get("/results", response_model=List[QuizAttemptResponse])
def my_results(student: User = Depends(require_student), db: Session = Depends(get_db)):
    """Get all past completed quiz attempts for this student."""
    return db.query(QuizAttempt).filter(
        QuizAttempt.student_id == student.id,
        QuizAttempt.status == AttemptStatus.COMPLETED
    ).order_by(QuizAttempt.completed_at.desc()).all()

@router.get("/{quiz_id}")
def get_quiz(quiz_id: int, student: User = Depends(require_student), db: Session = Depends(get_db)):
    """Get quiz details and questions (without correct answers)."""
    quiz = db.query(Quiz).filter(
        Quiz.id == quiz_id,
        Quiz.status == QuizStatus.PUBLISHED
    ).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

    # Mock tests are a paid, metered entitlement — don't leak questions to
    # students who can't start the test.
    if quiz.quiz_type == "mock":
        access = mock_test_access(student.id, quiz.id, db)
        if not access["allowed"]:
            in_progress = db.query(QuizAttempt.id).filter(
                QuizAttempt.quiz_id == quiz.id,
                QuizAttempt.student_id == student.id,
                QuizAttempt.status == AttemptStatus.IN_PROGRESS,
            ).first()
            if not in_progress:
                detail = (MOCK_SUBSCRIPTION_REQUIRED
                          if access["reason"] == "subscription_required"
                          else "You've used all mock tests in your plan. "
                               "Subscribe again to unlock more mock tests.")
                raise HTTPException(status_code=403, detail=detail)

    questions = [QuizQuestionResponse.from_orm(q) for q in quiz.questions]
    return {
        "id": quiz.id,
        "title": quiz.title,
        "description": quiz.description,
        "duration_mins": quiz.duration_mins,
        "total_marks": quiz.total_marks,
        "pass_marks": quiz.pass_marks,
        "question_count": len(questions),
        "questions": [q.dict() for q in questions],
    }

@router.post("/{quiz_id}/start", response_model=QuizStartResponse)
def start_quiz(
    quiz_id: int,
    fresh_questions: bool = Query(
        False, description="Retakes get a new AI-generated question set (current app builds send true)",
    ),
    student: User = Depends(require_student),
    db: Session = Depends(get_db),
):
    """Initialize a quiz attempt. Used for resuming or creating new attempts.

    The response carries the attempt's `questions`: on a retake (student already
    completed this quiz) with `fresh_questions=true` these are new questions,
    not the ones from GET /quiz/{id}.
    """
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id, Quiz.status == QuizStatus.PUBLISHED).first()
    if not quiz: raise HTTPException(404)

    # Resume existing if 'STARTED'
    existing = db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.student_id == student.id,
        QuizAttempt.status == AttemptStatus.IN_PROGRESS
    ).first()
    if existing: return _start_response(existing, db)

    is_retake = has_completed_attempt(quiz_id, student.id, db)

    # Mock tests: require an active plan and spend one credit per distinct
    # test (retakes within the same subscription are free). Same transaction
    # as the attempt insert, so a failed insert doesn't burn a credit.
    if quiz.quiz_type == "mock":
        consume_mock_test(student.id, quiz.id, db)

    attempt = QuizAttempt(
        quiz_id=quiz_id,
        student_id=student.id,
        status=AttemptStatus.IN_PROGRESS,
        total_marks=quiz.total_marks
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)

    # AI call runs after the commit so the subscription row isn't locked
    # meanwhile; on failure the attempt just keeps the quiz's own questions.
    if fresh_questions and is_retake:
        try:
            if prepare_retake_questions(quiz, attempt, db):
                db.commit()
            else:
                db.rollback()
        except Exception:
            db.rollback()
        db.refresh(attempt)
    return _start_response(attempt, db)


def _start_response(attempt: QuizAttempt, db: Session) -> dict:
    data = QuizAttemptResponse.model_validate(attempt).model_dump()
    data["questions"] = [
        QuizQuestionResponse.model_validate(q).model_dump()
        for q in attempt_questions(attempt, db)
    ]
    data["fresh_questions"] = bool(attempt.question_ids)
    return data

@router.post("/attempt/{attempt_id}/save-answer")
def save_answer(
    attempt_id: int,
    question_id: int = Body(...),
    selected_option: str = Body(...),
    student: User = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Hig-Level Sync: Save answer atomically as the student progresses. 
    Ensures data persistence if the app crashes.
    """
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id, 
        QuizAttempt.student_id == student.id,
        QuizAttempt.status == AttemptStatus.IN_PROGRESS
    ).first()
    if not attempt: raise HTTPException(403, "No active attempt")

    # Update or Create
    ans = db.query(QuizAnswer).filter(
        QuizAnswer.attempt_id == attempt_id, 
        QuizAnswer.question_id == question_id
    ).first()
    
    if ans:
        ans.selected_option = selected_option.upper()
    else:
        ans = QuizAnswer(
            attempt_id=attempt_id,
            question_id=question_id,
            selected_option=selected_option.upper()
        )
        db.add(ans)
    
    db.commit()
    return {"status": "success"}

@router.post("/attempt/{attempt_id}/submit")
def submit_quiz(
    attempt_id: int,
    time_taken_secs: int = Body(...),
    student: User = Depends(require_student),
    db: Session = Depends(get_db)
):
    """
    Finalize the quiz. Backend calculates the score based on saved answers.
    Security: The truth (answers) remains on the server.
    """
    attempt = db.query(QuizAttempt).filter(
        QuizAttempt.id == attempt_id,
        QuizAttempt.student_id == student.id,
        QuizAttempt.status == AttemptStatus.IN_PROGRESS
    ).first()
    if not attempt: raise HTTPException(404)

    quiz = attempt.quiz
    questions = {q.id: q for q in scoring_questions(attempt, db)}
    user_answers = {a.question_id: a for a in attempt.answers}

    score = 0
    total = 0
    for q_id, q in questions.items():
        total += q.marks
        ans = user_answers.get(q_id)
        if ans:
            ans.is_correct = ans.selected_option == q.correct_option
            ans.marks_awarded = q.marks if ans.is_correct else 0
            score += ans.marks_awarded

    attempt.score = score
    attempt.total_marks = total
    attempt.percentage = (score / total * 100) if total > 0 else 0
    attempt.passed = score >= effective_pass_marks(quiz, total)
    attempt.status = AttemptStatus.COMPLETED
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.time_taken_secs = time_taken_secs

    # Update total points in profile
    if student.student_profile:
        student.student_profile.total_points += score

    # Chapter quiz → update chapter progress + enforce the pass gate.
    must_retake = False
    if quiz.quiz_type == "chapter" and quiz.chapter_id:
        prog = db.query(ChapterProgress).filter(
            ChapterProgress.student_id == student.id,
            ChapterProgress.chapter_id == quiz.chapter_id,
        ).first()
        if not prog:
            prog = ChapterProgress(student_id=student.id, chapter_id=quiz.chapter_id)
            db.add(prog)
        pct = attempt.percentage or 0
        if prog.quiz_best_percentage is None or pct > prog.quiz_best_percentage:
            prog.quiz_best_percentage = pct
        if attempt.passed:
            prog.quiz_passed = True
        elif chapter_quiz_passed(quiz, prog):
            prog.quiz_passed = True  # e.g. legacy pass_marks > total
        must_retake = bool(quiz.require_pass and not prog.quiz_passed)

    db.commit()

    # A completed quiz is a learning day (after the commit: a streak failure
    # must never lose the result).
    streak = record_streak(student.id, "mock_test" if quiz.quiz_type == "mock" else "quiz", db)

    return {
        "streak": streak,
        "score": score,
        "total": total,
        "passed": attempt.passed,
        "pass_marks": quiz.pass_marks,
        "require_pass": quiz.require_pass,
        "must_retake": must_retake,
    }

@router.get("/attempt/{attempt_id}/result")
def get_result(attempt_id: int, student: User = Depends(require_student), db: Session = Depends(get_db)):
    """Full feedback including correct options and explanations."""
    attempt = db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id, QuizAttempt.student_id == student.id).first()
    if not attempt or attempt.status != AttemptStatus.COMPLETED:
        raise HTTPException(404)

    breakdown = []
    for q in scoring_questions(attempt, db):
        ans = next((a for a in attempt.answers if a.question_id == q.id), None)
        breakdown.append({
            "question": q.question_text,
            "options": {"A": q.option_a, "B": q.option_b, "C": q.option_c, "D": q.option_d},
            "correct": q.correct_option,
            "selected": ans.selected_option if ans else None,
            "is_correct": ans.is_correct if ans else False,
            "explanation": q.explanation
        })
    return {"score": attempt.score, "total": attempt.total_marks, "breakdown": breakdown}

@router.get("/{quiz_id}/leaderboard", response_model=List[LeaderboardEntry])
def get_leaderboard(quiz_id: int, db: Session = Depends(get_db), student: User = Depends(require_student)):
    """Top 50 ranking. Primary: Score, Secondary: Speed."""
    subq = db.query(
        QuizAttempt.student_id,
        QuizAttempt.score,
        QuizAttempt.time_taken_secs,
        func.row_number().over(
            partition_by=QuizAttempt.student_id,
            order_by=[QuizAttempt.score.desc(), QuizAttempt.time_taken_secs.asc()]
        ).label("rank")
    ).filter(QuizAttempt.quiz_id == quiz_id, QuizAttempt.status == AttemptStatus.COMPLETED).subquery()

    quiz_total = db.query(Quiz.total_marks).filter(Quiz.id == quiz_id).scalar() or 0
    results = db.query(User, subq.c.score, subq.c.time_taken_secs).join(subq, User.id == subq.c.student_id).filter(subq.c.rank == 1).order_by(subq.c.score.desc(), subq.c.time_taken_secs.asc()).limit(50).all()

    return [LeaderboardEntry(
        user_id=u.id, name=u.name or "Student", profile_image_url=u.profile_image_url,
        score=s, time_taken_secs=t, rank=i,
        percentage=(round(s / quiz_total * 100, 1) if quiz_total else 0)
    ) for i, (u, s, t) in enumerate(results, 1)]


@router.get("/leaderboard/global", response_model=List[GlobalRankEntry])
def get_global_rankings(
    category_id: Optional[int] = Query(None, description="Category being viewed; defaults to the student's own"),
    db: Session = Depends(get_db),
    student: User = Depends(require_student),
):
    """
    Category leaderboard (top 50).

    - Members: students whose category is `category_id` (default: the caller's).
    - Points: sum of scores on completed attempts of quizzes in THAT category,
      so points earned while browsing other boards don't leak into this ranking.
    - Ties are ordered by name, then id, so the order is stable.
    - The caller's own row is flagged `is_me`; if they're outside the top 50 it
      is appended (with their real rank) so the app can show "Your position".
    """
    from app.models.user import UserRole

    cat_id = category_id or student.category_id
    if not cat_id:
        return []

    points = func.coalesce(func.sum(QuizAttempt.score), 0).label("points")
    ranked = (
        db.query(User.id.label("user_id"), points)
        .join(QuizAttempt, QuizAttempt.student_id == User.id)
        .join(Quiz, Quiz.id == QuizAttempt.quiz_id)
        .filter(
            User.role == UserRole.STUDENT,
            User.category_id == cat_id,
            Quiz.category_id == cat_id,
            QuizAttempt.status == AttemptStatus.COMPLETED,
        )
        .group_by(User.id)
        .having(func.coalesce(func.sum(QuizAttempt.score), 0) > 0)
        .subquery()
    )
    rows = (
        db.query(User, ranked.c.points)
        .join(ranked, ranked.c.user_id == User.id)
        .order_by(ranked.c.points.desc(), User.name.asc(), User.id.asc())
        .all()
    )

    entries = []
    my_entry = None
    for i, (u, p) in enumerate(rows, 1):
        e = GlobalRankEntry(
            user_id=u.id,
            name=u.name or "Student",
            profile_image_url=u.profile_image_url,
            total_points=int(p or 0),
            rank=i,
            is_me=(u.id == student.id),
        )
        if e.is_me:
            my_entry = e
        if i <= 50:
            entries.append(e)
        elif my_entry is not None:
            break
    if my_entry is not None and my_entry.rank > 50:
        entries.append(my_entry)
    return entries
