"""
Admin — Subscription Management
GET   /api/admin/subscriptions              List all subscriptions (with student name + plan name)
PATCH /api/admin/subscriptions/{id}/cancel  Force-cancel an active subscription
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional
from datetime import datetime, timezone

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.subscription import StudentSubscription, SubscriptionPlan, SubscriptionStatus

router = APIRouter()


def _serialize(sub: StudentSubscription) -> dict:
    student_name = None
    student_email = None
    if sub.student:
        student_name = sub.student.name or sub.student.email
        student_email = sub.student.email

    plan_name = sub.plan.name if sub.plan else None

    return {
        "id": sub.id,
        "student_id": sub.student_id,
        "student_name": student_name,
        "student_email": student_email,
        "plan_id": sub.plan_id,
        "plan_name": plan_name,
        "status": sub.status,
        "amount_paid": float(sub.amount_paid) if sub.amount_paid else None,
        "started_at": sub.started_at.isoformat() if sub.started_at else None,
        "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        "mock_tests_allowed": sub.mock_tests_allowed,
        "mock_tests_used": sub.mock_tests_used,
        "video_call_minutes_total": sub.video_call_minutes_total,
        "video_call_minutes_used": sub.video_call_minutes_used,
        "razorpay_order_id": sub.razorpay_order_id,
        "razorpay_payment_id": sub.razorpay_payment_id,
        "created_at": sub.created_at.isoformat() if sub.created_at else None,
    }


@router.get("")
def list_subscriptions(
    status: Optional[str] = Query(None, description="Filter by status: active|pending|expired|cancelled|failed"),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List all student subscriptions with student name and plan name."""
    q = db.query(StudentSubscription).options(
        joinedload(StudentSubscription.student),
        joinedload(StudentSubscription.plan),
    )
    if status and status != "all":
        q = q.filter(StudentSubscription.status == status)

    subs = q.order_by(StudentSubscription.created_at.desc()).all()
    return [_serialize(s) for s in subs]


@router.patch("/{sub_id}/cancel")
def cancel_subscription(
    sub_id: int,
    payload: dict,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Force-cancel an active subscription."""
    sub = db.query(StudentSubscription).filter(StudentSubscription.id == sub_id).first()
    if not sub:
        raise HTTPException(status_code=404, detail="Subscription not found")
    if sub.status != SubscriptionStatus.ACTIVE.value:
        raise HTTPException(status_code=400, detail="Only active subscriptions can be cancelled")

    sub.status = SubscriptionStatus.CANCELLED.value
    db.commit()
    return {"message": "Subscription cancelled successfully"}
