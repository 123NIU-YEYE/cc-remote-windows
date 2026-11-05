"""Classify a dispatch failure so the room only retries what can heal itself.

A blind retry is worse than none: a missing directory fails forever and just
fills the timeline, while a dropped connection usually succeeds on the next try.
"""
from __future__ import annotations

# Conditions the user (or the machine) must fix; retrying cannot help.
PERMANENT_MARKERS = (
    "目录不存在",
    "会话已失效",
    "没有可用的编号",
    "无法为",
    "必须绑定",
    "unsupported",
    "not supported",
    "no such session",
    "invalid_session",
    "invalid_cwd",
)

# Conditions that usually clear on their own.
TRANSIENT_MARKERS = (
    "timeout",
    "timed out",
    "connection",
    "disconnected",
    "reset",
    "broken pipe",
    "eof",
    "unavailable",
    "temporarily",
    "rate limit",
    "overloaded",
    "502",
    "503",
    "504",
)

RETRY_LIMIT = 1


def is_transient(message: str) -> bool:
    """True when the same dispatch is worth attempting once more."""
    text = (message or "").lower()
    if not text:
        # An empty message is a programming error, not a network blip.
        return False
    if any(marker.lower() in text for marker in PERMANENT_MARKERS):
        return False
    return any(marker.lower() in text for marker in TRANSIENT_MARKERS)


def should_retry(message: str, attempts: int) -> bool:
    """``attempts`` counts attempts already made (1 = the first one failed)."""
    return attempts <= RETRY_LIMIT and is_transient(message)
