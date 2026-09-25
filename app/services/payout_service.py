"""
Teacher earnings — rate lookup and per-session earning creation.
Payouts to teachers happen through withdrawals (see teacher_wallet.py);
the old admin payout-batch engine was removed.
"""
from decimal import Decimal
from sqlalchemy.orm import Session

from app.models.payout import TeacherEarning, TeacherRate
from app.models.session import VideoCallSession


def get_teacher_rate(teacher_id: int, db: Session) -> Decimal:
    """Get the current hourly rate for a teacher. Default 0.00 if not set."""
    rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
    return rate.rate_per_hour if rate else Decimal("0.00")


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
    gross = (Decimal(str(actual_duration_mins)) / Decimal("60")) * rate

    earning = TeacherEarning(
        teacher_id=session.teacher_id,
        session_id=session.id,
        duration_mins=actual_duration_mins,
        rate_per_hour=rate,
        gross_earning=gross,
        payout_status="pending",
    )
    db.add(earning)
    return earning
