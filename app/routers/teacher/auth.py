from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User, UserRole
from app.schemas.teacher import (
    TeacherSyncRequest,
    TeacherOnboardingRequest,
    TeacherProfileResponse,
    TeacherSyncResponse,
)
from app.utils.firebase import verify_firebase_token
from app.utils.security import create_access_token


router = APIRouter()


@router.post("/sync", response_model=TeacherSyncResponse)
def sync_teacher(request: TeacherSyncRequest, db: Session = Depends(get_db)):
    """
    Teacher Firebase OTP Sync.

    Flow:
    1. Teacher app verifies phone via Firebase OTP
    2. App sends the Firebase ID token here
    3. Backend verifies token, extracts uid & phone
    4. Creates new user (role=TEACHER) or finds existing
    5. Returns local JWT for subsequent API calls

    IMPORTANT: If this phone is already registered as STUDENT, returns 403.
    """
    # Verify Firebase token
    decoded_token = verify_firebase_token(request.id_token)
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
            # Same phone, new UID — update it
            user.firebase_uid = firebase_uid
            db.commit()
            db.refresh(user)

    is_new_user = False

    if user:
        # --- EXISTING USER ---
        # Block if they're a student trying to use teacher app
        if user.role == UserRole.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as a Student. Please use the Student App."
            )
        # Block if they're an admin
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as an Admin. Cannot use Teacher App."
            )
    else:
        # --- NEW USER ---
        user = User(
            firebase_uid=firebase_uid,
            phone_number=phone_number,
            role=UserRole.TEACHER,
            onboarding_completed=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        is_new_user = True

    # Create local JWT
    access_token = create_access_token(
        data={"sub": str(user.id), "role": user.role.value}
    )

    return TeacherSyncResponse(
        access_token=access_token,
        user=TeacherProfileResponse.model_validate(user),
        is_new_user=is_new_user,
    )


@router.patch("/onboarding", response_model=TeacherProfileResponse)
def complete_teacher_onboarding(
    request: TeacherOnboardingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher),
):
    """
    Teacher completes onboarding by providing name and optional details.
    Called after first sync when is_new_user=true.
    """
    current_user.name = request.name
    current_user.onboarding_completed = True

    if request.email:
        # Check email not already taken
        existing = db.query(User).filter(
            User.email == request.email, User.id != current_user.id
        ).first()
        if existing:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email already in use"
            )
        current_user.email = request.email

    if request.profile_image_url:
        current_user.profile_image_url = request.profile_image_url

    # Validate Category
    from app.models.category import Category
    category = db.query(Category).filter(Category.id == request.category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Category ID"
        )
    current_user.category_id = request.category_id

    current_user.document_url = request.document_url

    db.commit()
    db.refresh(current_user)
    return current_user


@router.get("/profile", response_model=TeacherProfileResponse)
def get_teacher_profile(
    current_user: User = Depends(get_current_teacher),
):
    """Get current teacher's profile"""
    return current_user
