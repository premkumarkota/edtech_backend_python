"""
Study MCQ Service — AI-generated MCQs with caching.

Generates 10 MCQs per topic via LLM, caches in study_mcq_banks,
and handles scoring with pass/fail logic.
"""
import logging
from typing import Literal, Optional

McqSource = Literal["cache", "quiz_bank", "llm"]

from sqlalchemy.orm import Session

from app.models.syllabus import Chapter
from app.models.quiz import Quiz, QuizQuestion
from app.models.study_planner_v2 import (
    StudyCalendarEntry, StudyMcqBank, StudyMcqAttempt,
)
from app.services.ai_llm_service import chat_completion_json

logger = logging.getLogger(__name__)

_MCQ_SYSTEM_PROMPT = """Generate exactly 10 multiple-choice questions for the given topic.

Output valid JSON:
{
  "questions": [
    {
      "question": "...",
      "options": ["A) ...", "B) ...", "C) ...", "D) ..."],
      "correct": "A",
      "explanation": "Brief explanation why the answer is correct",
      "difficulty": "easy"
    }
  ]
}

Rules:
- Generate EXACTLY 10 questions
- Mix difficulties: 3 easy, 4 medium, 3 hard
- Questions must be factually accurate and relevant to the topic
- Each question has exactly 4 options labeled A, B, C, D
- The correct field should be just the letter: "A", "B", "C", or "D"
- Explanations should be 1-2 sentences
- Questions should test understanding, not just recall"""


def _try_quiz_bank_fallback(chapter_id: int, db: Session) -> Optional[list[dict]]:
    """
    Try to pull questions from the existing quiz bank for this chapter.
    Returns list of question dicts if enough questions exist.
    """
    if not chapter_id:
        return None

    questions = (
        db.query(QuizQuestion)
        .join(Quiz, Quiz.id == QuizQuestion.quiz_id)
        .filter(Quiz.chapter_id == chapter_id, Quiz.status == "published")
        .limit(10)
        .all()
    )

    if len(questions) < 5:
        return None

    result = []
    for i, q in enumerate(questions):
        options = []
        if q.option_a:
            options.append(f"A) {q.option_a}")
        if q.option_b:
            options.append(f"B) {q.option_b}")
        if q.option_c:
            options.append(f"C) {q.option_c}")
        if q.option_d:
            options.append(f"D) {q.option_d}")

        result.append({
            "question": q.question_text,
            "options": options,
            "correct": q.correct_option,
            "explanation": q.explanation or "",
            "difficulty": "medium",
        })

    return result


def get_or_generate_mcq(
    entry: StudyCalendarEntry,
    db: Session,
) -> tuple[Optional[StudyMcqBank], Optional[McqSource | str]]:
    """
    Get cached MCQs or generate new ones for a calendar entry's topic.

    Returns (bank, source) on success where source is ``cache``, ``quiz_bank``,
    or ``llm``. On LLM failure returns ``(None, "llm_unavailable")``.
    """
    # Check cache first
    cached = db.query(StudyMcqBank).filter(
        StudyMcqBank.chapter_id == entry.chapter_id,
        StudyMcqBank.topic_title == entry.topic_title,
    ).first()

    if cached and isinstance(cached.questions, list) and len(cached.questions) >= 5:
        return cached, "cache"

    # A subtopic session must get questions scoped to THAT subtopic. The published
    # chapter quiz isn't subtopic-aware and would hand every session of the chapter
    # the same 10 questions, so we skip that fallback for subtopic sessions and let
    # the LLM generate against the subtopic. Whole-chapter sessions may still reuse
    # the chapter quiz bank.
    is_subtopic = bool(getattr(entry, "subtopic_title", None))

    fallback_questions = None if is_subtopic else _try_quiz_bank_fallback(entry.chapter_id, db)
    source: McqSource = "quiz_bank"

    if not fallback_questions:
        source = "llm"
        chapter = db.query(Chapter).filter(Chapter.id == entry.chapter_id).first() if entry.chapter_id else None

        if is_subtopic:
            # Focus strictly on this subtopic within the chapter.
            topic_context = f"Sub-topic: {entry.subtopic_title}"
            if entry.chapter_name:
                topic_context += f"\nChapter: {entry.chapter_name}"
            if chapter and chapter.description:
                topic_context += f"\n\nChapter reference (for context only):\n{chapter.description}"
            if entry.subject_name:
                topic_context = f"Subject: {entry.subject_name}\n{topic_context}"
            scope_instruction = (
                f"Generate 10 MCQ questions ONLY about the sub-topic "
                f"\"{entry.subtopic_title}\". Do not ask about other parts of the chapter.\n\n"
            )
        else:
            topic_context = entry.topic_title
            if chapter and chapter.description:
                topic_context += f"\n\nChapter description: {chapter.description}"
            if entry.subject_name:
                topic_context = f"Subject: {entry.subject_name}\nTopic: {topic_context}"
            scope_instruction = "Generate 10 MCQ questions for:\n"

        messages = [
            {"role": "system", "content": _MCQ_SYSTEM_PROMPT},
            {"role": "user", "content": f"{scope_instruction}{topic_context}"},
        ]

        result = chat_completion_json(messages=messages, temperature=0.5, max_tokens=3000)

        if result and "questions" in result:
            fallback_questions = result["questions"]
        else:
            logger.error("LLM MCQ generation failed for topic: %s", entry.topic_title)
            return None, "llm_unavailable"

    # Save to cache
    if cached:
        cached.questions = fallback_questions
        db.flush()
        return cached, source

    mcq_bank = StudyMcqBank(
        chapter_id=entry.chapter_id,
        topic_title=entry.topic_title,
        questions=fallback_questions,
    )
    db.add(mcq_bank)
    db.flush()
    return mcq_bank, source


def score_mcq_attempt(
    mcq_bank: StudyMcqBank,
    answers: list[dict],
) -> tuple[float, list[dict]]:
    """
    Score an MCQ attempt.
    Returns (score_percentage, detailed_results).
    """
    questions = mcq_bank.questions or []
    if not questions:
        return 0.0, []

    results = []
    correct_count = 0

    # Index answers by question_index
    answer_map = {a["question_index"]: a["selected"].upper() for a in answers}

    for i, q in enumerate(questions):
        selected = answer_map.get(i, "")
        correct = q.get("correct", "").upper()
        is_correct = selected == correct

        if is_correct:
            correct_count += 1

        results.append({
            "question_index": i,
            "question": q.get("question", ""),
            "selected": selected,
            "correct": correct,
            "is_correct": is_correct,
            "explanation": q.get("explanation", ""),
        })

    score = round(correct_count / len(questions) * 100, 1)
    return score, results
