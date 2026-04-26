from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload
from typing import List
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User, UserRole
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.payout import TeacherRate
from app.models.category import Category
from app.schemas.admin import TeacherVerificationResponse, TeacherPendingResponse, TeacherApprovalRequest, MessageResponse
from app.utils.fcm import notify_teacher_approved, notify_teacher_rejected

router = APIRouter()

DEFAULT_RATE = 0.00


def _build_pending_response(teacher: User, db: Session) -> dict:
    """Build TeacherPendingResponse dict from User + related rows."""
    profile  = teacher.teacher_profile
    category = db.query(Category).filter(Category.id == teacher.category_id).first() if teacher.category_id else None
    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher.id).first()

    return {
        **teacher.__dict__,
        "category_name":    category.name if category else None,
        "status":           profile.status if profile else "pending",
        "document_url":     profile.document_url if profile else teacher.document_url,
        "video_url":        profile.video_url if profile else None,
        "bio":              profile.bio if profile else None,
        "experience_years": profile.experience_years if profile else None,
        "institution_name": profile.institution_name if profile else None,
        "location":         profile.location if profile else None,
        "education":        profile.education if profile else [],
        "experience":       profile.experience if profile else [],
        "subjects":         profile.subjects if profile else [],
        "languages":        profile.languages if profile else [],
        "achievements":     profile.achievements if profile else None,
        "rejection_reason": profile.rejection_reason if profile else None,
        "proposed_rate_per_hour": float(profile.proposed_rate_per_hour) if (profile and profile.proposed_rate_per_hour) else None,
        "current_rate_per_hour":  float(rate_row.rate_per_hour) if rate_row else None,
        "rate_per_hour":          float(rate_row.rate_per_hour) if rate_row else None,
    }


@router.get("/pending", response_model=List[TeacherPendingResponse])
def get_pending_teachers(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    List all teachers with TeacherProfile.status = PENDING.
    Returns full rich profile + proposed rate so admin can make an informed decision.
    """
    teachers = (
        db.query(User)
        .options(joinedload(User.teacher_profile))
        .join(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(
            User.role == UserRole.TEACHER,
            User.onboarding_completed == True,
            TeacherProfile.status == TeacherStatus.PENDING,
        )
        .all()
    )
    return [_build_pending_response(t, db) for t in teachers]


@router.get("/status/{status}", response_model=List[TeacherVerificationResponse])
def get_teachers_by_verification_status(
    status: TeacherStatus,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    List teachers by verification status: pending, approved, or rejected.
    Returns full rich profile details for admin review tabs.
    """
    teachers = (
        db.query(User)
        .options(joinedload(User.teacher_profile))
        .join(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(
            User.role == UserRole.TEACHER,
            User.onboarding_completed == True,
            TeacherProfile.status == status,
        )
        .order_by(User.created_at.desc())
        .all()
    )
    return [_build_pending_response(t, db) for t in teachers]


@router.get("/{teacher_id}", response_model=TeacherVerificationResponse)
def get_teacher_detail(
    teacher_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Return a full teacher profile for admin users and verification screens."""
    teacher = (
        db.query(User)
        .options(joinedload(User.teacher_profile))
        .filter(User.id == teacher_id, User.role == UserRole.TEACHER)
        .first()
    )
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")
    return _build_pending_response(teacher, db)


@router.post("/{teacher_id}/approve", response_model=MessageResponse)
def approve_or_reject_teacher(
    teacher_id: int,
    body: TeacherApprovalRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Approve or reject a pending teacher.

    APPROVAL flow:
      1. Resolves final rate: admin_override → teacher_proposal → platform default (₹2)
      2. Validates rate ≤ platform_config.max_teacher_rate_per_minute
      3. Upserts teacher_rates with the approved rate
      4. Sets TeacherProfile.status = APPROVED, reviewed_by, reviewed_at
      5. Sets User.is_verified = True (legacy compat)

    REJECTION flow:
      1. Sets TeacherProfile.status = REJECTED
      2. Stores rejection_reason
      3. Sets reviewed_by, reviewed_at
    """
    teacher = db.query(User).filter(
        User.id == teacher_id, User.role == UserRole.TEACHER
    ).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == teacher_id
    ).first()
    if not profile:
        raise HTTPException(status_code=400, detail="Teacher has not completed onboarding")

    now = datetime.now(timezone.utc)

    if body.approved:
        # ── Resolve final rate ────────────────────────────────────────────────
        if body.approved_rate_per_hour is not None:
            final_rate = body.approved_rate_per_hour
        elif profile.proposed_rate_per_hour is not None:
            final_rate = float(profile.proposed_rate_per_hour)
        else:
            final_rate = DEFAULT_RATE

        if final_rate < 0:
            raise HTTPException(status_code=400, detail="Rate must be non-negative")

        # ── Update TeacherProfile ─────────────────────────────────────────────
        profile.status           = TeacherStatus.APPROVED
        profile.reviewed_by      = admin.id
        profile.reviewed_at      = now
        profile.rejection_reason = None
        teacher.is_verified      = True   # legacy compat

        # ── Upsert teacher_rates ──────────────────────────────────────────────
        rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
        if rate_row:
            rate_row.rate_per_hour   = final_rate
            rate_row.set_by_admin_id = admin.id
        else:
            db.add(TeacherRate(
                teacher_id=teacher_id,
                rate_per_hour=final_rate,
                set_by_admin_id=admin.id,
            ))

        db.commit()

        # FCM push to teacher (non-blocking)
        if teacher.fcm_token:
            notify_teacher_approved(
                fcm_token=teacher.fcm_token,
                teacher_name=teacher.name or "Teacher",
                rate_per_hour=final_rate,
            )

        return MessageResponse(
            message=(
                f"Teacher '{teacher.name}' approved at ₹{final_rate}/hr. "
                "They will now appear in student home feeds."
            ),
            success=True,
        )

    else:
        # ── Rejection ─────────────────────────────────────────────────────────
        if not body.rejection_reason:
            raise HTTPException(
                status_code=400,
                detail="rejection_reason is required when rejecting a teacher",
            )

        profile.status           = TeacherStatus.REJECTED
        profile.reviewed_by      = admin.id
        profile.reviewed_at      = now
        profile.rejection_reason = body.rejection_reason
        teacher.is_verified      = False

        db.commit()

        # FCM push to teacher (non-blocking)
        if teacher.fcm_token:
            notify_teacher_rejected(
                fcm_token=teacher.fcm_token,
                teacher_name=teacher.name or "Teacher",
                reason=body.rejection_reason,
            )

        return MessageResponse(
            message=f"Teacher '{teacher.name}' rejected. Reason: {body.rejection_reason}",
            success=True,
        )


@router.patch("/{teacher_id}/update-rate", response_model=MessageResponse)
def update_teacher_rate(
    teacher_id: int,
    body: dict,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Update an already-approved teacher's rate at any time.
    Only affects FUTURE sessions — existing earnings already have the old rate snapshotted.
    """
    new_rate = body.get("rate_per_hour")
    if new_rate is None:
        raise HTTPException(status_code=400, detail="rate_per_hour is required")

    if float(new_rate) < 0:
        raise HTTPException(status_code=400, detail="Rate must be non-negative")

    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
    if rate_row:
        rate_row.rate_per_hour   = new_rate
        rate_row.set_by_admin_id = admin.id
    else:
        db.add(TeacherRate(
            teacher_id=teacher_id,
            rate_per_hour=new_rate,
            set_by_admin_id=admin.id,
        ))

    db.commit()
    return MessageResponse(
        message=f"Rate updated to ₹{new_rate}/hr for teacher {teacher_id}",
        success=True,
    )
