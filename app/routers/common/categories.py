
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.category import Category
from app.schemas.category import CategoryResponse

router = APIRouter()

@router.get("", response_model=List[CategoryResponse])
def get_all_categories(db: Session = Depends(get_db)):
    """
    Get all active categories.
    Public API (Used by Student/Teacher during onboarding).
    """
    return db.query(Category).filter(Category.is_active == True).all()
