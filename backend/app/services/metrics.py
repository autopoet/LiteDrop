from __future__ import annotations

from dataclasses import dataclass

from peewee import fn

from app.models import ShareFile, UploadPart


@dataclass(frozen=True, slots=True)
class StorageUsage:
    completed_bytes: int
    uploading_bytes: int

    @property
    def total_bytes(self) -> int:
        return self.completed_bytes + self.uploading_bytes


def get_storage_usage() -> StorageUsage:
    """Read the two values used by quota checks and the admin overview."""
    completed = (
        ShareFile.select(fn.COALESCE(fn.SUM(ShareFile.size), 0))
        .where(ShareFile.deleted_at.is_null())
        .scalar()
    )
    uploading = UploadPart.select(fn.COALESCE(fn.SUM(UploadPart.size), 0)).scalar()
    return StorageUsage(
        completed_bytes=int(completed or 0),
        uploading_bytes=int(uploading or 0),
    )
