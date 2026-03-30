from datetime import date as date_type, datetime, time, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.dependencies import get_onboarded_student, get_current_student
from app.models.user import User, UserRole
from app.models.category import Category
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.payout import TeacherRate
from app.models.availability import TeacherAvailability, TeacherAvailabilityOverride
from app.models.session import VideoCallSession, SessionStatus
from app.schemas.student import HomeFeedResponse, TeacherCardResponse, TeacherDetailResponse
from app.schemas.availability import TeacherSlotsResponse, TimeSlot, FcmTokenRequest

router = APIRouter()


def _get_rate(teacher_id: int, rate_map: dict) -> float:
    """Return approved rate for a teacher, or platform default."""
    return rate_map.get(teacher_id, 0.00)


@router.get("/teachers", response_model=HomeFeedResponse)
def get_home_feed(
    db: Session = Depends(get_db),
    student: User = Depends(get_onboarded_student),
):
    """
    Student home feed — list of APPROVED teachers in the student's category.

    Returns teacher cards with rate_per_minute, subjects, languages, location
    so students can compare before tapping into the detail page.

    Filters:
      - Same category as student
      - APPROVED status only
      - is_active = True
    """
    if not student.category_id:
        return HomeFeedResponse(student_category_id=None, teachers=[], total_count=0)

    teachers = (
        db.query(User)
        .options(joinedload(User.teacher_profile))
        .join(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(
            User.role == UserRole.TEACHER,
            User.is_active == True,
            User.category_id == student.category_id,
            TeacherProfile.status == TeacherStatus.APPROVED,
        )
        .order_by(User.created_at.desc())
        .all()
    )

    if not teachers:
        return HomeFeedResponse(
            student_category_id=student.category_id,
            teachers=[],
            total_count=0,
        )

    # Batch-fetch all rates for these teachers (2 queries total, no N+1)
    teacher_ids = [t.id for t in teachers]
    rates = db.query(TeacherRate).filter(TeacherRate.teacher_id.in_(teacher_ids)).all()
    rate_map = {r.teacher_id: float(r.rate_per_hour) for r in rates}

    teacher_cards = [
        TeacherCardResponse(
            id=t.id,
            name=t.name,
            profile_image_url=t.profile_image_url,
            category_id=t.category_id,
            location=t.teacher_profile.location if t.teacher_profile else None,
            bio=t.teacher_profile.bio if t.teacher_profile else None,
            experience_years=t.teacher_profile.experience_years if t.teacher_profile else None,
            institution_name=t.teacher_profile.institution_name if t.teacher_profile else None,
            rate_per_hour=_get_rate(t.id, rate_map),
            subjects=t.teacher_profile.subjects if t.teacher_profile else [],
            languages=t.teacher_profile.languages if t.teacher_profile else [],
        )
        for t in teachers
    ]

    return HomeFeedResponse(
        student_category_id=student.category_id,
        teachers=teacher_cards,
        total_count=len(teacher_cards),
    )


@router.get("/teachers/{teacher_id}", response_model=TeacherDetailResponse)
def get_teacher_detail(
    teacher_id: int,
    db: Session = Depends(get_db),
    student: User = Depends(get_current_student),
):
    """
    Full teacher profile page — the Practo doctor-detail equivalent.
    Called when student taps a teacher card.

    Returns complete profile: education[], experience[], subjects[],
    languages[], achievements, bio, rate, category_name.

    Only APPROVED teachers are accessible.
    """
    teacher = (
        db.query(User)
        .options(joinedload(User.teacher_profile))
        .join(TeacherProfile, User.id == TeacherProfile.user_id)
        .filter(
            User.id == teacher_id,
            User.role == UserRole.TEACHER,
            User.is_active == True,
            TeacherProfile.status == TeacherStatus.APPROVED,
        )
        .first()
    )

    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found or not yet approved")

    profile  = teacher.teacher_profile
    category = db.query(Category).filter(Category.id == teacher.category_id).first() if teacher.category_id else None
    rate_row = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher.id).first()

    return TeacherDetailResponse(
        id=teacher.id,
        name=teacher.name,
        profile_image_url=teacher.profile_image_url,
        category_id=teacher.category_id,
        category_name=category.name if category else None,
        location=profile.location if profile else None,
        rate_per_hour=float(rate_row.rate_per_hour) if rate_row else 0.00,
        bio=profile.bio if profile else None,
        experience_years=profile.experience_years if profile else None,
        institution_name=profile.institution_name if profile else None,
        education=profile.education if profile else [],
        experience=profile.experience if profile else [],
        subjects=profile.subjects if profile else [],
        languages=profile.languages if profile else [],
        achievements=profile.achievements if profile else None,
    )


@router.get("/teachers/{teacher_id}/slots", response_model=TeacherSlotsResponse)
def get_teacher_slots(
    teacher_id: int,
    date: date_type,
    db: Session = Depends(get_db),
    student: User = Depends(get_current_student),
):
    """
    Return all bookable time slots for a teacher on a specific date.
    Slots are generated from the teacher's weekly schedule minus already-booked sessions.

    Query param: ?date=2026-04-01
    """
    # 1. Check teacher exists and is approved
    teacher = db.query(User).filter(
        User.id == teacher_id,
        User.role == UserRole.TEACHER,
        User.is_active == True,
    ).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found.")

    profile = db.query(TeacherProfile).filter(
        TeacherProfile.user_id == teacher_id,
        TeacherProfile.status == TeacherStatus.APPROVED,
    ).first()
    if not profile:
        raise HTTPException(status_code=404, detail="Teacher not available.")

    # 2. Check if date is blocked (day off)
    override = db.query(TeacherAvailabilityOverride).filter(
        TeacherAvailabilityOverride.teacher_id == teacher_id,
        TeacherAvailabilityOverride.date == date,
        TeacherAvailabilityOverride.is_blocked == True,
    ).first()
    if override:
        return TeacherSlotsResponse(teacher_id=teacher_id, date=date, slots=[])

    # 3. Get weekly schedule for this day_of_week (0=Mon … 6=Sun)
    day_of_week = date.weekday()  # Python: Mon=0 … Sun=6
    blocks = db.query(TeacherAvailability).filter(
        TeacherAvailability.teacher_id == teacher_id,
        TeacherAvailability.day_of_week == day_of_week,
        TeacherAvailability.is_active == True,
    ).all()

    if not blocks:
        return TeacherSlotsResponse(teacher_id=teacher_id, date=date, slots=[])

    # 4. Collect already-booked session start times on this date
    day_start = datetime.combine(date, time.min).replace(tzinfo=timezone.utc)
    day_end   = datetime.combine(date, time.max).replace(tzinfo=timezone.utc)
    booked_sessions = db.query(VideoCallSession).filter(
        VideoCallSession.teacher_id == teacher_id,
        VideoCallSession.scheduled_at >= day_start,
        VideoCallSession.scheduled_at <= day_end,
        VideoCallSession.status.in_([
            SessionStatus.BOOKED.value,
            SessionStatus.IN_PROGRESS.value,
        ]),
    ).all()
    booked_starts = {
        s.scheduled_at.astimezone(timezone.utc).strftime("%H:%M")
        for s in booked_sessions
    }

    # 5. Generate slots from each availability block
    slots: list[TimeSlot] = []
    for block in blocks:
        slot_start = datetime.combine(date, block.start_time)
        block_end  = datetime.combine(date, block.end_time)
        delta      = timedelta(minutes=block.slot_duration_mins)

        while slot_start + delta <= block_end:
            slot_end   = slot_start + delta
            start_str  = slot_start.strftime("%H:%M")
            end_str    = slot_end.strftime("%H:%M")
            slots.append(TimeSlot(
                start=start_str,
                end=end_str,
                available=start_str not in booked_starts,
            ))
            slot_start = slot_end

    return TeacherSlotsResponse(teacher_id=teacher_id, date=date, slots=slots)


@router.put("/fcm-token")
def update_student_fcm_token(
    payload: FcmTokenRequest,
    student: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    """
    Called by the student app on every login to keep the push token fresh.
    """
    student.fcm_token = payload.fcm_token
    db.commit()
    return {"message": "FCM token updated."}
