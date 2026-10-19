
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class CategoryBase(BaseModel):
    name: str
    image_url: Optional[str] = None

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    name: Optional[str] = None
    image_url: Optional[str] = None

class CategoryResponse(CategoryBase):
    id: int
    is_active: bool
    created_at: datetime
    exam_category_id: Optional[int] = None
    exam_category_name: Optional[str] = None
    
    class Config:
        from_attributes = True


class ExamCategoryRequest(BaseModel):
    name: str
    order_index: Optional[int] = None


class ExamCategoryUpdate(BaseModel):
    name: Optional[str] = None
    order_index: Optional[int] = None


class ExamCategoryResponse(BaseModel):
    id: int
    name: str
    order_index: int
    is_active: bool
    board_count: int = 0

    class Config:
        from_attributes = True
