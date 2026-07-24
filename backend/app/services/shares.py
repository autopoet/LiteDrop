from __future__ import annotations

from datetime import timedelta

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import create_token, decode_token
from app.core.time import utc_now
from app.models import ShareFile


def _public_share(code: str) -> ShareFile:
    now = utc_now()
    share = ShareFile.get_or_none(
        (ShareFile.code == code) & (ShareFile.deleted_at.is_null()) & (ShareFile.expires_at > now)
    )
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在或已失效")
    return share


def share_view(code: str) -> dict:
    share = _public_share(code)
    return {
        "code": share.code,
        "file_name": share.original_name,
        "size": share.size,
        "sha256": share.sha256,
        "created_at": share.created_at,
        "expires_at": share.expires_at,
        "download_limit": share.download_limit,
        "download_count": share.download_count,
        "remaining_downloads": max(share.download_limit - share.download_count, 0),
    }


def create_download_ticket(code: str) -> dict:
    share = _public_share(code)
    now = utc_now()
    updated = (
        ShareFile.update(download_count=ShareFile.download_count + 1)
        .where(
            (ShareFile.id == share.id)
            & (ShareFile.deleted_at.is_null())
            & (ShareFile.expires_at > now)
            & (ShareFile.download_count < ShareFile.download_limit)
        )
        .execute()
    )
    if updated != 1:
        current = ShareFile.get_or_none(ShareFile.id == share.id)
        if current and current.download_count >= current.download_limit:
            raise AppError(410, "DOWNLOAD_LIMIT_REACHED", "下载次数已用完")
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在或已失效")

    lifetime = timedelta(minutes=settings.download_ticket_ttl_minutes)
    token = create_token(settings.download_secret, "download", share.id, lifetime)
    return {
        "download_url": f"/api/downloads/{token}",
        "expires_at": now + lifetime,
    }


def share_from_ticket(ticket: str) -> ShareFile:
    try:
        payload = decode_token(settings.download_secret, ticket, "download")
    except ValueError as exc:
        raise AppError(401, "INVALID_DOWNLOAD_TICKET", str(exc)) from exc

    share = ShareFile.get_or_none(
        (ShareFile.id == payload["sub"]) & (ShareFile.deleted_at.is_null())
    )
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在或已失效")
    return share
