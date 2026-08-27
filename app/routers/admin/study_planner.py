"""
Admin — Study Planner maintenance.

One-time / occasional backfill so goals created before per-subtopic sessions
existed get their PENDING chapters re-split into distinct subtopics (each with
its own content focus and its own quiz).

Regeneration is progress-safe: `generate_plan_for_goal` deletes only PENDING
calendar entries and keeps COMPLETED / IN_PROGRESS ones, so students never lose
finished sessions or scores — only their upcoming schedule is re-split.

POST /api/admin/study-planner/backfill-subtopics
    Query params:
      goal_id     optional int  — restrict to a single goal
      only_active bool = true   — only goals with status == "active"
      dry_run     bool = false  — count candidates without regenerating
      limit       optional int  — cap how many goals are processed this call
"""

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import require_admin
from app.models.user import User
from app.models.study_planner_v2 import StudyGoal
from app.services.study_plan_engine import generate_plan_for_goal

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/backfill-subtopics")
def backfill_subtopics(
    goal_id: Optional[int] = None,
    only_active: bool = True,
    dry_run: bool = False,
    limit: Optional[int] = None,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Regenerate goals so pending chapters are split into per-subtopic sessions."""
    q = db.query(StudyGoal)
    if goal_id is not None:
        q = q.filter(StudyGoal.id == goal_id)
    if only_active:
        q = q.filter(StudyGoal.status == "active")
    q = q.order_by(StudyGoal.id.asc())
    if limit is not None and limit > 0:
        q = q.limit(limit)

    goals = q.all()

    if dry_run:
        return {
            "dry_run": True,
            "candidate_goals": len(goals),
            "goal_ids": [g.id for g in goals],
        }

    results = []
    regenerated = errors = skipped = 0

    for goal in goals:
        try:
            result = generate_plan_for_goal(goal, db)
            db.commit()  # commit per goal so one failure never rolls back the rest
            status = result.get("status")
            if status in ("generated", "completed"):
                regenerated += 1
            else:
                skipped += 1
            results.append({
                "goal_id": goal.id,
                "student_id": goal.student_id,
                "status": status,
                "total_units": result.get("total_units"),
                "message": result.get("message"),
            })
        except Exception as e:  # noqa: BLE001 — never let one goal abort the batch
            db.rollback()
            errors += 1
            logger.exception("Backfill failed for goal %s", goal.id)
            results.append({
                "goal_id": goal.id,
                "student_id": goal.student_id,
                "status": "error",
                "message": str(e),
            })

    return {
        "dry_run": False,
        "processed": len(goals),
        "regenerated": regenerated,
        "skipped": skipped,
        "errors": errors,
        "results": results,
    }
