from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from app.database import get_db
from app.dependencies import get_onboarded_student
from app.models.user import User, UserRole
from app.models.category import Category
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.schemas.student import HomeFeedResponse, TeacherCardResponse

router = APIRouter()

@router.get("/teachers", response_model=HomeFeedResponse)
def get_home_feed(
    db: Session = Depends(get_db),
    student: User = Depends(get_onboarded_student)
):
    """
    Industry-Level Student Home Feed API.
    
    1. JOINS Users and TeacherProfiles to show rich metadata (Bio, Experience).
    2. FILTERS by matching Category ID.
    3. FILTERS for 'Approved' status only (not just verified flag).
    4. SORTS by newest teachers first.
    """
    if not student.category_id:
        return HomeFeedResponse(
            student_category_id=None,
            teachers=[],
            total_count=0
        )

    # Perform Join with JoinedLoad to prevent N+1 queries (FETCH EVERYTHING AT ONCE)
    query = db.query(User).options(
        joinedload(User.teacher_profile)
    ).join(
        TeacherProfile, User.id == TeacherProfile.user_id
    ).filter(
        User.role == UserRole.TEACHER,
        User.is_active == True,
        User.category_id == student.category_id,
        TeacherProfile.status == TeacherStatus.APPROVED
    ).order_by(User.created_at.desc())

    teachers = query.all()
    
    # Map the mixed query results to our Pydantic cards
    teacher_cards = []
    for t in teachers:
        # Pydantic will automagically grab from t.teacher_profile.* if attributes match
        # We ensure properties like bio/experience exist via the relation
        card = TeacherCardResponse(
            id=t.id,
            name=t.name,
            profile_image_url=t.profile_image_url,
            category_id=t.category_id,
            bio=t.teacher_profile.bio if t.teacher_profile else None,
            experience_years=t.teacher_profile.experience_years if t.teacher_profile else None,
            institution_name=t.teacher_profile.institution_name if t.teacher_profile else None
        )
        teacher_cards.append(card)

    return HomeFeedResponse(
        student_category_id=student.category_id,
        teachers=teacher_cards,
        total_count=len(teacher_cards)
    )
