from fastapi import APIRouter, Depends, HTTPException, Body
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.models.quiz import Quiz, QuizQuestion, QuizAttempt, QuizAnswer, QuizStatus, AttemptStatus
from app.schemas.quiz import (
    QuizResponse, QuizQuestionResponse, QuizAttemptResponse,
    QuizSubmit, QuizResultResponse, LeaderboardEntry
)

router = APIRouter()

@router.get("", response_model=List[QuizResponse])
def list_quizzes(
    syllabus_id: int = None,
    chapter_id: int = None,
    student: User = Depends(require_student), 
    db: Session = Depends(get_db)
):
    """List all published quizzes for the student's category with optional Subject/Chapter filters."""
    if not student.category_id:
        raise HTTPException(status_code=400, detail="Complete onboarding first.")

    query = db.query(Quiz).filter(
        Quiz.category_id == student.category_id, 
        Quiz.status == QuizStatus.PUBLISHED
    )
    
    if syllabus_id:
        query = query.filter(Quiz.syllabus_id == syllabus_id)
    if chapter_id:
        query = query.filter(Quiz.chapter_id == chapter_id)

    quizzes = query.order_by(Quiz.created_at.desc()).all()

    results = []
    for quiz in quizzes:
        r = QuizResponse.from_orm(quiz)
        r.question_count = len(quiz.questions)
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
        Quiz.category_id == student.category_id,
        Quiz.status == QuizStatus.PUBLISHED
    ).first()
    if not quiz:
        raise HTTPException(status_code=404, detail="Quiz not found")

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

@router.post("/{quiz_id}/start", response_model=QuizAttemptResponse)
def start_quiz(quiz_id: int, student: User = Depends(require_student), db: Session = Depends(get_db)):
    """Initialize a quiz attempt. Used for resuming or creating new attempts."""
    quiz = db.query(Quiz).filter(Quiz.id == quiz_id, Quiz.status == QuizStatus.PUBLISHED).first()
    if not quiz: raise HTTPException(404)

    # Resume existing if 'STARTED'
    existing = db.query(QuizAttempt).filter(
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.student_id == student.id,
        QuizAttempt.status == AttemptStatus.IN_PROGRESS
    ).first()
    if existing: return existing

    attempt = QuizAttempt(
        quiz_id=quiz_id,
        student_id=student.id,
        status=AttemptStatus.IN_PROGRESS,
        total_marks=quiz.total_marks
    )
    db.add(attempt)
    db.commit()
    db.refresh(attempt)
    return attempt

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
    questions = {q.id: q for q in quiz.questions}
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
    attempt.passed = score >= quiz.pass_marks
    attempt.status = AttemptStatus.COMPLETED
    attempt.completed_at = datetime.now(timezone.utc)
    attempt.time_taken_secs = time_taken_secs

    db.commit()
    
    # Update total points in profile
    if student.student_profile:
        student.student_profile.total_points += score
        db.commit()
        
    return {"score": score, "total": total, "passed": attempt.passed}

@router.get("/attempt/{attempt_id}/result")
def get_result(attempt_id: int, student: User = Depends(require_student), db: Session = Depends(get_db)):
    """Full feedback including correct options and explanations."""
    attempt = db.query(QuizAttempt).filter(QuizAttempt.id == attempt_id, QuizAttempt.student_id == student.id).first()
    if not attempt or attempt.status != AttemptStatus.COMPLETED:
        raise HTTPException(404)

    breakdown = []
    for q in attempt.quiz.questions:
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

    results = db.query(User, subq.c.score, subq.c.time_taken_secs).join(subq, User.id == subq.c.student_id).filter(subq.c.rank == 1).order_by(subq.c.score.desc(), subq.c.time_taken_secs.asc()).limit(50).all()

    return [LeaderboardEntry(
        user_id=u.id, name=u.name or "Student", profile_image_url=u.profile_image_url,
        score=s, time_taken_secs=t, rank=i, percentage=(s/50*100) # Mock percentage calculation
    ) for i, (u, s, t) in enumerate(results, 1)]


@router.get("/leaderboard/global", response_model=List[GlobalRankEntry])
def get_global_rankings(db: Session = Depends(get_db), student: User = Depends(require_student)):
    """Global top 50 ranking based on total points."""
    from app.models.student_profile import StudentProfile
    
    # Efficiently fetch top 50 students with points > 0
    rankings = db.query(User, StudentProfile.total_points)\
        .join(StudentProfile, User.id == StudentProfile.user_id)\
        .filter(StudentProfile.total_points > 0)\
        .order_by(StudentProfile.total_points.desc())\
        .limit(50)\
        .all()

    return [GlobalRankEntry(
        user_id=u.id, 
        name=u.name or "Student", 
        profile_image_url=u.profile_image_url,
        total_points=p, 
        rank=i
    ) for i, (u, p) in enumerate(rankings, 1)]
