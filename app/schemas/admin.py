from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime


# ========== REQUEST SCHEMAS ==========

class AdminLoginRequest(BaseModel):
    """Admin login with email + password"""
    email: EmailStr
    password: str


class AdminCreateRequest(BaseModel):
    """Create first admin account"""
    name: str
    email: EmailStr
    password: str


class TeacherApprovalRequest(BaseModel):
    """
    Admin approves or rejects a pending teacher.
    Rate resolution (priority order):
      1. approved_rate_per_hour  — admin sets the hourly rate
      2. teacher's proposed_rate_per_hour
    No platform ceiling — admin has full control.
    """
    approved: bool
    approved_rate_per_hour: Optional[float] = None
    rejection_reason: Optional[str] = None


class PlatformConfigUpdateRequest(BaseModel):
    """Update a platform config value (admin only)"""
    value: str


# ========== RESPONSE SCHEMAS ==========

class AdminUserResponse(BaseModel):
    """Admin user info returned after login"""
    id: int
    name: str
    email: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class AdminTokenResponse(BaseModel):
    """Login response with JWT token"""
    access_token: str
    token_type: str = "bearer"
    user: AdminUserResponse


class MessageResponse(BaseModel):
    """Simple message response"""
    message: str
    success: bool = True


# ========== ADMIN PORTAL: USER LISTING ==========

class StudentListItem(BaseModel):
    """Student info shown in admin portal"""
    id: int
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    is_active: bool
    onboarding_completed: bool
    profile_image_url: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TeacherListItem(BaseModel):
    """Teacher info shown in admin portal"""
    id: int
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    is_active: bool
    onboarding_completed: bool
    profile_image_url: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class UserStatsResponse(BaseModel):
    """Summary counts for admin dashboard"""
    total_students: int
    total_teachers: int
    active_students: int
    active_teachers: int
    onboarded_students: int
    onboarded_teachers: int


# ========== TEACHER VERIFICATION / DETAIL ==========

class TeacherVerificationResponse(BaseModel):
    """
    Teacher shown in admin pending-approvals list.
    Full rich profile so admin can make an informed decision before approving.
    """
    id: int
    name: Optional[str] = None
    phone_number: Optional[str] = None
    email: Optional[str] = None
    profile_image_url: Optional[str] = None
    category_id: Optional[int] = None
    category_name: Optional[str] = None
    created_at: Optional[datetime] = None

    # From teacher_profile
    status: str = "pending"
    document_url: Optional[str] = None
    bio: Optional[str] = None
    experience_years: Optional[int] = None
    institution_name: Optional[str] = None
    location: Optional[str] = None
    education: Optional[List[Any]] = None
    experience: Optional[List[Any]] = None
    subjects: Optional[List[str]] = None
    languages: Optional[List[str]] = None
    achievements: Optional[str] = None
    rejection_reason: Optional[str] = None

    # Rate negotiation
    proposed_rate_per_hour: Optional[float] = None     # what the teacher asked for
    current_rate_per_hour: Optional[float] = None      # currently approved rate (null = not set yet)

    class Config:
        from_attributes = True


# Backward-compatible name used by the existing pending endpoint.
TeacherPendingResponse = TeacherVerificationResponse


# ========== PLATFORM CONFIG ==========

class PlatformConfigResponse(BaseModel):
    key: str
    value: str
    description: Optional[str] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
