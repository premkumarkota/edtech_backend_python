"""
Student learning streaks — GET /api/student/streak

One payload for the home card, the Quizzes chip, and the full streak screen
(see app/services/streak_service.py for the rules).
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_student
from app.models.user import User
from app.services.streak_service import streak_overview

router = APIRouter()


@router.get("")
def get_my_streak(student: User = Depends(require_student), db: Session = Depends(get_db)):
    return streak_overview(student, db)
