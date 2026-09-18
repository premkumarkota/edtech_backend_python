"""
AI output language — shared helper for admin-triggered AI generation.

The admin panel chooses the language the AI must WRITE its output in (English by
default, or Telugu). This module normalizes that choice and produces a single
directive string that is appended to the system prompt of every generator
(chapter content, quiz, chapter quiz, mock test, and their refine paths), so the
language rule is defined once and applied identically everywhere.

Only the generated prose is translated — universally standard math symbols,
chemical formulas, numerals, and the fixed structural tokens the callers rely on
(Markdown syntax, fenced ASCII flowcharts, and the A/B/C/D option letters) stay
as-is so downstream rendering and grading keep working.
"""
from typing import Literal

# Canonical language codes used across the request schemas and services.
ENGLISH = "english"
TELUGU = "telugu"

SUPPORTED_LANGUAGES = (ENGLISH, TELUGU)

# Human-readable names + native script used inside the prompt directive.
_LANGUAGE_LABELS = {
    ENGLISH: "English",
    TELUGU: "Telugu (తెలుగు)",
}


def normalize_language(language: str | None) -> Literal["english", "telugu"]:
    """Coerce arbitrary input to a supported language code (defaults to English)."""
    code = (language or "").strip().lower()
    if code in ("te", "telugu", "తెలుగు"):
        return TELUGU
    return ENGLISH


def language_directive(language: str | None) -> str:
    """
    Return a directive to append to a system prompt so the model writes its
    output in the requested language. English returns an empty string (the
    models already default to English), keeping existing prompts unchanged.
    """
    code = normalize_language(language)
    if code == ENGLISH:
        return ""

    label = _LANGUAGE_LABELS[code]
    return (
        "\n\nCRITICAL LANGUAGE REQUIREMENT — this overrides any language implied "
        "elsewhere:\n"
        f"- Write ALL human-readable output in {label}. Every heading, sentence, "
        "question, answer option, explanation, worked example, label, and title "
        f"MUST be in {label}.\n"
        "- Keep universally standard mathematical symbols, chemical formulas, "
        "equations, and numerals exactly as they are (do not transliterate them).\n"
        "- Do NOT translate the fixed structural tokens: keep Markdown syntax, the "
        "fenced ``` code block for the ASCII flowchart, and the answer option "
        "letters A, B, C, D in Latin script. Only the words/phrases you write for "
        f"those options and boxes are in {label}.\n"
        "- Proper nouns and widely-used technical terms with no natural equivalent "
        f"may stay in English, but all explanatory prose must be in {label}.\n"
        f"- Do not mix in English sentences. Respond fully in {label}."
    )
