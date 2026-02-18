from pydantic import BaseModel, EmailStr
from typing import Optional
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
