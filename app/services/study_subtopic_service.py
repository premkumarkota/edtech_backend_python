"""
Study Subtopic Service — split a chapter into N ordered subtopics.

The AI study planner gives each study session its own distinct subtopic so that
content, headings, and MCQ quizzes differ across the sessions of one chapter
(instead of repeating the whole chapter every time). Subtopics are AI-segmented
from the chapter's title/description and, where possible, mapped to the chapter's
uploaded content items. Results are cached per (chapter_id, session_count).

Returned subtopic shape:
    {"title": str, "summary": str, "content_ids": [int, ...]}
"""
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.study_planner_v2 import StudyChapterSubtopic
from app.services.ai_llm_service import chat_completion_json

logger = logging.getLogger(__name__)

_MAX_DESC_CHARS = 4000  # trim long chapter descriptions for the prompt

_SEG_SYSTEM_PROMPT = """You split a study chapter into a fixed number of ordered subtopics for a study plan.

Output valid JSON only:
{
  "subtopics": [
    {
      "title": "Short, specific subtopic name (max 6 words)",
      "summary": "One sentence on what this subtopic covers",
      "content_indexes": [0, 2]
    }
  ]
}

Rules:
- Produce EXACTLY the requested number of subtopics, in the natural teaching order.
- Titles must be distinct, specific parts of the chapter — NOT generic labels like
  "Part 1" or "Introduction/Practice/Revision".
- "content_indexes" lists the indexes (from the provided MATERIALS list) that best
  match that subtopic. Use [] if no material fits. Every index may be used at most once.
- If there are no materials, still return the subtopics with "content_indexes": []."""


def _trim(text: str, limit: int) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " …"


def _even_split(items: list, n: int) -> list[list]:
    """Split a list into n contiguous, roughly-equal, order-preserving groups."""
    if n <= 0:
        return []
    groups: list[list] = [[] for _ in range(n)]
    if not items:
        return groups
    for i, item in enumerate(items):
        groups[min(i * n // len(items), n - 1)].append(item)
    return groups


def _heuristic_subtopics(chapter_title: str, content_items: list[dict], n: int) -> list[dict]:
    """Fallback when the LLM is unavailable or returns something unusable.

    Splits by content items when present (title = first item's title), otherwise
    produces generic ordered parts. Always returns exactly n subtopics.
    """
    subtopics: list[dict] = []
    if content_items:
        groups = _even_split(content_items, n)
        for k, group in enumerate(groups, start=1):
            if group:
                title = group[0].get("title") or f"{chapter_title} — Part {k}"
            else:
                title = f"{chapter_title} — Part {k}"
            subtopics.append({
                "title": title[:200],
                "summary": "",
                "content_ids": [c["id"] for c in group],
            })
    else:
        for k in range(1, n + 1):
            subtopics.append({
                "title": f"{chapter_title} — Part {k}"[:200],
                "summary": "",
                "content_ids": [],
            })
    return subtopics


def _normalize(raw_subtopics: list, content_items: list[dict], chapter_title: str, n: int) -> list[dict]:
    """Coerce LLM output into exactly n well-formed subtopics with content_ids."""
    out: list[dict] = []
    used: set[int] = set()
    for st in (raw_subtopics or [])[:n]:
        if not isinstance(st, dict):
            continue
        title = (st.get("title") or "").strip()
        if not title:
            continue
        content_ids: list[int] = []
        for idx in st.get("content_indexes", []) or []:
            if isinstance(idx, int) and 0 <= idx < len(content_items) and idx not in used:
                used.add(idx)
                content_ids.append(content_items[idx]["id"])
        out.append({
            "title": title[:200],
            "summary": (st.get("summary") or "").strip()[:300],
            "content_ids": content_ids,
        })

    # Pad if the model returned too few.
    while len(out) < n:
        k = len(out) + 1
        out.append({"title": f"{chapter_title} — Part {k}"[:200], "summary": "", "content_ids": []})

    # Distribute any content items the model left unassigned across the subtopics
    # so nothing in the chapter is orphaned.
    leftovers = [content_items[i]["id"] for i in range(len(content_items)) if i not in used]
    for j, cid in enumerate(leftovers):
        out[j % n]["content_ids"].append(cid)

    return out[:n]


def get_subtopics(
    chapter_id: Optional[int],
    chapter_title: str,
    description: str,
    content_items: list[dict],
    n: int,
    db: Session,
    *,
    subject_name: str = "",
) -> list[dict]:
    """
    Return exactly ``n`` ordered subtopics for a chapter, using cache → LLM → heuristic.

    content_items: [{"id": int, "title": str}] in playback order.
    """
    n = max(1, int(n))
    if n == 1:
        # A single-session chapter is studied whole — no segmentation needed.
        return [{
            "title": chapter_title,
            "summary": "",
            "content_ids": [c["id"] for c in content_items],
        }]

    # 1. Cache
    if chapter_id:
        cached = (
            db.query(StudyChapterSubtopic)
            .filter(
                StudyChapterSubtopic.chapter_id == chapter_id,
                StudyChapterSubtopic.session_count == n,
            )
            .first()
        )
        if cached and isinstance(cached.subtopics, list) and len(cached.subtopics) == n:
            return cached.subtopics

    # 2. LLM segmentation
    subtopics: Optional[list[dict]] = None
    try:
        materials_block = "\n".join(
            f"{i}. {c.get('title') or 'Untitled'}" for i, c in enumerate(content_items)
        ) or "(no uploaded materials)"
        context = (
            f"Subject: {subject_name}\n" if subject_name else ""
        ) + (
            f"Chapter: {chapter_title}\n"
            f"Number of subtopics to produce: {n}\n\n"
            f"Chapter description:\n{_trim(description, _MAX_DESC_CHARS) or '(none provided)'}\n\n"
            f"MATERIALS (index. title):\n{materials_block}"
        )
        result = chat_completion_json(
            messages=[
                {"role": "system", "content": _SEG_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.4,
            max_tokens=1500,
        )
        if result and isinstance(result.get("subtopics"), list) and result["subtopics"]:
            subtopics = _normalize(result["subtopics"], content_items, chapter_title, n)
    except Exception as e:  # never let segmentation break plan generation
        logger.warning("Subtopic segmentation LLM call failed for chapter %s: %s", chapter_id, e)

    # 3. Heuristic fallback
    if not subtopics:
        subtopics = _heuristic_subtopics(chapter_title, content_items, n)

    # 4. Cache (best-effort)
    if chapter_id:
        try:
            existing = (
                db.query(StudyChapterSubtopic)
                .filter(
                    StudyChapterSubtopic.chapter_id == chapter_id,
                    StudyChapterSubtopic.session_count == n,
                )
                .first()
            )
            if existing:
                existing.subtopics = subtopics
            else:
                db.add(StudyChapterSubtopic(
                    chapter_id=chapter_id, session_count=n, subtopics=subtopics,
                ))
            db.flush()
        except Exception as e:
            logger.warning("Failed to cache subtopics for chapter %s: %s", chapter_id, e)

    return subtopics
