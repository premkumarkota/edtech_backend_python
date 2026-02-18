from pydantic import BaseModel
from typing import Optional, List, TypeVar, Generic
from datetime import datetime


# ========== COMMON RESPONSE SCHEMAS ==========

class MessageResponse(BaseModel):
    """Simple message response"""
    message: str
    success: bool = True


class ErrorResponse(BaseModel):
    """Error response"""
    detail: str
    success: bool = False


# ========== PAGINATION ==========

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    """Generic paginated response"""
    items: List[T]
    total: int
    page: int
    page_size: int
    total_pages: int
