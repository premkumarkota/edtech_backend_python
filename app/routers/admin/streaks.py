"""Admin — learning streak engagement stats (read-only)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.services.streak_service import admin_stats

router = APIRouter()


@router.get("/stats")
def streak_stats(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return admin_stats(db)
