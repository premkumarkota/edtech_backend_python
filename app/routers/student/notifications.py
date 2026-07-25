"""
Student notification endpoints — list, read, mark-all-read, unread-count.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import desc

from app.database import get_db
from app.dependencies.auth import get_current_student
from app.models.user import User
from app.models.notification import Notification

router = APIRouter()

_VALID_CATEGORIES = {"sessions", "study_planner", "offers", "system"}


@router.get("")
def list_notifications(
    category: str | None = Query(None, description="Filter by category"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    q = db.query(Notification).filter(Notification.user_id == user.id)
    if category and category in _VALID_CATEGORIES:
        q = q.filter(Notification.category == category)
    total = q.count()
    items = (
        q.order_by(desc(Notification.created_at))
        .offset(offset)
        .limit(limit)
        .all()
    )
    return {
        "total": total,
        "items": [
            {
                "id": n.id,
                "title": n.title,
                "body": n.body,
                "type": n.notif_type,
                "category": n.category,
                "data": n.data,
                "is_read": n.is_read,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in items
        ],
    }


@router.get("/unread-count")
def unread_count(
    user: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    count = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .count()
    )
    return {"unread_count": count}


@router.patch("/{notification_id}/read")
def mark_read(
    notification_id: int,
    user: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    n = (
        db.query(Notification)
        .filter(Notification.id == notification_id, Notification.user_id == user.id)
        .first()
    )
    if not n:
        return {"detail": "Not found"}
    n.is_read = True
    db.commit()
    return {"message": "Marked as read"}


@router.post("/mark-all-read")
def mark_all_read(
    user: User = Depends(get_current_student),
    db: Session = Depends(get_db),
):
    updated = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read == False)
        .update({"is_read": True})
    )
    db.commit()
    return {"message": f"Marked {updated} notifications as read"}
