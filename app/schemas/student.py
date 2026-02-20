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
