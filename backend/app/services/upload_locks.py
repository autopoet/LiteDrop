from __future__ import annotations

from threading import Lock

_locks: dict[tuple[str, int], Lock] = {}
_registry_lock = Lock()


def part_lock(upload_id: str, part_number: int) -> Lock:
    """Serialize duplicate writes for one part without blocking other parts."""
    key = (upload_id, part_number)
    with _registry_lock:
        return _locks.setdefault(key, Lock())


def clear_upload_locks(upload_id: str) -> None:
    with _registry_lock:
        keys = [key for key in _locks if key[0] == upload_id]
        for key in keys:
            _locks.pop(key, None)
