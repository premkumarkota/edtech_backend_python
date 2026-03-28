from pydantic import BaseModel
from typing import List


class RevenuByPlanItem(BaseModel):
    plan_name: str
    count: int
    revenue: float


class RevenueStats(BaseModel):
    total_all_time: float
    this_month: float
    this_week: float
    today: float
    by_plan: List[RevenuByPlanItem]


class UserStats(BaseModel):
    total_students: int
    total_teachers: int
    active_students: int
    pending_teacher_approvals: int


class SubscriptionStats(BaseModel):
    total_active: int
    total_expired: int
    expiring_this_week: int


class SessionStats(BaseModel):
    total_completed: int
    this_month: int
    total_minutes_used: int


class PayoutStats(BaseModel):
    total_pending_earnings: float
    total_paid_all_time: float
    teachers_with_pending: int


class AdminStatsResponse(BaseModel):
    """Full admin dashboard stats — one call, all numbers."""
    users: UserStats
    revenue: RevenueStats
    subscriptions: SubscriptionStats
    sessions: SessionStats
    payouts: PayoutStats
