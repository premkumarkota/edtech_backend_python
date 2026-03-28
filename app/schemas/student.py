from pydantic import BaseModel
from typing import Optional
from datetime import datetime


# ========== REQUEST SCHEMAS ==========

class StudentSyncRequest(BaseModel):
    """
    Student app sends Firebase ID token after OTP verification.
    Backend verifies token, creates/finds user, returns local JWT.
    """
    id_token: str


class StudentOnboardingRequest(BaseModel):
    """Student fills in profile during onboarding"""
    name: str
    email: Optional[str] = None
    profile_image_url: Optional[str] = None
    dob: str
    age: int
    school_college: str
    location: str
    category_id: int


# ========== RESPONSE SCHEMAS ==========

class StudentProfileResponse(BaseModel):
    """Student profile returned after sync/onboarding"""
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    onboarding_completed: bool
    profile_image_url: Optional[str] = None

    # Specific fields
    category_id: Optional[int] = None
    category_name: Optional[str] = None   # human-readable category name
    dob: Optional[str] = None
    age: Optional[int] = None
    school_college: Optional[str] = None
    location: Optional[str] = None

    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class StudentSyncResponse(BaseModel):
    """Response after Firebase sync"""
    access_token: str
    token_type: str = "bearer"
    user: StudentProfileResponse
    is_new_user: bool


# ========== HOME FEED & DISCOVERY ==========

class TeacherCardResponse(BaseModel):
    """Simplified teacher card for student home feed (list view)"""
    id: int
    name: Optional[str] = None
    profile_image_url: Optional[str] = None
    category_id: Optional[int] = None
    location: Optional[str] = None

    # Summary fields from teacher_profile
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None

    # NEW: shown on card so student knows cost before tapping
    rate_per_minute: Optional[float] = None
    subjects: Optional[list] = None
    languages: Optional[list] = None

    class Config:
        from_attributes = True


class TeacherDetailResponse(BaseModel):
    """
    Full teacher profile page (Practo doctor-detail equivalent).
    Shown when student taps a teacher card.
    GET /api/student/home/teachers/{teacher_id}
    """
    id: int
    name: Optional[str] = None
    profile_image_url: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    location: Optional[str] = None
    rate_per_minute: Optional[float] = None

    # Full rich profile
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None
    education: Optional[list] = None
    experience: Optional[list] = None
    subjects: Optional[list] = None
    languages: Optional[list] = None
    achievements: Optional[str] = None

    class Config:
        from_attributes = True


class HomeFeedResponse(BaseModel):
    """Industry-standard home feed response with metadata"""
    status: str = "success"
    student_category_id: Optional[int] = None
    teachers: list[TeacherCardResponse]
    total_count: int
