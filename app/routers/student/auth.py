from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from app.database import get_db
from app.dependencies import get_current_student
from app.models.user import User, UserRole
from app.schemas.student import (
    StudentSyncRequest,
    StudentOnboardingRequest,
    StudentProfileResponse,
    StudentSyncResponse,
)
from app.utils.firebase import verify_firebase_token
from app.utils.security import create_access_token


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
        # Block if they're a teacher trying to use student app
        if user.role == UserRole.TEACHER:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as a Teacher. Please use the Teacher App."
            )
        # Block if they're an admin
        if user.role == UserRole.ADMIN:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This phone is registered as an Admin. Cannot use Student App."
            )
    else:
        # --- NEW USER ---
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


@router.get("/profile", response_model=StudentProfileResponse)
def get_student_profile(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_student),
):
    """
    Get current student's profile.
    Merges core User data with academic StudentProfile data.
    """
    from app.models.category import Category
    profile = current_user.student_profile
    category = db.query(Category).filter(Category.id == current_user.category_id).first() if current_user.category_id else None
    return {
        **current_user.__dict__,
        "dob": profile.dob if profile else None,
        "age": profile.age if profile else None,
        "school_college": profile.school_college if profile else None,
        "location": profile.location if profile else None,
        "category_name": category.name if category else None,
    }
