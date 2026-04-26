"""
Admin — Subscription Plan Management
POST   /api/admin/plans/            Create a plan
GET    /api/admin/plans/            List all plans
PATCH  /api/admin/plans/{id}        Update a plan
DELETE /api/admin/plans/{id}        Soft-delete (set inactive)
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.subscription import SubscriptionPlan
from app.schemas.subscription import (
    SubscriptionPlanCreate, SubscriptionPlanUpdate, SubscriptionPlanResponse
)

router = APIRouter()


@router.post("", response_model=SubscriptionPlanResponse, status_code=201)
def create_plan(
    payload: SubscriptionPlanCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Create a new subscription plan."""
    plan = SubscriptionPlan(**payload.dict())
    db.add(plan)
    db.commit()
    db.refresh(plan)
    return plan


@router.get("", response_model=List[SubscriptionPlanResponse])
def list_plans(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List active plans for admin management. Deactivated plans are excluded."""
    return (
        db.query(SubscriptionPlan)
        .filter(SubscriptionPlan.is_active == True)
        .order_by(SubscriptionPlan.created_at.desc())
        .all()
    )


@router.patch("/{plan_id}", response_model=SubscriptionPlanResponse)
def update_plan(
    plan_id: int,
    payload: SubscriptionPlanUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Update a subscription plan.
    NOTE: Changes do NOT affect existing active subscriptions
    (entitlements are snapshotted at purchase time).
    """
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    update_data = payload.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(plan, field, value)

    db.commit()
    db.refresh(plan)
    return plan


@router.delete("/{plan_id}")
def delete_plan(
    plan_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete a plan (set inactive). Never hard-deleted due to subscription references."""
    plan = db.query(SubscriptionPlan).filter(SubscriptionPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    plan.is_active = False
    db.commit()
    return {"message": f"Plan '{plan.name}' deactivated successfully."}
