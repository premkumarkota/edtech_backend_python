from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.schemas.common import MessageResponse
from app.schemas.student import (
    StudentSyncRequest,
    StudentOnboardingRequest,
    StudentCategoryUpdateRequest,
    StudentProfileResponse,
    StudentSyncResponse,
)
from app.utils.firebase import verify_firebase_token
from app.utils.security import create_access_token
from app.services.storage_service import (
    upload_file,
    ALLOWED_IMAGE_TYPES,
    MAX_PROFILE_AVATAR_MB,
)


router = APIRouter()


@router.post("/sync", response_model=StudentSyncResponse)
def sync_student(request: StudentSyncRequest, db: Session = Depends(get_db)):
    """
    Student Firebase OTP Sync.

    Flow:
    1. Student app verifies phone via Firebase OTP
    2. App sends the Firebase ID token here
    3. Backend verifies token, extracts uid & phone
    4. Creates new user (role=STUDENT) or finds existing
    5. Returns local JWT for subsequent API calls

    IMPORTANT: If this phone is already registered as TEACHER, returns 403.
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

    # Find existing STUDENT row for this Firebase identity.
    # Same phone can also exist as a TEACHER row — that is intentional and allowed.
    user = db.query(User).filter(
        User.firebase_uid == firebase_uid,
        User.role == UserRole.STUDENT,
    ).first()

    # Re-install / new Firebase UID — match by phone + role
    if not user and phone_number:
        user = db.query(User).filter(
            User.phone_number == phone_number,
            User.role == UserRole.STUDENT,
        ).first()
        if user:
            user.firebase_uid = firebase_uid
            db.commit()
            db.refresh(user)

    is_new_user = False

    if not user:
        # --- NEW STUDENT ---
        user = User(
            firebase_uid=firebase_uid,
            phone_number=phone_number,
            role=UserRole.STUDENT,
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

    return StudentSyncResponse(
        access_token=access_token,
        user=StudentProfileResponse.model_validate(user),
        is_new_user=is_new_user,
    )


@router.patch("/onboarding", response_model=StudentProfileResponse)
def complete_student_onboarding(
    request: StudentOnboardingRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Student completes onboarding by providing name and optional details.
    Called after first sync when is_new_user=true.
    """
    current_user.name = request.name
    current_user.onboarding_completed = True

    if request.profile_image_url:
        current_user.profile_image_url = request.profile_image_url

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

    # Validate Category
    from app.models.category import Category
    from app.models.student_profile import StudentProfile
    
    category = db.query(Category).filter(Category.id == request.category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Category ID"
        )
    current_user.category_id = request.category_id

    # --- ARCHITECT FIX: Create/Update Student Profile ---
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == current_user.id).first()
    if not profile:
        profile = StudentProfile(user_id=current_user.id)
        db.add(profile)
    
    profile.dob = request.dob
    profile.age = request.age
    profile.school_college = request.school_college
    profile.location = request.location

    db.commit()
    db.refresh(current_user)

    category_name = category.name if category else None

    # Return merged data for the schema
    return {
        **current_user.__dict__,
        "dob": profile.dob,
        "age": profile.age,
        "school_college": profile.school_college,
        "location": profile.location,
        "category_name": category_name,
    }


def _student_profile_dict(user: User, db: Session) -> dict:
    """Merged User + StudentProfile + category name (same shape as GET /profile)."""
    from app.models.category import Category

    profile = user.student_profile
    category = (
        db.query(Category).filter(Category.id == user.category_id).first()
        if user.category_id
        else None
    )
    return {
        **user.__dict__,
        "dob": profile.dob if profile else None,
        "age": profile.age if profile else None,
        "school_college": profile.school_college if profile else None,
        "location": profile.location if profile else None,
        "category_name": category.name if category else None,
    }


@router.get("/profile", response_model=StudentProfileResponse)
def get_student_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Get current student's profile.
    Merges core User data with academic StudentProfile data.
    """
    return _student_profile_dict(current_user, db)


@router.patch("/category", response_model=StudentProfileResponse)
def update_student_category(
    request: StudentCategoryUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Update the student's learning category (home board switcher).
    Study Planner exams/goals then follow this category.
    """
    from app.models.category import Category

    category = db.query(Category).filter(
        Category.id == request.category_id,
        Category.is_active == True,
    ).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid or inactive category",
        )

    current_user.category_id = request.category_id
    db.commit()
    db.refresh(current_user)
    return _student_profile_dict(current_user, db)


@router.post("/upload-profile-photo", response_model=StudentProfileResponse)
async def upload_student_profile_photo(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Multipart upload for profile picture (JPEG / PNG / WebP, max 5 MB).
    Saves to object storage, sets users.profile_image_url, returns full profile.
    """
    url = await upload_file(
        file,
        folder=f"students/avatars/{current_user.id}",
        allowed_types=ALLOWED_IMAGE_TYPES,
        max_size_mb=MAX_PROFILE_AVATAR_MB,
    )
    current_user.profile_image_url = url
    db.commit()
    db.refresh(current_user)
    return _student_profile_dict(current_user, db)


@router.post("/logout", response_model=MessageResponse)
def logout_student(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Best-effort backend logout for student app.
    Clears server-side push token so the logged-out device stops receiving
    student push notifications. JWT remains stateless and is removed client-side.
    """
    current_user.fcm_token = None
    db.commit()
    return {"message": "Logged out successfully", "success": True}
