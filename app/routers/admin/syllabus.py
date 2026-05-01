from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
from sqlalchemy import func
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
from app.models.category import Category
from app.schemas.syllabus import (
    SyllabusSummary, SyllabusDetail, SyllabusCreate,
    ChapterResponse, SyllabusContentResponse, ChapterBase
)
from app.services.storage_service import (
    upload_file,
    ALLOWED_DOCUMENT_TYPES,
    ALLOWED_VIDEO_TYPES,
    SYLLABUS_CONTENT_MAX_DOC_MB,
    SYLLABUS_CONTENT_MAX_VIDEO_MB,
)

router = APIRouter()

# --- Syllabus (Subject/Course) ---
@router.post("", response_model=SyllabusSummary)
async def create_syllabus(
    category_id: int = Form(...),
    title: str = Form(...),
    description: Optional[str] = Form(None),
    thumbnail: Optional[UploadFile] = File(None),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    thumbnail_url = None
    if thumbnail:
        thumbnail_url = await upload_file(thumbnail, folder=f"syllabus/thumbnails")

    syllabus = Syllabus(
        category_id=category_id,
        title=title,
        description=description,
        thumbnail_url=thumbnail_url
    )
    db.add(syllabus)
    db.commit()
    db.refresh(syllabus)
    return syllabus

@router.get("", response_model=List[SyllabusSummary])
def list_syllabus(
    category_id: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    q = db.query(Syllabus)
    if category_id:
        q = q.filter(Syllabus.category_id == category_id)
    
    # We want to add chapter count
    results = q.order_by(Syllabus.created_at.desc()).all()
    for s in results:
        s.chapter_count = db.query(Chapter).filter(Chapter.syllabus_id == s.id).count()
    return results

@router.get("/{syllabus_id}", response_model=SyllabusDetail)
def get_syllabus_detail(
    syllabus_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    s = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    return s

@router.delete("/{syllabus_id}")
def delete_syllabus(syllabus_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    s = db.query(Syllabus).filter(Syllabus.id == syllabus_id).first()
    if not s: raise HTTPException(404)
    db.delete(s)
    db.commit()
    return {"message": "Deleted"}

# --- Chapters ---
@router.post("/{syllabus_id}/chapters", response_model=ChapterResponse)
def create_chapter(
    syllabus_id: int,
    payload: ChapterBase,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    chapter = Chapter(
        syllabus_id=syllabus_id,
        title=payload.title,
        description=payload.description,
        order_index=payload.order_index
    )
    db.add(chapter)
    db.commit()
    db.refresh(chapter)
    return chapter

@router.delete("/chapters/{chapter_id}")
def delete_chapter(chapter_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c: raise HTTPException(404)
    db.delete(c)
    db.commit()
    return {"message": "Deleted"}

# --- Content ---
@router.post("/chapters/{chapter_id}/content", response_model=SyllabusContentResponse)
async def create_content(
    chapter_id: int,
    title: str = Form(...),
    description: Optional[str] = Form(None),
    content_type: str = Form(...), # video, pdf, ppt
    file: UploadFile = File(...),
    order_index: int = Form(0),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin)
):
    c = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not c:
        raise HTTPException(status_code=404, detail="Chapter not found")

    ct = (content_type or "").strip().lower()
    if ct == "video":
        allowed = ALLOWED_VIDEO_TYPES
        max_mb = SYLLABUS_CONTENT_MAX_VIDEO_MB
    elif ct in ("pdf", "ppt", "pptx", "document", "deck"):
        # Store as pdf / ppt in DB — map generic labels to document MIME whitelist
        if ct == "pptx":
            ct = "ppt"
        if ct == "document":
            ct = "pdf"
        if ct == "deck":
            ct = "ppt"
        allowed = ALLOWED_DOCUMENT_TYPES
        max_mb = SYLLABUS_CONTENT_MAX_DOC_MB
    else:
        raise HTTPException(
            status_code=400,
            detail="content_type must be one of: video, pdf, ppt",
        )

    # Upload file (typed allowlist + syllabus-specific size caps)
    file_url = await upload_file(
        file,
        folder=f"syllabus/content/{chapter_id}",
        allowed_types=allowed,
        max_size_mb=max_mb,
    )
    
    content = SyllabusContent(
        chapter_id=chapter_id,
        title=title,
        description=description,
        content_type=ct,
        file_url=file_url,
        order_index=order_index
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content

@router.delete("/content/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    ct = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not ct: raise HTTPException(404)
    db.delete(ct)
    db.commit()
    return {"message": "Deleted"}
