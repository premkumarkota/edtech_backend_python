"""
Admin — Teacher Rate & Payout Management
PUT    /api/admin/teacher-rates/{teacher_id}          Set per-hour rate
GET    /api/admin/teacher-rates/                      List all teacher rates

The monthly payout-batch endpoints were removed (never used; could double-pay
teachers alongside withdrawals). Teachers are paid via /api/admin/withdrawals.
GET    /api/admin/subscriptions/                      View all student subscriptions
PATCH  /api/admin/subscriptions/{id}/cancel           Force-cancel subscription
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User, UserRole
from app.models.payout import TeacherRate
from app.models.subscription import StudentSubscription, SubscriptionStatus
from app.schemas.payout import TeacherRateSet, TeacherRateResponse
from app.schemas.subscription import StudentSubscriptionResponse

router = APIRouter()


# ── Teacher Rates ─────────────────────────────────────────────────

@router.put("/teacher-rates/{teacher_id}", response_model=TeacherRateResponse)
def set_teacher_rate(
    teacher_id: int,
    payload: TeacherRateSet,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Set or update a teacher's per-hour call rate."""
    teacher = db.query(User).filter(User.id == teacher_id, User.role == UserRole.TEACHER).first()
    if not teacher:
        raise HTTPException(status_code=404, detail="Teacher not found")

    rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == teacher_id).first()
    if rate:
        rate.rate_per_hour   = payload.rate_per_hour
        rate.set_by_admin_id = admin.id
    else:
        rate = TeacherRate(
            teacher_id=teacher_id,
            rate_per_hour=payload.rate_per_hour,
            set_by_admin_id=admin.id,
        )
        db.add(rate)

    db.commit()
    db.refresh(rate)
    return {
        "teacher_id": teacher.id,
        "teacher_name": teacher.name,
        "rate_per_hour": rate.rate_per_hour,
        "updated_at": rate.updated_at,
    }


@router.get("/teacher-rates", response_model=List[TeacherRateResponse])
def list_teacher_rates(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all teachers with their current per-hour rate."""
    teachers = db.query(User).filter(User.role == UserRole.TEACHER).all()
    result = []
    for t in teachers:
        rate = db.query(TeacherRate).filter(TeacherRate.teacher_id == t.id).first()
        result.append({
            "teacher_id": t.id,
            "teacher_name": t.name,
            "rate_per_hour": rate.rate_per_hour if rate else "0.00",
            "updated_at": rate.updated_at if rate else None,
        })
    return result


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
