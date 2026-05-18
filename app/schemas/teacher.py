from pydantic import BaseModel
from typing import Optional, List, Any
from datetime import datetime


# ========== REQUEST SCHEMAS ==========

class TeacherSyncRequest(BaseModel):
    """Teacher app sends Firebase ID token after OTP verification."""
    id_token: str


class TeacherOnboardingRequest(BaseModel):
    """
    Teacher fills full Practo-style profile during onboarding.
    All fields except name, document_url, category_id are optional
    so partial onboarding is allowed.
    """
    name: str
    email: Optional[str] = None
    profile_image_url: Optional[str] = None
    document_url: str
    category_id: int

    # Proposed rate (validated against platform max)
    proposed_rate_per_hour: Optional[float] = None

    # Rich profile fields
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None
    location: Optional[str] = None

    # education: [{ "degree": "B.Tech", "institution": "IIT Delhi", "year_of_passing": 2010 }]
    education: Optional[List[Any]] = None

    # experience: [{ "role": "Professor", "institution": "IIT Bombay",
    #                "from_year": 2015, "to_year": null, "is_current": true }]
    experience: Optional[List[Any]] = None

    # subjects: ["Mathematics", "Physics"]
    subjects: Optional[List[str]] = None

    # languages: ["English", "Hindi", "Tamil"]
    languages: Optional[List[str]] = None

    achievements: Optional[str] = None
    video_url: Optional[str] = None


class TeacherProfileUpdateRequest(BaseModel):
    """
    Teacher updates their profile after onboarding.
    All fields optional — partial updates supported.
    Cannot change: document_url, category_id, proposed_rate (re-onboard for those).
    """
    profile_image_url: Optional[str] = None
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None
    location: Optional[str] = None
    education: Optional[List[Any]] = None
    experience: Optional[List[Any]] = None
    subjects: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    achievements: Optional[str] = None
    video_url: Optional[str] = None  # Teacher can update demo/intro video


# ========== RESPONSE SCHEMAS ==========

class TeacherProfileResponse(BaseModel):
    """Full teacher profile — returned after sync, onboarding, profile fetch."""
    id: int
    name: Optional[str] = None
    email: Optional[str] = None
    phone_number: Optional[str] = None
    role: str
    is_active: bool
    onboarding_completed: bool
    profile_image_url: Optional[str] = None

    # Verification
    document_url: Optional[str] = None
    video_url: Optional[str] = None
    is_verified: bool = False              # Legacy field
    status: str = "pending"               # pending / approved / rejected
    rejection_reason: Optional[str] = None
    reviewed_at: Optional[datetime] = None

    # Category
    category_id: Optional[int] = None
    category_name: Optional[str] = None

    # Rate
    proposed_rate_per_hour: Optional[float] = None   # what teacher asked
    rate_per_hour: Optional[float] = None               # what admin approved

    # Rich profile
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None
    location: Optional[str] = None
    education: Optional[List[Any]] = None
    experience: Optional[List[Any]] = None
    subjects: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    achievements: Optional[str] = None

    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TeacherSyncResponse(BaseModel):
    """Response after Firebase sync"""
    access_token: str
    token_type: str = "bearer"
    user: TeacherProfileResponse
    is_new_user: bool
