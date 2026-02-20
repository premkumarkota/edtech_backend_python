
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User, UserRole
from app.schemas.teacher import TeacherProfileResponse

router = APIRouter()

@router.get("/pending", response_model=List[TeacherProfileResponse])
def get_pending_teachers(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    Get all teachers who have onboarded but are NOT verified.
    Admin needs to review their docs.
    """
    return db.query(User).filter(
        User.role == UserRole.TEACHER,
        User.onboarding_completed == True,
        User.is_verified == False
    ).all()


@router.patch("/{teacher_id}/approve")
def approve_teacher(
    teacher_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Approve a teacher (Verify them)"""
    teacher = db.query(User).filter(User.id == teacher_id, User.role == UserRole.TEACHER).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
        
    teacher.is_verified = True
    db.commit()
    return {"message": f"Teacher {teacher.name} has been verified and will now appear on student home screens."}

@router.patch("/{teacher_id}/reject")
def reject_teacher(
    teacher_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Reject a teacher (Optional: you might want to delete or mark inactive)"""
    teacher = db.query(User).filter(User.id == teacher_id, User.role == UserRole.TEACHER).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
        
    teacher.is_active = False # Or delete
    db.commit()
    return {"message": "Teacher rejected."}
