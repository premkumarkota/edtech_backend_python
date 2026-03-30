"""
Teacher — Home Screen
GET /api/teacher/home

Returns different payload based on profile status:
  - pending  → lock screen data (status, submitted_at, what to expect)
  - approved → full home dashboard (profile, rate, stats)
  - rejected → rejection reason + re-onboard prompt
"""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.dependencies import get_current_teacher
from app.models.user import User
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.payout import TeacherRate, TeacherEarning
from app.models.session import VideoCallSession, SessionStatus
from app.models.category import Category

router = APIRouter()


@router.get("")
def teacher_home(
    teacher: User = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Single endpoint for teacher home screen.
    The app checks `status` field and renders the right screen:

      status = "not_onboarded" → show onboarding flow
      status = "pending"       → show "Under Review" lock screen
      status = "approved"      → show home dashboard
      status = "rejected"      → show rejection screen with reason
    """
    # ── Not onboarded yet ─────────────────────────────────────────────────────
    if not teacher.onboarding_completed:
        return {
            "status": "not_onboarded",
            "teacher_id": teacher.id,
            "message": "Please complete your profile to get started.",
        }

    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == teacher.id
    ).first()

    if not profile:
        return {
            "status": "not_onboarded",
            "teacher_id": teacher.id,
            "message": "Please complete your profile to get started.",
        }

    # ── Pending review ────────────────────────────────────────────────────────
    if profile.status == TeacherStatus.PENDING:
        return {
            "status": "pending",
            "teacher_id": teacher.id,
            "name": teacher.name,
            "profile_image_url": teacher.profile_image_url,
            "submitted_at": profile.created_at,
            "message": "Your profile is under review by our team.",
            "sub_message": "We typically review profiles within 24-48 hours. You'll receive a push notification once reviewed.",
            "what_happens_next": [
                "Our team reviews your credentials and document",
                "Admin sets your hourly rate based on your experience",
                "You receive a push notification with the result",
                "Once approved, students can book sessions with you",
            ],
        }

    # ── Rejected ──────────────────────────────────────────────────────────────
    if profile.status == TeacherStatus.REJECTED:
        return {
            "status": "rejected",
            "teacher_id": teacher.id,
            "name": teacher.name,
            "profile_image_url": teacher.profile_image_url,
            "rejection_reason": profile.rejection_reason,
            "reviewed_at": profile.reviewed_at,
            "message": "Your profile was not approved.",
            "sub_message": "Please review the reason below and re-submit your profile with the required changes.",
            "can_reapply": True,
        }

    # ── Approved — full dashboard ─────────────────────────────────────────────
    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher.id).first()
    category = db.query(Category).filter(Category.id == teacher.category_id).first() if teacher.category_id else None

    now = datetime.now(timezone.utc)

    # Upcoming sessions count
    upcoming_count = db.query(func.count(VideoCallSession.id)).filter(
        VideoCallSession.teacher_id == teacher.id,
        VideoCallSession.status == SessionStatus.BOOKED.value,
        VideoCallSession.scheduled_at > now,
    ).scalar() or 0

    # Pending earnings
    pending_earnings = db.query(func.coalesce(func.sum(TeacherEarning.gross_earning), 0)).filter(
        TeacherEarning.teacher_id == teacher.id,
        TeacherEarning.payout_status == "pending",
    ).scalar()

    # Total sessions completed
    completed_count = db.query(func.count(VideoCallSession.id)).filter(
        VideoCallSession.teacher_id == teacher.id,
        VideoCallSession.status == SessionStatus.COMPLETED.value,
    ).scalar() or 0

    return {
        "status": "approved",
        "teacher_id": teacher.id,
        "name": teacher.name,
        "profile_image_url": teacher.profile_image_url,
        "phone_number": teacher.phone_number,
        "email": teacher.email,
        "category_id": teacher.category_id,
        "category_name": category.name if category else None,

        # Rate
        "rate_per_hour": float(rate_row.rate_per_hour) if rate_row else None,
        "rate_display": f"₹{rate_row.rate_per_hour}/hr" if rate_row else "Rate not set",

        # Rich profile
        "bio": profile.bio,
        "experience_years": profile.experience_years,
        "institution_name": profile.institution_name,
        "location": profile.location,
        "subjects": profile.subjects or [],
        "languages": profile.languages or [],
        "education": profile.education or [],
        "experience": profile.experience or [],
        "achievements": profile.achievements,

        # Dashboard stats
        "stats": {
            "upcoming_sessions": upcoming_count,
            "completed_sessions": completed_count,
            "pending_earnings": float(pending_earnings),
            "pending_earnings_display": f"₹{pending_earnings}",
        },

        "approved_at": profile.reviewed_at,
        "message": "Your profile is live. Students can now book sessions with you!",
    }
