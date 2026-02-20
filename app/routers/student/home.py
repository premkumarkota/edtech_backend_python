
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.models.category import Category
from app.schemas.teacher import TeacherProfileResponse

router = APIRouter()

@router.get("/teachers", response_model=List[TeacherProfileResponse])
def get_home_feed(
    db: Session = Depends(get_db),
    student: User = Depends(get_current_student)
):
    """
    Get relevant teachers for the student's home screen.
    Logic: Find Verified Teachers in the same Category as the Student.
    """
    if not student.category_id:
        # If student hasn't selected a category (edge case), return empty or all
        return []

    teachers = db.query(User).filter(
        User.role == UserRole.TEACHER,
        User.is_active == True,
        User.is_verified == True,  # Only verified teachers
        User.category_id == student.category_id  # CATEGORY MATCHING
    ).all()
    
    return teachers
