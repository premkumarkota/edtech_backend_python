"""
Single source of truth for a quiz's total / pass marks.

Any admin action that changes a quiz's questions (AI refine, Excel re-upload,
editing a question's marks, deleting a question) must call `recalc_marks` so
`pass_marks` keeps the same pass PERCENTAGE. Previously only `total_marks`
was updated, which left e.g. pass 5 of total 3 (an impossible 167% quiz).
"""
from __future__ import annotations

DEFAULT_PASS_RATIO = 0.6


def recalc_marks(quiz, new_total: int) -> None:
    old_total = int(quiz.total_marks or 0)
    old_pass = int(quiz.pass_marks or 0)
    if old_total > 0 and 0 < old_pass <= old_total:
        ratio = old_pass / old_total
    elif old_pass > 0 and old_total > 0:          # already broken (pass > total)
        ratio = DEFAULT_PASS_RATIO
    else:
        ratio = DEFAULT_PASS_RATIO if old_pass > 0 else 0.0
    quiz.total_marks = int(new_total)
    if new_total <= 0:
        quiz.pass_marks = 0
    elif ratio > 0:
        quiz.pass_marks = max(1, min(int(new_total), round(ratio * new_total)))
    else:
        quiz.pass_marks = 0


def effective_pass_marks(quiz, total: int | None = None) -> int:
    """Pass mark never above what's achievable (heals legacy bad rows)."""
    t = int(total if total is not None else (quiz.total_marks or 0))
    p = int(quiz.pass_marks or 0)
    return min(p, t) if t > 0 else p


def pass_percentage(quiz) -> float | None:
    t = int(quiz.total_marks or 0)
    if t <= 0:
        return None
    return round(effective_pass_marks(quiz) / t * 100, 1)


def chapter_quiz_passed(quiz, prog) -> bool:
    """Stored flag OR best score already meets the (clamped) pass mark —
    self-heals attempts scored against a broken pass_marks > total."""
    if not prog:
        return False
    if prog.quiz_passed:
        return True
    pct = pass_percentage(quiz) if quiz is not None else None
    best = prog.quiz_best_percentage
    return pct is not None and best is not None and best >= pct
