"""
Learning Streak Service — the single source of truth for student streaks.

A "learning day" is an IST calendar day on which the student completed at least
one learning action: a quiz (chapter or mock), an AI-planner session, or reading
a chapter. Consecutive learning days build the streak.

Rules (BRD: Learning Streaks):
- First activity of a day adds +1; more activities that day add nothing.
- A missed day uses a freeze if one is held (max 2; one earned per 7 days).
  With no freeze the streak breaks (current → 0, best is kept).
- A broken streak can be repaired on the day it broke by completing
  REPAIR_ACTIVITIES actions, at most once every REPAIR_COOLDOWN_DAYS.
- Milestones (3, 7, 14, 30, 50, 100) pay bonus points once each time the
  streak crosses them; some also award a badge.

All writes go through `record_learning_activity` / `settle_missed_days` under a
row lock, so concurrent requests and multiple Cloud Run instances can't
double-count. Callers commit.
"""
import logging
from datetime import datetime, timedelta, timezone, date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.student_profile import StudentProfile
from app.models.study_planner_v2 import StudyStreak, StudyBadge, StudentLearningDay
from app.models.user import User

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)

MAX_FREEZES = 2
FREEZE_EVERY_DAYS = 7
REPAIR_ACTIVITIES = 2
REPAIR_COOLDOWN_DAYS = 30
HISTORY_DAYS = 35

# (min days, key, display name)
LEVELS = [
    (30, "legend", "Legend"),
    (14, "inferno", "Inferno"),
    (7, "blaze", "Blaze"),
    (3, "flame", "Flame"),
    (1, "spark", "Spark"),
    (0, "ember", "Ember"),
]

# days → (bonus points, badge_type, badge_name) — badge types match the
# existing planner streak badges so nobody gets the same badge twice.
MILESTONES = {
    3: (50, None, None),
    7: (100, "streak_7", "Week Warrior"),
    14: (200, "streak_14", "Inferno"),
    30: (500, "streak_30", "Monthly Master"),
    50: (750, "streak_50", "Unstoppable"),
    100: (1500, "streak_100", "Century Club"),
}

SOURCES = {"quiz", "mock_test", "planner_session", "chapter_read"}


# ── helpers ──────────────────────────────────────────────────────────────────

def today_ist(now: Optional[datetime] = None) -> date:
    return ((now or datetime.now(timezone.utc)) + IST_OFFSET).date()


def seconds_left_today(now: Optional[datetime] = None) -> int:
    now = now or datetime.now(timezone.utc)
    ist = now + IST_OFFSET
    midnight = datetime.combine(ist.date() + timedelta(days=1), datetime.min.time())
    return max(0, int((midnight - ist.replace(tzinfo=None)).total_seconds()))


def level_for(days: int) -> dict:
    for min_days, key, name in LEVELS:
        if days >= min_days:
            return {"key": key, "name": name, "min_days": min_days}
    return {"key": "ember", "name": "Ember", "min_days": 0}


def next_level_for(days: int) -> Optional[dict]:
    for min_days, key, name in reversed(LEVELS):
        if min_days > days:
            return {"key": key, "name": name, "min_days": min_days,
                    "days_to_go": min_days - days}
    return None


def _get_streak(student_id: int, db: Session, lock: bool = True) -> StudyStreak:
    q = db.query(StudyStreak).filter(StudyStreak.student_id == student_id)
    streak = (q.with_for_update() if lock else q).first()
    if not streak:
        streak = StudyStreak(student_id=student_id, current_streak=0, longest_streak=0,
                             freezes=0, broken_streak=0)
        db.add(streak)
        db.flush()
    return streak


def _day_row(student_id: int, day: date, db: Session) -> StudentLearningDay:
    row = db.query(StudentLearningDay).filter(
        StudentLearningDay.student_id == student_id,
        StudentLearningDay.day == day,
    ).first()
    if not row:
        row = StudentLearningDay(student_id=student_id, day=day, activities=0,
                                 sources=[], frozen=False)
        db.add(row)
        db.flush()
    return row


def _break(streak: StudyStreak, today: date) -> None:
    streak.broken_streak = streak.current_streak
    streak.broken_on = today
    streak.current_streak = 0


# ── core ─────────────────────────────────────────────────────────────────────

def settle_missed_days(streak: StudyStreak, db: Session, today: Optional[date] = None) -> int:
    """
    Walk the days between the last learning day and today. Each missed day
    spends a freeze; when freezes run out the streak breaks. Idempotent.
    Returns the number of freezes used.
    """
    today = today or today_ist()
    if not streak.current_streak or not streak.last_study_date:
        return 0
    used = 0
    d = streak.last_study_date + timedelta(days=1)
    while d < today:
        if (streak.freezes or 0) > 0:
            streak.freezes -= 1
            row = _day_row(streak.student_id, d, db)
            row.frozen = True
            streak.last_study_date = d
            used += 1
        else:
            _break(streak, today)
            break
        d += timedelta(days=1)
    streak.updated_at = datetime.now(timezone.utc)
    return used


def _award_milestone(student_id: int, days: int, db: Session) -> dict:
    points, badge_type, badge_name = MILESTONES[days]
    profile = db.query(StudentProfile).filter(StudentProfile.user_id == student_id).first()
    if profile:
        profile.total_points = (profile.total_points or 0) + points
    badge = None
    if badge_type:
        exists = db.query(StudyBadge.id).filter(
            StudyBadge.student_id == student_id, StudyBadge.badge_type == badge_type,
        ).first()
        if not exists:
            db.add(StudyBadge(student_id=student_id, badge_type=badge_type,
                              badge_name=badge_name, badge_icon=f"flame_{days}"))
            badge = badge_name
    return {"days": days, "points": points, "badge": badge}


def record_learning_activity(student_id: int, source: str, db: Session) -> dict:
    """
    Count one completed learning action for today and update the streak.
    Returns a celebration event for the client.
    """
    source = source if source in SOURCES else "quiz"
    today = today_ist()
    streak = _get_streak(student_id, db, lock=True)
    settle_missed_days(streak, db, today)

    row = _day_row(student_id, today, db)
    row.activities = (row.activities or 0) + 1
    row.sources = sorted(set(row.sources or []) | {source})

    before = streak.current_streak or 0
    old_best = streak.longest_streak or 0
    first_today = streak.last_study_date != today
    repaired = False

    if first_today:
        streak.current_streak = before + 1
        streak.last_study_date = today

    # Repair: the streak broke this morning and the student did enough today.
    cooldown_ok = (streak.repair_used_on is None
                   or (today - streak.repair_used_on).days >= REPAIR_COOLDOWN_DAYS)
    if (streak.broken_on == today and (streak.broken_streak or 0) > 0 and cooldown_ok
            and row.activities >= REPAIR_ACTIVITIES):
        before = streak.broken_streak
        streak.current_streak = streak.broken_streak + 1
        streak.broken_streak = 0
        streak.repair_used_on = today
        repaired = True

    after = streak.current_streak
    milestones = []
    freeze_earned = False
    if after > before:
        for m in sorted(MILESTONES):
            if before < m <= after:
                milestones.append(_award_milestone(student_id, m, db))
        if after // FREEZE_EVERY_DAYS > before // FREEZE_EVERY_DAYS and (streak.freezes or 0) < MAX_FREEZES:
            streak.freezes = (streak.freezes or 0) + 1
            freeze_earned = True

    streak.longest_streak = max(old_best, after)
    streak.updated_at = datetime.now(timezone.utc)
    db.flush()

    return {
        "first_today": first_today,
        "current_streak": after,
        "longest_streak": streak.longest_streak,
        "new_record": after > old_best and old_best > 0,
        "level": level_for(after),
        "level_up": level_for(after)["key"] != level_for(before)["key"],
        "milestone": milestones[-1] if milestones else None,
        "freeze_earned": freeze_earned,
        "freezes": streak.freezes,
        "repaired": repaired,
        "repair": _repair_state(streak, row, today),
    }


def safe_record(student_id: int, source: str, db: Session) -> Optional[dict]:
    """Record + commit, never breaking the caller (quiz submit etc.)."""
    try:
        event = record_learning_activity(student_id, source, db)
        db.commit()
        return event
    except Exception:
        db.rollback()
        logger.exception("Streak update failed for student %s (%s)", student_id, source)
        return None


def _repair_state(streak: StudyStreak, today_row: Optional[StudentLearningDay], today: date) -> dict:
    cooldown_ok = (streak.repair_used_on is None
                   or (today - streak.repair_used_on).days >= REPAIR_COOLDOWN_DAYS)
    available = streak.broken_on == today and (streak.broken_streak or 0) > 0 and cooldown_ok
    done = (today_row.activities if today_row else 0) or 0
    return {
        "available": available,
        "broken_streak": streak.broken_streak if available else 0,
        "activities_needed": max(0, REPAIR_ACTIVITIES - done) if available else 0,
    }


# ── read model ───────────────────────────────────────────────────────────────

def _display_name(name: Optional[str]) -> str:
    parts = (name or "Student").split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def leaderboard(student: User, db: Session, limit: int = 10) -> dict:
    """Top live streaks among students of the same board (category)."""
    yesterday = today_ist() - timedelta(days=1)
    q = (
        db.query(StudyStreak, User)
        .join(User, User.id == StudyStreak.student_id)
        .filter(StudyStreak.current_streak > 0, StudyStreak.last_study_date >= yesterday)
    )
    if student.category_id:
        q = q.filter(User.category_id == student.category_id)
    rows = q.order_by(StudyStreak.current_streak.desc(), StudyStreak.updated_at.asc()).limit(limit).all()
    top = [
        {"rank": i, "name": _display_name(u.name), "streak": s.current_streak,
         "level": level_for(s.current_streak)["key"], "is_me": u.id == student.id}
        for i, (s, u) in enumerate(rows, start=1)
    ]
    mine = next((r for r in top if r["is_me"]), None)
    my_rank = mine["rank"] if mine else None
    if my_rank is None:
        me = db.query(StudyStreak).filter(StudyStreak.student_id == student.id).first()
        if me and me.current_streak and me.last_study_date and me.last_study_date >= yesterday:
            ahead = db.query(func.count(StudyStreak.id)).join(User, User.id == StudyStreak.student_id).filter(
                StudyStreak.current_streak > me.current_streak,
                StudyStreak.last_study_date >= yesterday,
                *( [User.category_id == student.category_id] if student.category_id else [] ),
            ).scalar() or 0
            my_rank = ahead + 1
    return {"top": top, "my_rank": my_rank}


def streak_overview(student: User, db: Session) -> dict:
    """Everything the streak screen + home card need, in one payload."""
    today = today_ist()
    streak = _get_streak(student.id, db, lock=True)
    settle_missed_days(streak, db, today)
    db.commit()

    start = today - timedelta(days=HISTORY_DAYS - 1)
    rows = {
        r.day: r for r in db.query(StudentLearningDay).filter(
            StudentLearningDay.student_id == student.id,
            StudentLearningDay.day >= start,
        ).all()
    }
    history = []
    for i in range(HISTORY_DAYS):
        d = start + timedelta(days=i)
        r = rows.get(d)
        if r and r.activities:
            status = "learned"
        elif r and r.frozen:
            status = "frozen"
        elif d == today:
            status = "today"
        else:
            status = "missed"
        history.append({"date": d.isoformat(), "status": status,
                        "activities": (r.activities if r else 0) or 0})

    current = streak.current_streak or 0
    today_row = rows.get(today)
    next_m = next((m for m in sorted(MILESTONES) if m > current), None)
    prev_m = max([0] + [m for m in MILESTONES if m <= current])

    return {
        "current_streak": current,
        "longest_streak": streak.longest_streak or 0,
        "level": level_for(current),
        "next_level": next_level_for(current),
        "today_done": bool(today_row and today_row.activities),
        "today_activities": (today_row.activities if today_row else 0) or 0,
        "seconds_left_today": seconds_left_today(),
        "freezes": streak.freezes or 0,
        "max_freezes": MAX_FREEZES,
        "history": history,
        "milestones": [
            {"days": m, "points": p, "badge": b, "reached": current >= m}
            for m, (p, _t, b) in sorted(MILESTONES.items())
        ],
        "next_milestone": None if next_m is None else {
            "days": next_m,
            "points": MILESTONES[next_m][0],
            "badge": MILESTONES[next_m][2],
            "days_to_go": next_m - current,
            "progress": round((current - prev_m) / (next_m - prev_m), 3),
        },
        "repair": _repair_state(streak, today_row, today),
        "leaderboard": leaderboard(student, db),
    }


# ── jobs ─────────────────────────────────────────────────────────────────────

def nightly_settle_all(db: Session) -> int:
    """Run just after IST midnight: apply freezes / breaks for yesterday."""
    yesterday = today_ist() - timedelta(days=1)
    ids = [sid for (sid,) in db.query(StudyStreak.student_id).filter(
        StudyStreak.current_streak > 0, StudyStreak.last_study_date < yesterday,
    ).all()]
    for sid in ids:
        settle_missed_days(_get_streak(sid, db, lock=True), db)
    return len(ids)


def students_to_remind(db: Session) -> list[tuple[User, StudyStreak]]:
    """Live streak, nothing learned yet today, not reminded today."""
    today = today_ist()
    yesterday = today - timedelta(days=1)
    return (
        db.query(User, StudyStreak)
        .join(StudyStreak, StudyStreak.student_id == User.id)
        .filter(
            StudyStreak.current_streak > 0,
            StudyStreak.last_study_date == yesterday,
            (StudyStreak.last_reminder_on.is_(None)) | (StudyStreak.last_reminder_on < today),
            User.fcm_token.isnot(None),
        )
        .all()
    )


def admin_stats(db: Session) -> dict:
    today = today_ist()
    yesterday = today - timedelta(days=1)
    week_ago = today - timedelta(days=6)
    live = db.query(StudyStreak).filter(
        StudyStreak.current_streak > 0, StudyStreak.last_study_date >= yesterday,
    ).all()
    levels = {key: 0 for _, key, _ in LEVELS if key != "ember"}
    for s in live:
        levels[level_for(s.current_streak)["key"]] += 1
    streaks = sorted(s.current_streak for s in live)
    return {
        "active_today": db.query(func.count(StudentLearningDay.id)).filter(
            StudentLearningDay.day == today, StudentLearningDay.activities > 0).scalar() or 0,
        "active_last_7_days": db.query(func.count(func.distinct(StudentLearningDay.student_id))).filter(
            StudentLearningDay.day >= week_ago, StudentLearningDay.activities > 0).scalar() or 0,
        "students_with_streak": len(live),
        "average_streak": round(sum(streaks) / len(streaks), 1) if streaks else 0,
        "median_streak": streaks[len(streaks) // 2] if streaks else 0,
        "longest_live_streak": streaks[-1] if streaks else 0,
        "streak_3_plus": sum(1 for x in streaks if x >= 3),
        "streak_7_plus": sum(1 for x in streaks if x >= 7),
        "streak_30_plus": sum(1 for x in streaks if x >= 30),
        "levels": levels,
        "freezes_used_last_7_days": db.query(func.count(StudentLearningDay.id)).filter(
            StudentLearningDay.day >= week_ago, StudentLearningDay.frozen.is_(True)).scalar() or 0,
    }
