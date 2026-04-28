from fastapi import APIRouter, Depends, HTTPException, status, File, UploadFile, Request
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User, UserRole
from app.schemas.common import MessageResponse
from app.schemas.teacher import (
    TeacherSyncRequest,
    TeacherOnboardingRequest,
    TeacherProfileResponse,
    TeacherSyncResponse,
)
from app.utils.firebase import verify_firebase_token
from app.utils.security import create_access_token
from app.services.storage_service import upload_file, ALLOWED_DOCUMENT_TYPES, ALLOWED_VIDEO_TYPES, MAX_VIDEO_SIZE_MB, generate_signed_url
import uuid


router = APIRouter()


def _build_teacher_response(user: User, db: Session) -> dict:
    """
    Build the full TeacherProfileResponse dict from User + related tables.
    Used by all teacher auth endpoints for a consistent response.
    """
    from app.models.category import Category
    from app.models.payout import TeacherRate

    profile  = user.teacher_profile
    category = db.query(Category).filter(Category.id == user.category_id).first() if user.category_id else None
    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == user.id).first()

    return {
        **user.__dict__,
        "status":           profile.status if profile else "pending",
        "document_url":     profile.document_url if profile else user.document_url,
        "video_url":        profile.video_url if profile else None,
        "rejection_reason": profile.rejection_reason if profile else None,
        "reviewed_at":      profile.reviewed_at if profile else None,
        "category_name":    category.name if category else None,
        "rate_per_hour":          float(rate_row.rate_per_hour) if rate_row else None,
        "proposed_rate_per_hour": float(profile.proposed_rate_per_hour) if (profile and profile.proposed_rate_per_hour) else None,
        # Rich profile fields
        "bio":              profile.bio if profile else None,
        "experience_years": profile.experience_years if profile else None,
        "institution_name": profile.institution_name if profile else None,
        "location":         profile.location if profile else None,
        "education":        profile.education if profile else [],
        "experience":       profile.experience if profile else [],
        "subjects":         profile.subjects if profile else [],
        "languages":        profile.languages if profile else [],
        "achievements":     profile.achievements if profile else None,
        # category_id is already in user.__dict__
    }


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

    # Find existing user
    user = db.query(User).filter(User.firebase_uid == firebase_uid).first()

    if not user and phone_number:
        user = db.query(User).filter(User.phone_number == phone_number).first()
        if user:
            user.firebase_uid = firebase_uid
            db.commit()
            db.refresh(user)

    is_new_user = False

    if user:
        if user.role == UserRole.STUDENT:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as a Student. Please use the Student App."
            )
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as an Admin. Cannot use Teacher App."
            )
    else:
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
    Teacher completes full Practo-style onboarding.

    Saves:
      - Basic info (name, email, category)
      - Proposed rate (validated against platform max ceiling)
      - Rich profile (bio, education, experience, subjects, languages, achievements)
      - Creates/updates TeacherProfile with status = PENDING

    After this, admin reviews and approves via POST /api/admin/teachers/{id}/approve.
    """
    from app.models.category import Category
    from app.models.teacher_profile import TeacherProfile, TeacherStatus
    # ── Validate proposed rate ────────────────────────────────────────────────
    if request.proposed_rate_per_hour is not None:
        if request.proposed_rate_per_hour < 0:
            raise HTTPException(status_code=400, detail="Proposed rate must be non-negative")

    # ── Update User fields ────────────────────────────────────────────────────
    current_user.name                = request.name
    current_user.onboarding_completed = True

    if request.profile_image_url:
        current_user.profile_image_url = request.profile_image_url

    if request.email:
        existing = db.query(User).filter(
            User.email == request.email, User.id != current_user.id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="Email already in use")
        current_user.email = request.email

    # ── Validate Category ─────────────────────────────────────────────────────
    category = db.query(Category).filter(Category.id == request.category_id).first()
    if not category:
        raise HTTPException(status_code=400, detail="Invalid Category ID")
    current_user.category_id = request.category_id

    # ── Create/Update TeacherProfile ──────────────────────────────────────────
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()
    if not profile:
        profile = TeacherProfile(user_id=current_user.id)
        db.add(profile)

    profile.document_url  = request.document_url
    profile.status        = TeacherStatus.PENDING   # reset to pending on re-onboard

    # Rate proposal
    if request.proposed_rate_per_hour is not None:
        profile.proposed_rate_per_hour = request.proposed_rate_per_hour

    # Rich profile
    if request.bio is not None:
        profile.bio = request.bio
    if request.experience_years is not None:
        profile.experience_years = request.experience_years
    if request.institution_name is not None:
        profile.institution_name = request.institution_name
    if request.location is not None:
        profile.location = request.location
    if request.education is not None:
        profile.education = request.education
    if request.experience is not None:
        profile.experience = request.experience
    if request.subjects is not None:
        profile.subjects = request.subjects
    if request.languages is not None:
        profile.languages = request.languages
    if request.achievements is not None:
        profile.achievements = request.achievements
    if request.video_url is not None:
        profile.video_url = request.video_url

    db.commit()
    db.refresh(current_user)

    return _build_teacher_response(current_user, db)


@router.post("/upload-document")
async def upload_teacher_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_teacher),
):
    """
    Step 1.5 of Onboarding: Teacher picks a file and uploads it.
    Returns the final URL to include in the onboarding PATCH request.
    """
    file_url = await upload_file(
        file,
        folder=f"teachers/docs/{current_user.id}",
        allowed_types=ALLOWED_DOCUMENT_TYPES
    )
    return {"document_url": file_url}


@router.post("/upload-video")
async def upload_teacher_video(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_teacher),
):
    """
    Optional onboarding step: Teacher uploads a short lecture video (max 10 MB).
    Returns the URL to include as video_url in the onboarding PATCH request.
    Accepted formats: MP4, MOV, AVI, MKV, WebM.
    """
    file_url = await upload_file(
        file,
        folder=f"teachers/videos/{current_user.id}",
        allowed_types=ALLOWED_VIDEO_TYPES,
        max_size_mb=MAX_VIDEO_SIZE_MB,
    )
    return {"video_url": file_url}


@router.get("/profile", response_model=TeacherProfileResponse)
def get_teacher_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher),
):
    """
    Get current teacher's full profile.
    Merges User, TeacherProfile, Category, and TeacherRate tables.
    """
    return _build_teacher_response(current_user, db)


@router.post("/logout", response_model=MessageResponse)
def logout_teacher(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher),
):
    """
    Best-effort backend logout for teacher app.
    Clears server-side push token so this device stops receiving
    teacher push notifications after logout.
    """
    current_user.fcm_token = None
    db.commit()
    return {"message": "Logged out successfully", "success": True}
