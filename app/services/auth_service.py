from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.user import User, UserRole
from app.utils.security import hash_password, verify_password, create_access_token
from app.utils.firebase import verify_firebase_token


def authenticate_admin(email: str, password: str, db: Session) -> dict:
    """
    Authenticate admin with email + password.
    Returns dict with access_token and user object.
    """
    user = db.query(User).filter(User.email == email).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied. Admin only."
        )

    if not verify_password(password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password"
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled"
        )

    access_token = create_access_token(
        data={"sub": str(user.id), "email": user.email, "role": user.role.value}
    )

    return {"access_token": access_token, "user": user}


def create_first_admin(name: str, email: str, password: str, db: Session) -> str:
    """
    Create the first admin account. Only works if no admin exists yet.
    Returns success message.
    """
    existing_admin = db.query(User).filter(User.role == UserRole.ADMIN).first()
    if existing_admin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Admin already exists. Contact existing admin."
        )

    existing_email = db.query(User).filter(User.email == email).first()
    if existing_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    new_admin = User(
        name=name,
        email=email,
        hashed_password=hash_password(password),
        role=UserRole.ADMIN,
        is_active=True,
        onboarding_completed=True,
    )

    db.add(new_admin)
    db.commit()

    return f"Admin '{name}' created successfully!"


def sync_firebase_user(id_token: str, role: UserRole, db: Session) -> dict:
    """
    Verify Firebase ID token, create or find user with specified role.
    Returns dict with access_token, user, and is_new_user flag.

    Used by both Student and Teacher sync endpoints.
    """
    decoded_token = verify_firebase_token(id_token)
    if not decoded_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired Firebase token"
        )

    firebase_uid = decoded_token.get("uid")
    phone_number = decoded_token.get("phone_number")

    if not firebase_uid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Firebase token missing UID"
        )

    # --- Find existing user ---

    # 1. Try by Firebase UID
    user = db.query(User).filter(User.firebase_uid == firebase_uid).first()

    # 2. If not found, try by phone number (handles re-install / new Firebase UID)
    if not user and phone_number:
        user = db.query(User).filter(User.phone_number == phone_number).first()
        if user:
            user.firebase_uid = firebase_uid
            db.commit()
            db.refresh(user)

    is_new_user = False

    if user:
        # --- EXISTING USER: role conflict check ---
        if role == UserRole.STUDENT and user.role == UserRole.TEACHER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as a Teacher. Please use the Teacher App."
            )
        if role == UserRole.TEACHER and user.role == UserRole.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as a Student. Please use the Student App."
            )
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"This phone is registered as an Admin. Cannot use {role.value.title()} App."
            )
    else:
        # --- NEW USER ---
        user = User(
            firebase_uid=firebase_uid,
            phone_number=phone_number,
            role=role,
            onboarding_completed=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True

    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value}
    )

    return {"access_token": access_token, "user": user, "is_new_user": is_new_user}
