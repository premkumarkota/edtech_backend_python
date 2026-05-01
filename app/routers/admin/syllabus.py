from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func
from typing import List, Optional

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.syllabus import Syllabus, Chapter, SyllabusContent
from app.models.category import Category
from app.schemas.syllabus import (
    SyllabusSummary,
    SyllabusCreate,
    ChapterResponse,
    SyllabusContentResponse,
    ChapterBase,
    SyllabusDetailStudent,
    ChapterStudentResponse,
)
from app.schemas.syllabus_admin_requests import (
    ReorderChapterContentsRequest,
    SyllabusContentPatch,
)
from app.services.syllabus_layout import syllabus_to_detail_with_layout, chapter_to_layout_response
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

@router.get("/{syllabus_id}", response_model=SyllabusDetailStudent)
def get_syllabus_detail(
    syllabus_id: int,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """
    Same enriched shape as the student app (hero_video, document_contents,
    extra_videos) so the admin panel can preview Udemy-style grouping.
    """
    s = (
        db.query(Syllabus)
        .options(
            joinedload(Syllabus.chapters).joinedload(Chapter.contents),
        )
        .filter(Syllabus.id == syllabus_id)
        .first()
    )
    if not s:
        raise HTTPException(status_code=404, detail="Syllabus not found")
    return syllabus_to_detail_with_layout(s)

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
    order_index: Optional[int] = Form(default=None),
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

    placement = order_index
    if placement is None:
        mx = (
            db.query(func.max(SyllabusContent.order_index))
            .filter(SyllabusContent.chapter_id == chapter_id)
            .scalar()
        )
        placement = (mx if mx is not None else -1) + 1

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
        order_index=placement,
    )
    db.add(content)
    db.commit()
    db.refresh(content)
    return content

@router.patch("/content/{content_id}", response_model=SyllabusContentResponse)
def patch_content(
    content_id: int,
    payload: SyllabusContentPatch,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ct = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not ct:
        raise HTTPException(status_code=404, detail="Content not found")
    if payload.title is not None:
        t = payload.title.strip()
        if not t:
            raise HTTPException(status_code=400, detail="title cannot be empty")
        ct.title = t
    if payload.description is not None:
        ct.description = payload.description
    if payload.order_index is not None:
        ct.order_index = payload.order_index
    db.commit()
    db.refresh(ct)
    return ct


@router.put(
    "/chapters/{chapter_id}/contents/reorder",
    response_model=ChapterStudentResponse,
)
def reorder_chapter_contents(
    chapter_id: int,
    payload: ReorderChapterContentsRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    ch = db.query(Chapter).filter(Chapter.id == chapter_id).first()
    if not ch:
        raise HTTPException(status_code=404, detail="Chapter not found")

    ids = payload.ordered_content_ids
    if len(ids) != len(set(ids)):
        raise HTTPException(
            status_code=400, detail="ordered_content_ids must not contain duplicates"
        )
    by_id = {c.id: c for c in ch.contents}
    if set(ids) != set(by_id.keys()):
        raise HTTPException(
            status_code=400,
            detail=(
                "ordered_content_ids must list every content row in this chapter "
                "exactly once, in playback order."
            ),
        )

    for idx, cid in enumerate(ids):
        by_id[cid].order_index = idx
    db.commit()
    db.refresh(ch)
    for row in ch.contents:
        db.refresh(row)

    # Re-fetch for clean ordering (relationship collection may be unordered)
    ch2 = (
        db.query(Chapter)
        .options(joinedload(Chapter.contents))
        .filter(Chapter.id == chapter_id)
        .first()
    )
    return chapter_to_layout_response(ch2)


@router.delete("/content/{content_id}")
def delete_content(content_id: int, db: Session = Depends(get_db), admin: User = Depends(require_admin)):
    ct = db.query(SyllabusContent).filter(SyllabusContent.id == content_id).first()
    if not ct: raise HTTPException(404)
    db.delete(ct)
    db.commit()
    return {"message": "Deleted"}
