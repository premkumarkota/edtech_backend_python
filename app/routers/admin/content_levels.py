"""
Admin-managed content levels for AI generation.

GET    /api/admin/content-levels           list (ordered)
POST   /api/admin/content-levels           create
PATCH  /api/admin/content-levels/{id}      update name / guidance / order / active
DELETE /api/admin/content-levels/{id}      delete
"""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.content_level import ContentLevel
from app.models.user import User

router = APIRouter()


class ContentLevelIn(BaseModel):
    name: str
    ai_guidance: Optional[str] = None
    order_index: Optional[int] = None
    is_active: bool = True


class ContentLevelPatch(BaseModel):
    name: Optional[str] = None
    ai_guidance: Optional[str] = None
    order_index: Optional[int] = None
    is_active: Optional[bool] = None


class ContentLevelOut(BaseModel):
    id: int
    name: str
    ai_guidance: Optional[str] = None
    order_index: int
    is_active: bool

    class Config:
        from_attributes = True


def _clean_name(name: Optional[str]) -> str:
    n = (name or "").strip()
    if not n:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Level name is required")
    return n[:100]


def _ensure_unique(db: Session, name: str, exclude_id: Optional[int] = None):
    q = db.query(ContentLevel).filter(func.lower(ContentLevel.name) == name.lower())
    if exclude_id is not None:
        q = q.filter(ContentLevel.id != exclude_id)
    if q.first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f'Level "{name}" already exists')


def level_prompt_text(db: Session, level_name: Optional[str]) -> str:
    """'Name — guidance' for the AI prompt; falls back to the raw name."""
    name = (level_name or "").strip() or "General"
    row = db.query(ContentLevel).filter(func.lower(ContentLevel.name) == name.lower()).first()
    if row and (row.ai_guidance or "").strip():
        return f"{row.name} — {row.ai_guidance.strip()}"
    return name


@router.get("", response_model=List[ContentLevelOut])
def list_levels(
    include_inactive: bool = True,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    q = db.query(ContentLevel)
    if not include_inactive:
        q = q.filter(ContentLevel.is_active == True)  # noqa: E712
    return q.order_by(ContentLevel.order_index.asc(), ContentLevel.id.asc()).all()


@router.post("", response_model=ContentLevelOut, status_code=status.HTTP_201_CREATED)
def create_level(
    body: ContentLevelIn,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    name = _clean_name(body.name)
    _ensure_unique(db, name)
    order = body.order_index
    if order is None:
        order = (db.query(func.max(ContentLevel.order_index)).scalar() or 0) + 1
    row = ContentLevel(
        name=name,
        ai_guidance=(body.ai_guidance or "").strip() or None,
        order_index=order,
        is_active=body.is_active,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/{level_id}", response_model=ContentLevelOut)
def update_level(
    level_id: int,
    body: ContentLevelPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    row = db.query(ContentLevel).filter(ContentLevel.id == level_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Level not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data:
        name = _clean_name(data["name"])
        _ensure_unique(db, name, exclude_id=row.id)
        row.name = name
    if "ai_guidance" in data:
        row.ai_guidance = (data["ai_guidance"] or "").strip() or None
    if data.get("order_index") is not None:
        row.order_index = data["order_index"]
    if data.get("is_active") is not None:
        row.is_active = data["is_active"]
    db.commit()
    db.refresh(row)
    return row


@router.delete("/{level_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_level(
    level_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    row = db.query(ContentLevel).filter(ContentLevel.id == level_id).first()
    if not row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Level not found")
    db.delete(row)
    db.commit()
