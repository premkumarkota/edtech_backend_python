from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.user import User, UserRole
from app.schemas.admin import AdminLoginRequest, AdminCreateRequest, AdminTokenResponse, AdminUserResponse, MessageResponse
from app.utils.security import hash_password, verify_password, create_access_token


router = APIRouter()


@router.post("/login", response_model=AdminTokenResponse)
def admin_login(credentials: AdminLoginRequest, db: Session = Depends(get_db)):
    """
    Admin login with email + password.
    Returns JWT token on success.
    """
    # Find user by email
    user = db.query(User).filter(User.email == credentials.email).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # Must be an admin
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin only."
        )

    # Verify password
    if not verify_password(credentials.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    # Check if active
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    # Create JWT
    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role.value}
    )

    return AdminTokenResponse(
        access_token=access_token,
        user=AdminUserResponse.model_validate(user)
    )


@router.post("/create-admin", response_model=MessageResponse)
def create_first_admin(admin_data: AdminCreateRequest, db: Session = Depends(get_db)):
    """
    Create first admin account.
    Only works if no admin exists yet (one-time setup).
    """
    # Check if any admin already exists
    existing_admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
    if existing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin already exists. Contact existing admin."
        )

    # Check if email already used
    existing_email = db.query(User).filter(User.email == admin_data.email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # Create admin
    new_admin = User(
        name=admin_data.name,
        email=admin_data.email,
        hashed_password=hash_password(admin_data.password),
        role=UserRole.ADMIN,
        is_active=True,
        onboarding_completed=True,
    )

    db.add(new_admin)
    db.commit()

    return MessageResponse(message=f"Admin '{admin_data.name}' created successfully!")
