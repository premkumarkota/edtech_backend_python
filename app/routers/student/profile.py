from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole
from app.dependencies import get_current_user
from app.schemas.student import StudentProfileResponse

router = APIRouter()

@router.get("/{user_id}", response_model=StudentProfileResponse)
def get_public_student_profile(
    user_id: int, 
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """
    Get profile of a student by ID.
    Requires authentication (Any valid user).
    """
    user = db.query(User).filter(User.id == user_id, User.role == UserRole.STUDENT).first()
    if not user:
        raise HTTPException(status_code=404, detail="Student not found")
    return user
