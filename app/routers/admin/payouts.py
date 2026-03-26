"""
Admin — Teacher Rate & Payout Management
PUT    /api/admin/teacher-rates/{teacher_id}          Set per-minute rate
GET    /api/admin/teacher-rates/                      List all teacher rates
POST   /api/admin/payouts/trigger                     Trigger payout batch
GET    /api/admin/payouts/                            List all batches
GET    /api/admin/payouts/{batch_id}                  Batch detail
PATCH  /api/admin/payouts/{batch_id}/payouts/{id}/mark-paid   Mark paid
GET    /api/admin/subscriptions/                      View all student subscriptions
PATCH  /api/admin/subscriptions/{id}/cancel           Force-cancel subscription
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User, UserRole
from app.models.payout import TeacherRate, PayoutBatch, TeacherPayout, TeacherEarning
from app.models.subscription import StudentSubscription, SubscriptionStatus
from app.schemas.payout import (
    TeacherRateSet, TeacherRateResponse,
    TriggerPayoutRequest, PayoutBatchResponse,
    MarkPayoutTransferredRequest,
)
from app.schemas.subscription import StudentSubscriptionResponse
from app.services.payout_service import calculate_and_create_payout_batch

router = APIRouter()


# ── Teacher Rates ─────────────────────────────────────────────────

@router.put("/teacher-rates/{teacher_id}", response_model=TeacherRateResponse)
def set_teacher_rate(
    teacher_id: int,
    payload: TeacherRateSet,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set or update a teacher's per-minute call rate."""
    teacher = db.query(User).filter(User.id == teacher_id, User.role == UserRole.TEACHER).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
    if rate:
        rate.rate_per_minute = payload.rate_per_minute
        rate.set_by_admin_id = admin.id
    else:
        rate = TeacherRate(
            teacher_id=teacher_id,
            rate_per_minute=payload.rate_per_minute,
            set_by_admin_id=admin.id,
        )
        db.add(rate)

    db.commit()
    db.refresh(rate)
    return {
        "teacher_id": teacher.id,
        "teacher_name": teacher.name,
        "rate_per_minute": rate.rate_per_minute,
        "updated_at": rate.updated_at,
    }


@router.get("/teacher-rates", response_model=List[TeacherRateResponse])
def list_teacher_rates(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all teachers with their current per-minute rate."""
    teachers = db.query(User).filter(User.role == UserRole.TEACHER).all()
    result = []
    for t in teachers:
        rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == t.id).first()
        result.append({
            "teacher_id": t.id,
            "teacher_name": t.name,
            "rate_per_minute": rate.rate_per_minute if rate else "2.00",
            "updated_at": rate.updated_at if rate else None,
        })
    return result


# ── Payout Batches ────────────────────────────────────────────────

@router.post("/payouts/trigger", response_model=PayoutBatchResponse)
def trigger_payout(
    payload: TriggerPayoutRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Trigger a payout batch for a period.
    Atomically groups all pending teacher earnings and creates payout records.
    """
    batch = calculate_and_create_payout_batch(
        period_from=payload.period_from,
        period_to=payload.period_to,
        admin_id=admin.id,
        notes=payload.notes,
        db=db,
    )
    return batch


@router.get("/payouts", response_model=List[PayoutBatchResponse])
def list_payout_batches(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all payout batches (newest first)."""
    return db.query(PayoutBatch).order_by(PayoutBatch.created_at.desc()).all()


@router.get("/payouts/{batch_id}", response_model=PayoutBatchResponse)
def get_payout_batch(
    batch_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Get detailed breakdown of a payout batch, including per-teacher amounts."""
    batch = db.query(PayoutBatch).filter(PayoutBatch.id == batch_id).first()
    if not batch:
        raise HTTPException(status_code=404, detail="Payout batch not found")
    return batch


@router.patch("/payouts/{batch_id}/payouts/{payout_id}/mark-paid")
def mark_payout_transferred(
    batch_id: int,
    payout_id: int,
    payload: MarkPayoutTransferredRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Admin marks a teacher's payout as transferred (manual bank transfer)."""
    payout = db.query(TeacherPayout).filter(
        TeacherPayout.id == payout_id,
        TeacherPayout.batch_id == batch_id,
    ).first()
    if not payout:
        raise HTTPException(status_code=404, detail="Payout record not found")

    payout.status = "transferred"
    payout.transferred_at = datetime.now(timezone.utc)
    if payload.notes:
        payout.notes = payload.notes
    db.commit()
    return {"message": "Payout marked as transferred.", "payout_id": payout_id}


# ── Subscription Management ───────────────────────────────────────

@router.get("/subscriptions", response_model=List[StudentSubscriptionResponse])
def list_subscriptions(
    status: Optional[str] = None,
    plan_id: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all student subscriptions with optional filters."""
    q = db.query(StudentSubscription)
    if status:
        q = q.filter(StudentSubscription.status == status)
    if plan_id:
        q = q.filter(StudentSubscription.plan_id == plan_id)
    return q.order_by(StudentSubscription.created_at.desc()).all()


@router.patch("/subscriptions/{sub_id}/cancel")
def cancel_subscription(
    sub_id: int,
    reason: str = "Admin cancelled",
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Force-cancel a student subscription (fraud, chargeback, abuse)."""
    sub = db.query(StudentSubscription).filter(StudentSubscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.status != SubscriptionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Subscription is not active")

    sub.status = SubscriptionStatus.CANCELLED.value
    db.commit()
    return {"message": f"Subscription {sub_id} cancelled. Reason: {reason}"}
