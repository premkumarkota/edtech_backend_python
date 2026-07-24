"""
Admin Exam Management Routes.

CRUD for goal/exam templates and subject mapping.
"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_admin
from app.models.user import User
from app.models.syllabus import Syllabus
from app.models.study_planner_v2 import GoalExam, GoalExamSubject
from app.schemas.study_planner_v2 import (
    ExamCreateRequest, ExamUpdateRequest, ExamResponse,
    ExamSubjectResponse, ExamSubjectMapRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/", response_model=list[ExamResponse])
def list_exams(
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """List all exams (including inactive)."""
    exams = db.query(GoalExam).order_by(GoalExam.display_order, GoalExam.name).all()
    results = []
    for exam in exams:
        subjects = []
        for es in exam.subjects:
            syl = es.syllabus
            if syl:
                subjects.append(ExamSubjectResponse(
                    syllabus_id=syl.id, title=syl.title,
                    is_required=es.is_required, weightage=es.weightage,
                ))
        results.append(ExamResponse(
            id=exam.id, name=exam.name, code=exam.code,
            description=exam.description, icon_url=exam.icon_url,
            category_id=exam.category_id, is_active=exam.is_active,
            display_order=exam.display_order, subjects=subjects,
        ))
    return results


@router.post("/", response_model=ExamResponse)
def create_exam(
    payload: ExamCreateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Create a new exam template."""
    existing = db.query(GoalExam).filter(GoalExam.code == payload.code).first()
    if existing:
        raise HTTPException(400, f"Exam with code '{payload.code}' already exists")

    exam = GoalExam(
        name=payload.name,
        code=payload.code,
        description=payload.description,
        icon_url=payload.icon_url,
        category_id=payload.category_id,
        display_order=payload.display_order,
    )
    db.add(exam)
    db.commit()
    db.refresh(exam)

    return ExamResponse(
        id=exam.id, name=exam.name, code=exam.code,
        description=exam.description, icon_url=exam.icon_url,
        category_id=exam.category_id, is_active=exam.is_active,
        display_order=exam.display_order, subjects=[],
    )


@router.put("/{exam_id}", response_model=ExamResponse)
def update_exam(
    exam_id: int,
    payload: ExamUpdateRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Update an exam template."""
    exam = db.query(GoalExam).filter(GoalExam.id == exam_id).first()
    if not exam:
        raise HTTPException(404, "Exam not found")

    if payload.name is not None:
        exam.name = payload.name
    if payload.description is not None:
        exam.description = payload.description
    if payload.icon_url is not None:
        exam.icon_url = payload.icon_url
    if payload.is_active is not None:
        exam.is_active = payload.is_active
    if payload.display_order is not None:
        exam.display_order = payload.display_order

    db.commit()
    db.refresh(exam)

    subjects = []
    for es in exam.subjects:
        syl = es.syllabus
        if syl:
            subjects.append(ExamSubjectResponse(
                syllabus_id=syl.id, title=syl.title,
                is_required=es.is_required, weightage=es.weightage,
            ))

    return ExamResponse(
        id=exam.id, name=exam.name, code=exam.code,
        description=exam.description, icon_url=exam.icon_url,
        category_id=exam.category_id, is_active=exam.is_active,
        display_order=exam.display_order, subjects=subjects,
    )


@router.delete("/{exam_id}")
def archive_exam(
    exam_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Soft-delete (deactivate) an exam."""
    exam = db.query(GoalExam).filter(GoalExam.id == exam_id).first()
    if not exam:
        raise HTTPException(404, "Exam not found")

    exam.is_active = False
    db.commit()
    return {"message": "Exam archived"}


@router.post("/{exam_id}/subjects")
def add_subject_to_exam(
    exam_id: int,
    payload: ExamSubjectMapRequest,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Map a syllabus subject to an exam."""
    exam = db.query(GoalExam).filter(GoalExam.id == exam_id).first()
    if not exam:
        raise HTTPException(404, "Exam not found")

    syllabus = db.query(Syllabus).filter(Syllabus.id == payload.syllabus_id).first()
    if not syllabus:
        raise HTTPException(404, "Syllabus not found")

    existing = db.query(GoalExamSubject).filter(
        GoalExamSubject.exam_id == exam_id,
        GoalExamSubject.syllabus_id == payload.syllabus_id,
    ).first()
    if existing:
        existing.is_required = payload.is_required
        existing.weightage = payload.weightage
    else:
        db.add(GoalExamSubject(
            exam_id=exam_id,
            syllabus_id=payload.syllabus_id,
            is_required=payload.is_required,
            weightage=payload.weightage,
        ))

    db.commit()
    return {"message": "Subject mapped to exam"}


@router.delete("/{exam_id}/subjects/{syllabus_id}")
def remove_subject_from_exam(
    exam_id: int,
    syllabus_id: int,
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """Remove a subject mapping from an exam."""
    mapping = db.query(GoalExamSubject).filter(
        GoalExamSubject.exam_id == exam_id,
        GoalExamSubject.syllabus_id == syllabus_id,
    ).first()
    if not mapping:
        raise HTTPException(404, "Subject mapping not found")

    db.delete(mapping)
    db.commit()
    return {"message": "Subject removed from exam"}
