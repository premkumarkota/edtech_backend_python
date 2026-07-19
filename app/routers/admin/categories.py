
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.models.category import Category
from app.schemas.category import CategoryResponse
from app.services.storage_service import upload_file

router = APIRouter()

@router.post("/", response_model=CategoryResponse)
async def create_category(
    name: str = Form(...),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Admin creates a new category (e.g., '10th Class', 'B.Tech')"""
    existing_cat = db.query(Category).filter(Category.name == name).first()
    if existing_cat:
        raise HTTPException(status_code=400, detail="Category already exists")

    image_url = None
    if image:
        image_url = await upload_file(image, folder="categories")

    new_cat = Category(name=name, image_url=image_url)
    db.add(new_cat)
    db.commit()
    db.refresh(new_cat)
    return new_cat

@router.put("/{category_id}", response_model=CategoryResponse)
async def update_category(
    category_id: int,
    name: Optional[str] = Form(None),
    image: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """Update category name and/or image."""
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")
    if name is not None and name != cat.name:
        existing = db.query(Category).filter(Category.name == name).first()
        if existing:
            raise HTTPException(status_code=400, detail="Category with this name already exists")
        cat.name = name
    if image:
        cat.image_url = await upload_file(image, folder="categories")
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
