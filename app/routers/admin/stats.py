from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func, and_
from datetime import datetime, timedelta, timezone

from app.database import get_db
from app.dependencies import get_current_admin
from app.models.user import User, UserRole
from app.models.teacher_profile import TeacherProfile, TeacherStatus
from app.models.subscription import StudentSubscription, SubscriptionPlan, SubscriptionStatus
from app.models.session import VideoCallSession, SessionStatus
from app.models.payout import TeacherEarning, TeacherPayout
from app.schemas.stats import (
    AdminStatsResponse, UserStats, RevenueStats, RevenuByPlanItem,
    SubscriptionStats, SessionStats, PayoutStats,
)

router = APIRouter()


@router.get("", response_model=AdminStatsResponse)
def get_admin_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_admin),
):
    """
    Full admin dashboard stats in one call.
    Covers users, revenue, subscriptions, sessions, and payout summary.
    """
    now = datetime.now(timezone.utc)
    start_of_today  = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start_of_week   = start_of_today - timedelta(days=now.weekday())
    start_of_month  = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end_of_week     = start_of_today + timedelta(days=7)

    # ── USER STATS ────────────────────────────────────────────────────────────
    total_students = db.query(func.count(User.id)).filter(
        User.role == UserRole.STUDENT
    ).scalar() or 0

    total_teachers = db.query(func.count(User.id)).filter(
        User.role == UserRole.TEACHER
    ).scalar() or 0

    active_students = db.query(func.count(User.id)).filter(
        User.role == UserRole.STUDENT, User.is_active == True
    ).scalar() or 0

    pending_approvals = db.query(func.count(TeacherProfile.id)).filter(
        TeacherProfile.status == TeacherStatus.PENDING
    ).scalar() or 0

    # ── REVENUE STATS ─────────────────────────────────────────────────────────
    # Only count ACTIVE subscriptions (payment successfully captured)
    def revenue_query(from_dt=None):
        q = db.query(func.coalesce(func.sum(StudentSubscription.amount_paid), 0)).filter(
            StudentSubscription.status == SubscriptionStatus.ACTIVE,
            StudentSubscription.amount_paid.isnot(None),
        )
        if from_dt:
            q = q.filter(StudentSubscription.started_at >= from_dt)
        return float(q.scalar() or 0)

    total_revenue     = revenue_query()
    revenue_month     = revenue_query(start_of_month)
    revenue_week      = revenue_query(start_of_week)
    revenue_today     = revenue_query(start_of_today)

    # Revenue breakdown by plan
    plan_rows = (
        db.query(
            SubscriptionPlan.name,
            func.count(StudentSubscription.id).label("count"),
            func.coalesce(func.sum(StudentSubscription.amount_paid), 0).label("revenue"),
        )
        .join(StudentSubscription, StudentSubscription.plan_id == SubscriptionPlan.id)
        .filter(StudentSubscription.status == SubscriptionStatus.ACTIVE)
        .group_by(SubscriptionPlan.name)
        .all()
    )
    by_plan = [
        RevenuByPlanItem(plan_name=r.name, count=r.count, revenue=float(r.revenue))
        for r in plan_rows
    ]

    # ── SUBSCRIPTION STATS ────────────────────────────────────────────────────
    total_active = db.query(func.count(StudentSubscription.id)).filter(
        StudentSubscription.status == SubscriptionStatus.ACTIVE
    ).scalar() or 0

    total_expired = db.query(func.count(StudentSubscription.id)).filter(
        StudentSubscription.status == SubscriptionStatus.EXPIRED
    ).scalar() or 0

    expiring_this_week = db.query(func.count(StudentSubscription.id)).filter(
        StudentSubscription.status == SubscriptionStatus.ACTIVE,
        StudentSubscription.expires_at >= now,
        StudentSubscription.expires_at <= end_of_week,
    ).scalar() or 0

    # ── SESSION STATS ─────────────────────────────────────────────────────────
    total_completed = db.query(func.count(VideoCallSession.id)).filter(
        VideoCallSession.status == SessionStatus.COMPLETED
    ).scalar() or 0

    sessions_this_month = db.query(func.count(VideoCallSession.id)).filter(
        VideoCallSession.status == SessionStatus.COMPLETED,
        VideoCallSession.ended_at >= start_of_month,
    ).scalar() or 0

    total_minutes = db.query(
        func.coalesce(func.sum(VideoCallSession.actual_duration_mins), 0)
    ).filter(VideoCallSession.status == SessionStatus.COMPLETED).scalar() or 0

    # ── PAYOUT STATS ──────────────────────────────────────────────────────────
    pending_earnings = float(
        db.query(func.coalesce(func.sum(TeacherEarning.gross_earning), 0)).filter(
            TeacherEarning.payout_status == "pending"
        ).scalar() or 0
    )

    paid_all_time = float(
        db.query(func.coalesce(func.sum(TeacherEarning.gross_earning), 0)).filter(
            TeacherEarning.payout_status == "paid"
        ).scalar() or 0
    )

    teachers_with_pending = db.query(func.count(func.distinct(TeacherEarning.teacher_id))).filter(
        TeacherEarning.payout_status == "pending"
    ).scalar() or 0

    # ── ASSEMBLE RESPONSE ─────────────────────────────────────────────────────
    return AdminStatsResponse(
        users=UserStats(
            total_students=total_students,
            total_teachers=total_teachers,
            active_students=active_students,
            pending_teacher_approvals=pending_approvals,
        ),
        revenue=RevenueStats(
            total_all_time=total_revenue,
            this_month=revenue_month,
            this_week=revenue_week,
            today=revenue_today,
            by_plan=by_plan,
        ),
        subscriptions=SubscriptionStats(
            total_active=total_active,
            total_expired=total_expired,
            expiring_this_week=expiring_this_week,
        ),
        sessions=SessionStats(
            total_completed=total_completed,
            this_month=sessions_this_month,
            total_minutes_used=int(total_minutes),
        ),
        payouts=PayoutStats(
            total_pending_earnings=pending_earnings,
            total_paid_all_time=paid_all_time,
            teachers_with_pending=teachers_with_pending,
        ),
    )
