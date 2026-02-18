from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User, UserRole
from app.schemas.admin import StudentListItem, TeacherListItem, UserStatsResponse


router = APIRouter()


@router.get("/students", response_model=List[StudentListItem])
def list_students(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """List all students (for admin portal dashboard)"""
    students = db.query(User).filter(
        User.role == UserRole.STUDENT
    ).order_by(User.created_at.desc()).all()
    return students


@router.get("/teachers", response_model=List[TeacherListItem])
def list_teachers(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """List all teachers (for admin portal dashboard)"""
    teachers = db.query(User).filter(
        User.role == UserRole.TEACHER
    ).order_by(User.created_at.desc()).all()
    return teachers


@router.get("/stats", response_model=UserStatsResponse)
def get_user_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Get summary counts for admin dashboard"""
    total_students = db.query(User).filter(User.role == UserRole.STUDENT).count()
    total_teachers = db.query(User).filter(User.role == UserRole.TEACHER).count()
    active_students = db.query(User).filter(
        User.role == UserRole.STUDENT, User.is_active == True
    ).count()
    active_teachers = db.query(User).filter(
        User.role == UserRole.TEACHER, User.is_active == True
    ).count()
    onboarded_students = db.query(User).filter(
        User.role == UserRole.STUDENT, User.onboarding_completed == True
    ).count()
    onboarded_teachers = db.query(User).filter(
        User.role == UserRole.TEACHER, User.onboarding_completed == True
    ).count()

    return UserStatsResponse(
        total_students=total_students,
        total_teachers=total_teachers,
        active_students=active_students,
        active_teachers=active_teachers,
        onboarded_students=onboarded_students,
        onboarded_teachers=onboarded_teachers,
    )


@router.patch("/students/{user_id}/toggle-active")
def toggle_student_active(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Enable/disable a student account"""
    user = db.query(User).filter(User.id == user_id, User.role == UserRole.STUDENT).first()
    if not user:
        raise HTTPException(status_code=404, detail="Student not found")

    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return {"message": f"Student {'activated' if user.is_active else 'deactivated'}", "is_active": user.is_active}


@router.patch("/teachers/{user_id}/toggle-active")
def toggle_teacher_active(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Enable/disable a teacher account"""
    user = db.query(User).filter(User.id == user_id, User.role == UserRole.TEACHER).first()
    if not user:
        raise HTTPException(status_code=404, detail="Teacher not found")

    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)
    return {"message": f"Teacher {'activated' if user.is_active else 'deactivated'}", "is_active": user.is_active}
