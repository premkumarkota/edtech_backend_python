"""
Teacher Wallet — single source of truth for a teacher's withdrawable balance.

Ledger model (no per-earning bookkeeping needed for money movement):

  total_earned      = Σ TeacherEarning.gross_earning               (every session)
  legacy_paid       = Σ earnings paid by the old admin batch payout  (payout_batch_id set)
  total_withdrawn   = Σ WithdrawalRequest.amount  status='completed'
  in_flight         = Σ WithdrawalRequest.amount  status in (pending, processing)

  available_balance = total_earned − legacy_paid − total_withdrawn − in_flight

A teacher can withdraw ANY amount between the admin-set minimum and
available_balance; exactly that amount is sent. Failed / rejected withdrawals
simply drop out of the sums, so the money is back in the balance automatically.

TeacherEarning.payout_status is kept only as a per-session display label:
sync_earning_labels() marks the oldest sessions 'paid' while they are FULLY
covered by completed withdrawals — it never affects the balance.

Minimum withdrawal lives in PlatformConfig (key 'min_withdrawal_amount') and
is editable from the admin Settings screen.
"""
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.payout import TeacherEarning
from app.models.platform_config import PlatformConfig
from app.models.withdrawal import WithdrawalRequest

MIN_WITHDRAWAL_KEY = "min_withdrawal_amount"
DEFAULT_MIN_WITHDRAWAL = Decimal("1.00")     # Cashfree floor; admin raises it in Settings

_ZERO = Decimal("0.00")
_IN_FLIGHT = ("pending", "processing")


@dataclass
class TeacherWallet:
    total_earned: Decimal
    total_withdrawn: Decimal     # completed withdrawals + legacy batch payouts
    in_flight: Decimal           # pending + processing withdrawals
    available_balance: Decimal
    session_count: int


def _d(value) -> Decimal:
    return Decimal(str(value or 0)).quantize(Decimal("0.01"))


def get_wallet(teacher_id: int, db: Session) -> TeacherWallet:
    earned, sessions = db.query(
        func.coalesce(func.sum(TeacherEarning.gross_earning), 0),
        func.count(TeacherEarning.id),
    ).filter(TeacherEarning.teacher_id == teacher_id).one()

    legacy_paid = db.query(
        func.coalesce(func.sum(TeacherEarning.gross_earning), 0)
    ).filter(
        TeacherEarning.teacher_id == teacher_id,
        TeacherEarning.payout_status == "paid",
        TeacherEarning.payout_batch_id.isnot(None),
    ).scalar()

    withdrawn = db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(
        WithdrawalRequest.teacher_id == teacher_id,
        WithdrawalRequest.status == "completed",
    ).scalar()

    in_flight = db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(
        WithdrawalRequest.teacher_id == teacher_id,
        WithdrawalRequest.status.in_(_IN_FLIGHT),
    ).scalar()

    earned, legacy_paid, withdrawn, in_flight = map(_d, (earned, legacy_paid, withdrawn, in_flight))
    available = max(earned - legacy_paid - withdrawn - in_flight, _ZERO)

    return TeacherWallet(
        total_earned=earned,
        total_withdrawn=legacy_paid + withdrawn,
        in_flight=in_flight,
        available_balance=available,
        session_count=sessions or 0,
    )


def sync_earning_labels(teacher_id: int, db: Session) -> None:
    """
    Recompute per-session 'paid'/'pending' labels from completed withdrawals.
    Oldest sessions are 'paid' only while fully covered — never over-marks.
    Legacy batch-paid rows are left untouched. Caller commits.
    """
    covered = _d(db.query(func.coalesce(func.sum(WithdrawalRequest.amount), 0)).filter(
        WithdrawalRequest.teacher_id == teacher_id,
        WithdrawalRequest.status == "completed",
    ).scalar())

    earnings = (
        db.query(TeacherEarning)
        .filter(
            TeacherEarning.teacher_id == teacher_id,
            TeacherEarning.payout_batch_id.is_(None),
        )
        .order_by(TeacherEarning.created_at.asc(), TeacherEarning.id.asc())
        .all()
    )
    running = _ZERO
    for e in earnings:
        running += _d(e.gross_earning)
        e.payout_status = "paid" if running <= covered else "pending"


# ── Minimum withdrawal (admin-configurable) ───────────────────────────────────

def get_min_withdrawal(db: Session) -> Decimal:
    row = db.query(PlatformConfig).filter(PlatformConfig.key == MIN_WITHDRAWAL_KEY).first()
    if not row:
        return DEFAULT_MIN_WITHDRAWAL
    try:
        return _d(row.value)
    except Exception:
        return DEFAULT_MIN_WITHDRAWAL
