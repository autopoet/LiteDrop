from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Return naive UTC for SQLite DateTimeField comparisons."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def utc_from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, timezone.utc).replace(tzinfo=None)
