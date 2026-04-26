from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from sqlalchemy import text
from typing import List
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User, UserRole
from app.schemas.admin import StudentListItem, TeacherListItem, UserStatsResponse
import firebase_admin.auth as firebase_auth


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


@router.delete("/{user_id}", status_code=status.HTTP_200_OK)
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    Permanently delete a user account (student or teacher) and revoke
    their Firebase credentials.  Admins cannot delete themselves.
    Required by Google Play Store data-deletion policy.
    """
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.role == UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin accounts cannot be deleted via this endpoint"
        )
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You cannot delete your own account"
        )

    # Revoke Firebase credentials so the user cannot log in again
    if user.firebase_uid:
        try:
            firebase_auth.delete_user(user.firebase_uid)
        except Exception:
            pass  # Non-critical — proceed with DB deletion regardless

    # ── Clean up related records (correct FK dependency order) ───────────────
    try:
        # Step 1: Nullify nullable refs in sessions (must happen before session delete)
        db.execute(text("UPDATE video_call_sessions SET cancelled_by = NULL WHERE cancelled_by = :uid"), {"uid": user_id})
        db.execute(text("UPDATE video_call_sessions SET no_show_marked_by = NULL WHERE no_show_marked_by = :uid"), {"uid": user_id})

        # Step 2: Nullify other nullable refs
        db.execute(text("UPDATE teacher_rates SET set_by_admin_id = NULL WHERE set_by_admin_id = :uid"), {"uid": user_id})
        db.execute(text("UPDATE quizzes SET created_by = NULL WHERE created_by = :uid"), {"uid": user_id})
        db.execute(text("UPDATE platform_config SET updated_by = NULL WHERE updated_by = :uid"), {"uid": user_id})
        db.execute(text("UPDATE teacher_profiles SET reviewed_by = NULL WHERE reviewed_by = :uid"), {"uid": user_id})
        db.execute(text("UPDATE razorpay_payments SET student_id = NULL WHERE student_id = :uid"), {"uid": user_id})
        # Nullify subscription_id on payments linked to this student's subscriptions
        db.execute(text("""
            UPDATE razorpay_payments SET subscription_id = NULL
            WHERE subscription_id IN (
                SELECT id FROM student_subscriptions WHERE student_id = :uid
            )
        """), {"uid": user_id})

        # Step 3: Delete quiz activity
        db.execute(text("""
            DELETE FROM quiz_answers
            WHERE attempt_id IN (SELECT id FROM quiz_attempts WHERE student_id = :uid)
        """), {"uid": user_id})
        db.execute(text("DELETE FROM quiz_attempts WHERE student_id = :uid"), {"uid": user_id})

        # Step 4: Delete teacher_earnings BEFORE sessions
        # (teacher_earnings.session_id is a non-nullable FK to video_call_sessions)
        db.execute(text("DELETE FROM teacher_earnings WHERE teacher_id = :uid"), {"uid": user_id})
        db.execute(text("""
            DELETE FROM teacher_earnings
            WHERE session_id IN (
                SELECT id FROM video_call_sessions
                WHERE student_id = :uid OR teacher_id = :uid
            )
        """), {"uid": user_id})

        # Step 5: Delete teacher_payouts
        db.execute(text("DELETE FROM teacher_payouts WHERE teacher_id = :uid"), {"uid": user_id})

        # Step 6: Delete instant session requests
        db.execute(text("DELETE FROM instant_session_requests WHERE student_id = :uid OR teacher_id = :uid"), {"uid": user_id})

        # Step 7: Delete sessions (AFTER earnings)
        db.execute(text("DELETE FROM video_call_sessions WHERE student_id = :uid OR teacher_id = :uid"), {"uid": user_id})

        # Step 8: Delete teacher availability
        db.execute(text("DELETE FROM teacher_availability WHERE teacher_id = :uid"), {"uid": user_id})
        db.execute(text("DELETE FROM teacher_availability_overrides WHERE teacher_id = :uid"), {"uid": user_id})

        # Step 9: Delete the user (CASCADE handles teacher_profile, student_profile,
        #         teacher_rates, student_subscriptions)
        db.delete(user)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete user: {str(e)}"
        )
    return {"message": f"User {user_id} permanently deleted"}


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
