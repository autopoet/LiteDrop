from __future__ import annotations

from datetime import timedelta

from app.core.database import database_proxy
from app.core.time import utc_now
from app.models import UploadSession, UploadState
from app.services import storage

STALE_MERGE_AFTER = timedelta(minutes=30)


def _recover_merges(*, max_age: timedelta | None) -> int:
    now = utc_now()
    predicate = UploadSession.status == UploadState.MERGING
    if max_age is not None:
        predicate &= UploadSession.updated_at <= now - max_age

    upload_ids = [row.id for row in UploadSession.select(UploadSession.id).where(predicate)]
    recovered = 0
    for upload_id in upload_ids:
        # Acquire SQLite's write lock before reading. This avoids a WAL snapshot
        # upgrade failure after the temp file has already been removed.
        with database_proxy.atomic("IMMEDIATE"):
            exists = (
                UploadSession.select().where((UploadSession.id == upload_id) & predicate).exists()
            )
            if not exists:
                continue

            # Keep the session in "merging" until its disposable temp file is gone.
            # A retry therefore cannot create a new temp file that recovery then deletes.
            storage.merge_temp_path(upload_id).unlink(missing_ok=True)
            recovered += (
                UploadSession.update(status=UploadState.FAILED, updated_at=now)
                .where((UploadSession.id == upload_id) & predicate)
                .execute()
            )
    return recovered


def recover_interrupted_merges() -> int:
    """Recover every merge left behind before this process started."""
    return _recover_merges(max_age=None)


def recover_stale_merges(
    max_age: timedelta = STALE_MERGE_AFTER,
) -> int:
    """Recover only merges old enough that they cannot be normal active work."""
    if max_age <= timedelta(0):
        raise ValueError("max_age must be greater than 0")
    return _recover_merges(max_age=max_age)
