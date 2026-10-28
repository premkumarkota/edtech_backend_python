"""
Quiz Retake Service — fresh questions on every retake.

A quiz has one admin-authored question set, so without this every retake
showed the student the exact same questions. When a student starts a quiz
they have already completed, we ask the AI for a NEW set covering the same
concepts at the same difficulty, avoiding every question that student has
already seen (the quiz's own set + their earlier retake sets).

Generated questions are stored as `quiz_questions` rows owned by the attempt
(`attempt_id`), so they never show up in the admin's quiz, and
`quiz_attempts.question_ids` records the ordered set served. Each new question
takes the marks of the original it replaces, so total and pass marks are
unchanged. Any shortfall is topped up with original questions; if the AI is
unavailable the attempt simply uses the quiz's own set (question_ids NULL).
"""
import logging
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session

from app.models.quiz import Quiz, QuizQuestion, QuizAttempt, AttemptStatus
from app.services.ai_language import ENGLISH, TELUGU
from app.services.ai_quiz_service import (
    AIQuizError, _clean_questions, _norm_stem, _run_generation,
)

logger = logging.getLogger(__name__)

_BATCH_SIZE = 15          # questions per AI call (calls run in parallel)
_MAX_AVOID_IN_PROMPT = 80  # previously-seen stems listed in each prompt
_MAX_CONTENT_CHARS = 12000


def _contains_telugu(text: str) -> bool:
    return any("ఀ" <= ch <= "౿" for ch in text or "")


def _correct_text(q: QuizQuestion) -> str:
    return getattr(q, f"option_{(q.correct_option or 'a').lower()}", "") or ""


def has_completed_attempt(quiz_id: int, student_id: int, db: Session) -> bool:
    return db.query(QuizAttempt.id).filter(
        QuizAttempt.quiz_id == quiz_id,
        QuizAttempt.student_id == student_id,
        QuizAttempt.status == AttemptStatus.COMPLETED,
    ).first() is not None


def _seen_questions(quiz: Quiz, student_id: int, db: Session) -> list[QuizQuestion]:
    """The quiz's own questions + every retake question this student got before."""
    previous = (
        db.query(QuizQuestion)
        .join(QuizAttempt, QuizAttempt.id == QuizQuestion.attempt_id)
        .filter(QuizAttempt.quiz_id == quiz.id, QuizAttempt.student_id == student_id)
        .order_by(QuizQuestion.id.desc())
        .all()
    )
    return list(quiz.questions) + previous


def _prompt(quiz: Quiz, reference: list[QuizQuestion], avoid: list[str],
            content: str, part_note: str) -> str:
    n = len(reference)
    ref_block = "\n".join(
        f"{i}. {q.question_text}  [answer: {_correct_text(q)}]"
        for i, q in enumerate(reference, start=1)
    )
    avoid_block = "\n".join(f"- {s}" for s in avoid[:_MAX_AVOID_IN_PROMPT])
    content_block = (
        f"\n--- CHAPTER CONTENT (questions must stay within this) ---\n{content}\n"
        "--- CONTENT END ---\n" if content else ""
    )
    return (
        f"A student is RETAKING the quiz \"{quiz.title}\" and must get NEW questions.\n"
        f"{content_block}\n"
        f"REFERENCE QUESTIONS (the concepts and difficulty to cover — do NOT reuse them):\n"
        f"{ref_block}\n\n"
        f"ALREADY SEEN BY THIS STUDENT (do NOT repeat or lightly reword any of these):\n"
        f"{avoid_block}\n\n"
        f"Write exactly {n} NEW multiple-choice questions, one per reference question, "
        "testing the same concept at the same difficulty but with a different angle, "
        "scenario, example, or numbers.\n"
        f"{part_note}"
        "- Each question has 4 options (A-D) with exactly one correct answer.\n"
        "- 'correct_option' is A, B, C, or D. Vary the correct option across questions.\n"
        "- 'explanation' is 1-2 sentences on why the correct option is right.\n"
        f"- 'title' is \"{quiz.title}\"."
    )


def prepare_retake_questions(quiz: Quiz, attempt: QuizAttempt, db: Session) -> bool:
    """
    Generate fresh questions for `attempt` and record them in
    `attempt.question_ids`. Returns True if a fresh set was attached; False
    leaves the attempt on the quiz's own questions. Never raises.
    """
    base = sorted(quiz.questions, key=lambda q: (q.order_index or 0, q.id))
    if not base:
        return False

    seen = _seen_questions(quiz, attempt.student_id, db)
    seen_stems = {_norm_stem(q.question_text or "") for q in seen}
    avoid = [q.question_text for q in seen]
    language = TELUGU if any(_contains_telugu(q.question_text) for q in base) else ENGLISH

    content = ""
    if quiz.quiz_type == "chapter" and quiz.chapter:
        content = (quiz.chapter.live_description or "").strip()[:_MAX_CONTENT_CHARS]

    batches = [base[i:i + _BATCH_SIZE] for i in range(0, len(base), _BATCH_SIZE)]

    def run(i: int) -> list[dict]:
        note = (f"(This is part {i + 1} of {len(batches)}; only cover the reference "
                "questions listed above.)\n") if len(batches) > 1 else ""
        try:
            return _run_generation(
                _prompt(quiz, batches[i], avoid, content, note),
                1, quiz.title, language=language,
            )["questions"]
        except AIQuizError as e:
            logger.warning("Retake generation failed for quiz %s batch %s: %s", quiz.id, i, e)
            return []
        except Exception:
            logger.exception("Retake generation crashed for quiz %s batch %s", quiz.id, i)
            return []

    with ThreadPoolExecutor(max_workers=min(len(batches), 4)) as pool:
        results = list(pool.map(run, range(len(batches))))

    served_ids: list[int] = []
    fresh_count = 0
    for batch, generated in zip(batches, results):
        # `seen_stems` is shared, so duplicates of earlier questions (and across
        # batches) are dropped here.
        fresh = _clean_questions(generated, seen_stems)[: len(batch)]
        for original, q in zip(batch, fresh):
            row = QuizQuestion(
                quiz_id=quiz.id,
                attempt_id=attempt.id,
                question_text=q["question_text"],
                option_a=q["option_a"],
                option_b=q["option_b"],
                option_c=q["option_c"],
                option_d=q["option_d"],
                correct_option=q["correct_option"],
                explanation=q.get("explanation"),
                marks=original.marks,
                order_index=original.order_index,
            )
            db.add(row)
            db.flush()
            served_ids.append(row.id)
        fresh_count += len(fresh)
        # Short batch → keep the originals it couldn't replace, so the
        # question count and total marks stay the same.
        served_ids.extend(q.id for q in batch[len(fresh):])

    if fresh_count == 0:
        logger.warning("Retake for quiz %s fell back to the original questions", quiz.id)
        return False

    attempt.question_ids = served_ids
    db.flush()
    logger.info("Retake for quiz %s attempt %s: %s/%s fresh questions",
                quiz.id, attempt.id, fresh_count, len(base))
    return True


def attempt_questions(attempt: QuizAttempt, db: Session) -> list[QuizQuestion]:
    """Questions served for this attempt, in order."""
    ids = attempt.question_ids or []
    if not ids:
        return list(attempt.quiz.questions)
    rows = {q.id: q for q in db.query(QuizQuestion).filter(QuizQuestion.id.in_(ids)).all()}
    served = [rows[i] for i in ids if i in rows]
    return served or list(attempt.quiz.questions)


def scoring_questions(attempt: QuizAttempt, db: Session) -> list[QuizQuestion]:
    """
    The set to grade against. Older app builds load questions from
    GET /quiz/{id} (the quiz's own set) even when a retake set exists, so if
    the answers belong to the quiz's own questions, grade those instead.
    """
    served = attempt_questions(attempt, db)
    if not attempt.question_ids or not attempt.answers:
        return served
    served_ids = {q.id for q in served}
    if any(a.question_id in served_ids for a in attempt.answers):
        return served
    return list(attempt.quiz.questions)
