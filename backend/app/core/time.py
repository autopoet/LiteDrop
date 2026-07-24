from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return naive UTC for SQLite DateTimeField comparisons."""
    return datetime.now(UTC).replace(tzinfo=None)


def utc_from_timestamp(timestamp: float) -> datetime:
    return datetime.fromtimestamp(timestamp, UTC).replace(tzinfo=None)
