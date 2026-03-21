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
from app.services.storage_service import upload_file, ALLOWED_DOCUMENT_TYPES, generate_signed_url
from fastapi import File, UploadFile, Request
import uuid


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
    from app.models.teacher_profile import TeacherProfile, TeacherStatus
    
    category = db.query(Category).filter(Category.id == request.category_id).first()
    if not category:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid Category ID"
        )
    current_user.category_id = request.category_id

    # --- ARCHITECT FIX: Create/Update Teacher Profile ---
    profile = db.query(TeacherProfile).filter(TeacherProfile.user_id == current_user.id).first()
    if not profile:
        profile = TeacherProfile(user_id=current_user.id)
        db.add(profile)
    
    profile.document_url = request.document_url
    profile.status = TeacherStatus.PENDING  # Reset to pending on onboarding/update
    
    db.commit()
    db.refresh(current_user)
    
    # Return merged data for the schema
    return {
        **current_user.__dict__,
        "status": profile.status,
        "document_url": profile.document_url,
    }


@router.post("/upload-document")
async def upload_teacher_document(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_teacher),
):
    """
    Step 1.5 of Onboarding: Teacher picks a file and uploads it.
    The backend uploads it to your GCP bucket and returns the final URL.
    """
    # This will use the GCP bucket because DEBUG=False in your .env
    file_url = await upload_file(
        file, 
        folder=f"teachers/docs/{current_user.id}",
        allowed_types=ALLOWED_DOCUMENT_TYPES
    )
    return {"document_url": file_url}


@router.get("/profile", response_model=TeacherProfileResponse)
def get_teacher_profile(
    current_user: User = Depends(get_current_teacher),
):
    """
    Get current teacher's profile.
    Pulls data from both User data and TeacherProfile tables.
    """
    # Merge basic user data with extra teacher profile info
    profile = current_user.teacher_profile
    return {
        **current_user.__dict__,
        "status": profile.status if profile else "pending",
        "document_url": profile.document_url if profile else None,
        "rejection_reason": profile.rejection_reason if profile else None,
        "category_id": profile.category_id if profile else None,
    }
