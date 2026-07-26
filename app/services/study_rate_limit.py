"""
Simple in-memory per-key rate limiting for single-process deployments.

Used by Study Planner V2 generate endpoints. Resets are window-based (sliding
hour). Not suitable for multi-worker production without a shared store.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
from threading import Lock

_lock = Lock()
_hits: dict[str, list[datetime]] = defaultdict(list)


def check_rate_limit(
    key: str,
    *,
    max_calls: int,
    window_hours: float = 1.0,
) -> tuple[bool, str | None]:
    """
    Return (allowed, error_message).
    error_message is set when the limit is exceeded.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)

    with _lock:
        recent = [t for t in _hits[key] if t > cutoff]
        if len(recent) >= max_calls:
            _hits[key] = recent
            return False, (
                f"Rate limit exceeded: max {max_calls} requests per "
                f"{int(window_hours * 60)} minutes. Try again later."
            )
        recent.append(now)
        _hits[key] = recent

    return True, None


def reset_rate_limits() -> None:
    """Clear all counters — useful in tests."""
    with _lock:
        _hits.clear()
