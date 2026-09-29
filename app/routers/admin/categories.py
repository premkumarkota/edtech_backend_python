
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from typing import List, Optional
from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User
from app.models.category import Category
from app.models.quiz import Quiz
from app.models.study_planner_v2 import GoalExam
from app.models.syllabus import Syllabus
from app.schemas.category import CategoryResponse
from app.services.storage_service import upload_file, ALLOWED_IMAGE_TYPES

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
        image_url = await upload_file(
            image,
            folder="categories",
            allowed_types=ALLOWED_IMAGE_TYPES,
            max_size_mb=5,
        )

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
        cat.image_url = await upload_file(
            image,
            folder="categories",
            allowed_types=ALLOWED_IMAGE_TYPES,
            max_size_mb=5,
        )
    db.commit()
    db.refresh(cat)
    return cat

@router.delete("/{category_id}")
def delete_category(
    category_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin)
):
    """
    Admin deletes a category.

    Refuses (409) while anything still uses it — users, syllabus subjects,
    quizzes or study-goal exams all reference categories with NO ACTION
    foreign keys, so a delete would fail in the database anyway. The message
    tells the admin exactly what to move first.
    """
    cat = db.query(Category).filter(Category.id == category_id).first()
    if not cat:
        raise HTTPException(status_code=404, detail="Category not found")

    usage = [
        (db.query(User).filter(User.category_id == category_id).count(), "user", "users"),
        (db.query(Syllabus).filter(Syllabus.category_id == category_id).count(), "syllabus subject", "syllabus subjects"),
        (db.query(Quiz).filter(Quiz.category_id == category_id).count(), "quiz", "quizzes"),
        (db.query(GoalExam).filter(GoalExam.category_id == category_id).count(), "study-goal exam", "study-goal exams"),
    ]
    in_use = [f"{n} {one if n == 1 else many}" for n, one, many in usage if n]
    if in_use:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Can't delete \"{cat.name}\" because it's still in use: "
                f"{', '.join(in_use)}. Move them to another category first, then delete it."
            ),
        )

    db.delete(cat)
    db.commit()
    return {"message": "Category deleted successfully"}
