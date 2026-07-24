from __future__ import annotations

import logging
from datetime import timedelta

from app.core.config import settings
from app.core.time import utc_from_timestamp, utc_now
from app.models import ShareFile, UploadPart, UploadSession
from app.services import storage
from app.services.uploads import clear_part_locks

logger = logging.getLogger(__name__)


def run_cleanup() -> dict:
    """Remove expired data. Every step is safe to retry."""
    now = utc_now()
    uploads_removed = 0
    files_removed = 0
    tmp_files_removed = 0

    expired_uploads = list(
        UploadSession.select().where(
            (UploadSession.expires_at <= now)
            & (UploadSession.status.in_(("uploading", "failed", "cancelled")))
        )
    )
    for upload in expired_uploads:
        claimed = (
            UploadSession.update(status="cancelled", updated_at=now)
            .where(
                (UploadSession.id == upload.id)
                & (UploadSession.expires_at <= now)
                & (
                    UploadSession.status.in_(
                        ("uploading", "failed", "cancelled")
                    )
                )
            )
            .execute()
        )
        if claimed != 1:
            continue
        try:
            storage.remove_upload_files(upload.id)
            UploadPart.delete().where(UploadPart.upload == upload).execute()
            UploadSession.delete_by_id(upload.id)
            clear_part_locks(upload.id)
            uploads_removed += 1
        except OSError:
            logger.exception("Failed to remove upload %s", upload.id)

    # Completion commits the share before removing parts. If the process stopped
    # between those steps, this makes the leftover cleanup recoverable.
    completed_uploads = UploadSession.select().where(
        UploadSession.status == "completed"
    )
    for upload in completed_uploads:
        try:
            storage.remove_upload_files(upload.id)
            UploadPart.delete().where(UploadPart.upload == upload).execute()
            clear_part_locks(upload.id)
        except OSError:
            logger.exception("Failed to remove completed upload %s", upload.id)

    grace = timedelta(minutes=settings.download_ticket_ttl_minutes)
    removable_shares = list(
        ShareFile.select().where(
            (ShareFile.expires_at <= now - grace)
            | (ShareFile.deleted_at.is_null(False))
        )
    )
    for share in removable_shares:
        try:
            storage.remove_share_file(share.relative_path)
            UploadSession.delete().where(
                (UploadSession.status == "completed")
                & (UploadSession.share_id == share.id)
            ).execute()
            ShareFile.delete_by_id(share.id)
            files_removed += 1
        except OSError:
            logger.exception("Failed to remove share %s", share.id)

    for temporary in (settings.storage_root / "merging").glob("*.tmp"):
        try:
            if utc_from_timestamp(temporary.stat().st_mtime) < now - timedelta(hours=2):
                temporary.unlink(missing_ok=True)
                tmp_files_removed += 1
        except OSError:
            logger.exception("Failed to remove temporary file %s", temporary.name)

    referenced_paths = {
        row.relative_path
        for row in ShareFile.select(ShareFile.relative_path)
    }
    for file_path in (settings.storage_root / "files").rglob("*.bin"):
        relative_path = file_path.relative_to(settings.storage_root).as_posix()
        try:
            is_old = utc_from_timestamp(file_path.stat().st_mtime) < now - timedelta(
                hours=2
            )
            if relative_path not in referenced_paths and is_old:
                file_path.unlink(missing_ok=True)
                files_removed += 1
        except OSError:
            logger.exception("Failed to remove orphan file %s", file_path.name)

    return {
        "uploads_deleted": uploads_removed,
        "files_deleted": files_removed,
        "tmp_files_deleted": tmp_files_removed,
    }
