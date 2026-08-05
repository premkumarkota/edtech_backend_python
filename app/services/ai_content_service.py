"""
AI Content Service — generates rich, textbook-style chapter content with Claude.

Produces Markdown (headings, sub-headings, plain-text equations, and a mandatory
ASCII flowchart in a fenced code block) so it renders identically in the admin
panel and the student app via flutter_markdown — no diagram engine needed.

Config (app/config.py / .env.dev):
    ANTHROPIC_API_KEY  — Claude API key (sk-ant-...)
    ANTHROPIC_MODEL    — model id (default "claude-sonnet-5")
"""
import logging

from app.config import settings

logger = logging.getLogger(__name__)


class AIContentError(Exception):
    """Raised when content generation fails (missing key, API error, empty result)."""


_SYSTEM_PROMPT = (
    "You are an expert textbook author and educator for a premium EdTech platform "
    "(think BYJU'S / Khan Academy quality). You write clear, engaging, well-structured "
    "study content that a student reads to learn a topic from scratch.\n\n"
    "OUTPUT RULES — follow ALL strictly:\n"
    "1. Output GitHub-flavored Markdown ONLY. No preamble, no sign-off, no meta text.\n"
    "2. Structure with headings: one '# ' title, then '## ' sections and '### ' sub-sections.\n"
    "3. Use **bold** for key terms, and bullet or numbered lists where helpful.\n"
    "4. Write equations/formulas in plain readable text using unicode symbols "
    "(×, ÷, √, ², ₀, →, ≈, θ, Δ). Do NOT use LaTeX or '$' delimiters.\n"
    "5. MANDATORY: include at least one FLOWCHART for every piece of content, drawn as an "
    "ASCII diagram inside a fenced code block (```), using box/arrow characters "
    "(┌ ─ ┐ │ └ ┘ ▼ → ├ ┤). The flowchart must visually explain a process, derivation, "
    "classification, or concept flow from the topic.\n"
    "6. Recommended flow: a short intro, core concepts (with definitions), key formulas, "
    "the mandatory flowchart, one worked example, real-world applications, and a "
    "'Key Takeaways' summary list.\n"
    "7. Calibrate depth, vocabulary, rigor, and examples to the requested LEVEL. The same "
    "topic must be simpler for lower grades and more rigorous/mathematical for higher levels.\n"
    "8. Keep it accurate and self-contained."
)


def _build_prompt(
    category_name: str, subject: str, chapter: str, topic: str, level: str
) -> str:
    parts = ["Write complete study content for the following:\n"]
    if category_name:
        parts.append(f"- Category / audience: {category_name}")
    if subject:
        parts.append(f"- Subject: {subject}")
    if chapter:
        parts.append(f"- Chapter: {chapter}")
    parts.append(f"- Topic: {topic}")
    parts.append(f"- Level: {level}")
    parts.append(
        "\nRemember: rich headings/sub-headings, plain-text equations, and at least one "
        "mandatory ASCII flowchart in a code block. Output Markdown only."
    )
    return "\n".join(parts)


def generate_content(
    category_name: str,
    subject: str,
    chapter: str,
    topic: str,
    level: str,
) -> str:
    """
    Generate rich Markdown chapter content. Raises AIContentError on failure.
    """
    api_key = (getattr(settings, "ANTHROPIC_API_KEY", "") or "").strip()
    if not api_key:
        raise AIContentError(
            "ANTHROPIC_API_KEY is not configured. Add it to .env.dev and restart."
        )

    try:
        import anthropic
    except ImportError as e:
        raise AIContentError(
            "The 'anthropic' package is not installed. Run: pip install anthropic"
        ) from e

    model = (getattr(settings, "ANTHROPIC_MODEL", "") or "claude-sonnet-5").strip()
    topic = (topic or "").strip()
    level = (level or "General").strip()
    if not topic:
        raise AIContentError("Topic is required.")

    client = anthropic.Anthropic(api_key=api_key)
    try:
        response = client.messages.create(
            model=model,
            max_tokens=8000,
            thinking={"type": "disabled"},
            system=_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": _build_prompt(
                    category_name or "", subject or "", chapter or "", topic, level
                ),
            }],
        )
    except Exception as e:
        logger.error(f"AI content generation failed: {e}")
        raise AIContentError(f"AI generation failed: {e}") from e

    text = "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    ).strip()
    if not text:
        raise AIContentError("The AI returned empty content. Try again or refine the topic.")
    return text
