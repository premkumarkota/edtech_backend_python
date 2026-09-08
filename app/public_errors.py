"""User-facing HTTP errors. Never leak SDK, traceback, or infra details."""

from __future__ import annotations

import logging

from fastapi import HTTPException

logger = logging.getLogger(__name__)

AI_UNAVAILABLE = (
    "AI generation is temporarily unavailable. Please try again in a moment."
)
AI_NOT_CONFIGURED = (
    "AI is not set up on this server yet. Add the Anthropic API key and restart, "
    "or contact the platform owner."
)
AI_BUSY = "The AI service is busy right now. Wait a minute and try again."
AI_EMPTY = (
    "The AI didn't return usable content. Try a clearer topic and generate again."
)


def _looks_internal(text: str) -> bool:
    lowered = text.lower()
    markers = (
        "traceback",
        "exception",
        "error code",
        "httpx",
        "anthropic.",
        "status_code",
        "<html",
        "<!doctype",
        "sqlalchemy",
        "psycopg",
        "operationalerror",
    )
    return any(m in lowered for m in markers) or len(text) > 180


def http_from_ai_error(exc: BaseException) -> HTTPException:
    """Map an AI service exception to a public 4xx/502 without leaking internals."""
    text = str(exc).strip()
    lowered = text.lower()
    logger.warning("AI upstream error: %s", text)

    if "api key" in lowered or "not configured" in lowered:
        return HTTPException(status_code=502, detail=AI_NOT_CONFIGURED)
    if "not installed" in lowered:
        return HTTPException(status_code=502, detail=AI_NOT_CONFIGURED)
    if "429" in text or "rate" in lowered or "overloaded" in lowered:
        return HTTPException(status_code=429, detail=AI_BUSY)
    if "no questions" in lowered or "returned no" in lowered:
        return HTTPException(status_code=502, detail=AI_EMPTY)
    if any(
        phrase in lowered
        for phrase in (
            "required",
            "type what",
            "no existing content",
            "no content to generate",
            "select at least",
            "generate first",
            "generate content first",
        )
    ):
        return HTTPException(status_code=422, detail=text)

    if text and not _looks_internal(text):
        return HTTPException(status_code=502, detail=text)
    return HTTPException(status_code=502, detail=AI_UNAVAILABLE)


def public_validation_error(exc: BaseException) -> HTTPException:
    """422 for operator mistakes (bad Excel, missing fields) without leaking internals."""
    text = str(exc).strip()
    if not text or _looks_internal(text):
        return HTTPException(
            status_code=422,
            detail="This file couldn't be read. Check the columns and try again.",
        )
    return HTTPException(status_code=422, detail=text)


def public_server_error(exc: BaseException, *, action: str) -> HTTPException:
    logger.exception("%s failed", action)
    return HTTPException(
        status_code=500,
        detail=f"Couldn't {action}. Please try again. If it keeps happening, contact support.",
    )
