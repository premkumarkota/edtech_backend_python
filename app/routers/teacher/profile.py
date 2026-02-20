from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole
from app.dependencies import get_current_user
from app.schemas.teacher import TeacherProfileResponse

router = APIRouter()

@router.get("/{user_id}", response_model=TeacherProfileResponse)
def get_public_teacher_profile(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get profile of a teacher by ID.
    Requires authentication (Any valid user).
    """
    user = db.query(User).filter(User.id == user_id, User.role == UserRole.TEACHER).first()
    if not user:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return user
