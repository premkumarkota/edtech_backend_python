"""
Payout Engine
Admin-triggered batch calculation of teacher earnings.
All DB writes are wrapped in a single atomic transaction.
"""
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy.orm import Session
from fastapi import HTTPException, status

from app.models.payout import TeacherEarning, PayoutBatch, TeacherPayout, TeacherRate
from app.models.session import VideoCallSession, SessionStatus
from app.models.user import User


def get_teacher_rate(teacher_id: int, db: Session) -> Decimal:
    """Get the current per-minute rate for a teacher. Default 2.00 if not set."""
    rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
    return rate.rate_per_minute if rate else Decimal("2.00")


def calculate_and_create_payout_batch(
    period_from: date,
    period_to: date,
    admin_id: int,
    notes: str | None,
    db: Session,
) -> PayoutBatch:
    """
    Calculate all pending teacher earnings for the period and create a
    payout batch with per-teacher records.

    Entire operation is ONE DB transaction — if anything fails, nothing is saved.
    """
    period_end_dt = datetime.combine(period_to, datetime.max.time()).replace(tzinfo=timezone.utc)
    period_start_dt = datetime.combine(period_from, datetime.min.time()).replace(tzinfo=timezone.utc)

    # 1. Find all pending earnings in the period
    pending_earnings = (
        db.query(TeacherEarning)
        .join(VideoCallSession, TeacherEarning.session_id == VideoCallSession.id)
        .filter(
            TeacherEarning.payout_status == "pending",
            VideoCallSession.status == SessionStatus.COMPLETED.value,
            VideoCallSession.ended_at >= period_start_dt,
            VideoCallSession.ended_at <= period_end_dt,
        )
        .all()
    )

    if not pending_earnings:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No pending earnings found for the selected period.",
        )

    # 2. Group by teacher_id
    teacher_groups: dict[int, dict] = {}
    for earning in pending_earnings:
        tid = earning.teacher_id
        if tid not in teacher_groups:
            teacher_groups[tid] = {
                "amount": Decimal("0.00"),
                "sessions": 0,
                "earnings": [],
            }
        teacher_groups[tid]["amount"] += earning.gross_earning
        teacher_groups[tid]["sessions"] += 1
        teacher_groups[tid]["earnings"].append(earning)

    total_amount = sum(v["amount"] for v in teacher_groups.values())

    # 3. Create payout batch
    batch = PayoutBatch(
        initiated_by=admin_id,
        period_from=period_from,
        period_to=period_to,
        total_teachers=len(teacher_groups),
        total_amount=total_amount,
        status="processing",
        notes=notes,
    )
    db.add(batch)
    db.flush()   # Assign batch.id without committing yet

    # 4. Create per-teacher payout records + mark earnings as paid
    for teacher_id, data in teacher_groups.items():
        teacher = db.query(User).filter(User.id == teacher_id).first()
        bank_info = None
        if teacher:
            parts = [teacher.bank_account_number, teacher.bank_ifsc_code, teacher.bank_account_name]
            bank_info = " | ".join(p for p in parts if p) or None

        payout = TeacherPayout(
            batch_id=batch.id,
            teacher_id=teacher_id,
            amount=data["amount"],
            sessions_count=data["sessions"],
            bank_account=bank_info,
            status="pending",
        )
        db.add(payout)

        for earning in data["earnings"]:
            earning.payout_status = "paid"
            earning.payout_batch_id = batch.id

    batch.status = "completed"
    batch.completed_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(batch)
    return batch


def create_teacher_earning(
    session: VideoCallSession,
    actual_duration_mins: int,
    db: Session,
) -> TeacherEarning:
    """
    Called when a video call session ends.
    Looks up teacher's current rate, calculates earning, creates record.
    """
    rate = get_teacher_rate(session.teacher_id, db)
    gross = Decimal(str(actual_duration_mins)) * rate

    earning = TeacherEarning(
        teacher_id=session.teacher_id,
        session_id=session.id,
        duration_mins=actual_duration_mins,
        rate_per_minute=rate,
        gross_earning=gross,
        payout_status="pending",
    )
    db.add(earning)
    return earning
