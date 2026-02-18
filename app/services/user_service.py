from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.user import User, UserRole


def get_user_stats(db: Session) -> dict:
    """Get summary counts for admin dashboard."""
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

    return {
        "total_students": total_students,
        "total_teachers": total_teachers,
        "active_students": active_students,
        "active_teachers": active_teachers,
        "onboarded_students": onboarded_students,
        "onboarded_teachers": onboarded_teachers,
    }


def list_users_by_role(role: UserRole, db: Session) -> list:
    """List all users with a specific role, ordered by newest first."""
    return db.query(User).filter(
        User.role == role
    ).order_by(User.created_at.desc()).all()


def toggle_user_active(user_id: int, role: UserRole, db: Session) -> dict:
    """Toggle a user's is_active status. Returns message and new status."""
    role_name = role.value.title()

    user = db.query(User).filter(User.id == user_id, User.role == role).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{role_name} not found"
        )

    user.is_active = not user.is_active
    db.commit()
    db.refresh(user)

    status_text = "activated" if user.is_active else "deactivated"
    return {"message": f"{role_name} {status_text}", "is_active": user.is_active}


def complete_onboarding(
    current_user: User,
    name: str,
    email: str | None,
    profile_image_url: str | None,
    db: Session,
) -> User:
    """
    Complete user onboarding by setting name and optional fields.
    Works for both students and teachers.
    """
    current_user.name = name
    current_user.onboarding_completed = True

    if email:
        existing = db.query(User).filter(
            User.email == email, User.id != current_user.id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        current_user.email = email

    if profile_image_url:
        current_user.profile_image_url = profile_image_url

    db.commit()
    db.refresh(current_user)
    return current_user
