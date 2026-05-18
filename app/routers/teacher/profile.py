from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.teacher_profile import TeacherProfile
from app.schemas.teacher import TeacherProfileResponse, TeacherProfileUpdateRequest
from app.routers.teacher.auth import _build_teacher_response

router = APIRouter()


@router.patch("/update", response_model=TeacherProfileResponse)
def update_teacher_profile(
    request: TeacherProfileUpdateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_teacher),
):
    """
    Teacher updates their rich profile after onboarding.
    All fields optional — partial update, only provided fields are changed.

    CAN update:  profile_image_url, bio, experience_years, institution_name,
                 location, education[], experience[], subjects[], languages[], achievements
    CANNOT change via this: document_url, category_id, proposed_rate_per_minute
                 (re-do PATCH /auth/onboarding for those — triggers re-review)
    """
    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == current_user.id
    ).first()

    # User-level fields
    if request.profile_image_url is not None:
        current_user.profile_image_url = request.profile_image_url

    # TeacherProfile-level fields
    if profile:
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
