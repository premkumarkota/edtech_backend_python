
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.models.category import Category
from app.schemas.category import CategoryCreate, CategoryUpdate, CategoryResponse

router = APIRouter()

@router.post("/", response_model=CategoryResponse)
def create_category(
    category: CategoryCreate, 
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin creates a new category (e.g., '10th Class', 'B.Tech')"""
    existing_cat = db.query(Category).filter(Category.name == category.name).first()
    if existing_cat:
        raise HTTPException(status_code=400, detail="Category already exists")
    
    new_cat = Category(name=category.name, image_url=category.image_url)
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

@router.put("/{category_id}", response_model=CategoryResponse)
def update_category(
    category_id: int,
    payload: CategoryUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if payload.name is not None and payload.name != cat.name:
        existing = db.query(Category).filter(Category.name == payload.name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Category with this name already exists")
        cat.name = payload.name
    if payload.image_url is not None:
        cat.image_url = payload.image_url
    db.commit()
    db.refresh(cat)
    return cat

@router.delete("/{category_id}")
def delete_category(
    category_id: int, 
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """Admin deletes a category"""
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    
    db.delete(cat)
    db.commit()
    return {"message": "Category deleted successfully"}
