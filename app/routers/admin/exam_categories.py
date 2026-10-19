from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.models.category import Category
from app.models.exam_category import ExamCategory
from app.schemas.category import (
    ExamCategoryRequest, ExamCategoryUpdate, ExamCategoryResponse,
)

router = APIRouter()


def _board_counts(db: Session) -> dict:
    rows = (
        db.query(Category.exam_category_id, func.count(Category.id))
        .filter(Category.exam_category_id.isnot(None))
        .group_by(Category.exam_category_id)
        .all()
    )
    return {cid: n for cid, n in rows}


def _to_response(cat: ExamCategory, counts: dict) -> ExamCategoryResponse:
    return ExamCategoryResponse(
        id=cat.id, name=cat.name, order_index=cat.order_index or 0,
        is_active=bool(cat.is_active), board_count=counts.get(cat.id, 0),
    )


@router.get("", response_model=List[ExamCategoryResponse])
def list_exam_categories(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """All categories (Entrance / Competitive / Academic …) with board counts."""
    counts = _board_counts(db)
    cats = db.query(ExamCategory).order_by(ExamCategory.order_index, ExamCategory.id).all()
    return [_to_response(c, counts) for c in cats]


@router.post("", response_model=ExamCategoryResponse)
def create_exam_category(
    body: ExamCategoryRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Category name is required")
    if db.query(ExamCategory).filter(func.lower(ExamCategory.name) == name.lower()).first():
        raise HTTPException(status_code=400, detail="Category already exists")
    order = body.order_index
    if order is None:
        order = (db.query(func.max(ExamCategory.order_index)).scalar() or 0) + 1
    cat = ExamCategory(name=name, order_index=order)
    db.add(cat)
    db.commit()
    db.refresh(cat)
    return _to_response(cat, {})


@router.put("/{exam_category_id}", response_model=ExamCategoryResponse)
def update_exam_category(
    exam_category_id: int,
    body: ExamCategoryUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    cat = db.query(ExamCategory).filter(ExamCategory.id == exam_category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=400, detail="Category name is required")
        clash = (
            db.query(ExamCategory)
            .filter(func.lower(ExamCategory.name) == name.lower(), ExamCategory.id != cat.id)
            .first()
        )
        if clash:
            raise HTTPException(status_code=400, detail="Category with this name already exists")
        cat.name = name
    if body.order_index is not None:
        cat.order_index = body.order_index
    db.commit()
    db.refresh(cat)
    return _to_response(cat, _board_counts(db))


@router.delete("/{exam_category_id}")
def delete_exam_category(
    exam_category_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Deletes the category; its boards stay and become uncategorized."""
    cat = db.query(ExamCategory).filter(ExamCategory.id == exam_category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    db.query(Category).filter(Category.exam_category_id == cat.id).update(
        {Category.exam_category_id: None}, synchronize_session=False
    )
    db.delete(cat)
    db.commit()
    return {"message": "Category deleted. Its boards are now uncategorized."}
