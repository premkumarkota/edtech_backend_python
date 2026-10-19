
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.models.category import Category
from app.models.exam_category import ExamCategory
from app.schemas.category import CategoryResponse

router = APIRouter()

@router.get("", response_model=List[CategoryResponse])
def get_all_categories(db: Session = Depends(get_db)):
    """
    Get all active boards (the `categories` table), each with its
    `exam_category_id` / `exam_category_name` grouping (null = uncategorized).
    Ordered by category order, then board name, so apps can render
    "category heading → boards" directly.
    Public API (Used by Student/Teacher during onboarding).
    """
    return (
        db.query(Category)
        .outerjoin(ExamCategory, Category.exam_category_id == ExamCategory.id)
        .filter(Category.is_active == True)
        .order_by(ExamCategory.order_index.is_(None), ExamCategory.order_index,
                  ExamCategory.id, Category.name)
        .all()
    )
