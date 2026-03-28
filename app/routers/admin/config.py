from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.models.platform_config import PlatformConfig
from app.schemas.admin import PlatformConfigResponse, PlatformConfigUpdateRequest

router = APIRouter()

DEFAULT_MAX_RATE = 100.00


def get_max_teacher_rate(db: Session) -> float:
    """
    Returns the platform max teacher rate from DB.
    Falls back to 100.00 if the row doesn't exist yet.
    """
    row = db.query(PlatformConfig).filter(
        PlatformConfig.key == "max_teacher_rate_per_minute"
    ).first()
    if not row:
        return DEFAULT_MAX_RATE
    try:
        return float(row.value)
    except ValueError:
        return DEFAULT_MAX_RATE


@router.get("", response_model=List[PlatformConfigResponse])
def list_config(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """List all platform config settings."""
    return db.query(PlatformConfig).all()


@router.put("/max-teacher-rate", response_model=PlatformConfigResponse)
def set_max_teacher_rate(
    body: PlatformConfigUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Set the platform ceiling for teacher rates (INR per minute).
    Teachers cannot propose a rate above this value.
    Existing approved rates are NOT changed — only affects future proposals/approvals.
    """
    try:
        new_max = float(body.value)
    except ValueError:
        raise HTTPException(status_code=400, detail="Value must be a number (e.g. '100.00')")

    if new_max <= 0:
        raise HTTPException(status_code=400, detail="Max rate must be greater than 0")

    row = db.query(PlatformConfig).filter(
        PlatformConfig.key == "max_teacher_rate_per_minute"
    ).first()

    if not row:
        row = PlatformConfig(
            key="max_teacher_rate_per_minute",
            description="Maximum rate (INR/min) a teacher can propose or be assigned",
        )
        db.add(row)

    row.value = str(new_max)
    row.updated_by = admin.id
    db.commit()
    db.refresh(row)
    return row
