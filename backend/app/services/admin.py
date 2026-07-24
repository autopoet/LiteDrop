from __future__ import annotations

import hmac
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import create_token, decode_token, verify_password
from app.core.time import utc_now
from app.models import ShareFile, UploadSession, UploadState
from app.services import storage
from app.services.metrics import get_storage_usage


class LoginRateLimiter:
    """Small single-process limiter matching the single-worker deployment."""

    def __init__(self, max_failures: int = 5, window_minutes: int = 5) -> None:
        self.max_failures = max_failures
        self.window = timedelta(minutes=window_minutes)
        self._attempts: dict[str, deque[datetime]] = defaultdict(deque)
        self._lock = Lock()

    def check(self, client_ip: str) -> None:
        now = utc_now()
        with self._lock:
            attempts = self._attempts[client_ip]
            while attempts and attempts[0] <= now - self.window:
                attempts.popleft()
            if len(attempts) >= self.max_failures:
                raise AppError(
                    429,
                    "TOO_MANY_REQUESTS",
                    "登录失败次数过多，请稍后再试",
                )

    def record_failure(self, client_ip: str) -> None:
        with self._lock:
            self._attempts[client_ip].append(utc_now())

    def clear(self, client_ip: str) -> None:
        with self._lock:
            self._attempts.pop(client_ip, None)


login_limiter = LoginRateLimiter()


def login(username: str, password: str, client_ip: str) -> dict:
    login_limiter.check(client_ip)
    valid_username = hmac.compare_digest(username, settings.admin_username)
    valid_password = bool(
        settings.admin_password_hash and verify_password(password, settings.admin_password_hash)
    )
    if not (valid_username and valid_password):
        login_limiter.record_failure(client_ip)
        raise AppError(401, "ADMIN_UNAUTHORIZED", "用户名或密码错误")

    login_limiter.clear(client_ip)
    lifetime = timedelta(hours=settings.admin_token_expire_hours)
    return {
        "access_token": create_token(settings.app_secret, "admin", username, lifetime),
        "token_type": "bearer",
        "expires_at": utc_now() + lifetime,
    }


def verify_token(token: str) -> str:
    try:
        payload = decode_token(settings.app_secret, token, "admin")
    except ValueError as exc:
        raise AppError(401, "ADMIN_UNAUTHORIZED", str(exc)) from exc
    return str(payload["sub"])


def overview() -> dict:
    usage = get_storage_usage()
    file_count = ShareFile.select().where(ShareFile.deleted_at.is_null()).count()
    return {
        "file_count": file_count,
        "completed_bytes": usage.completed_bytes,
        "uploading_bytes": usage.uploading_bytes,
        "storage_quota_bytes": settings.storage_quota,
        "free_disk_bytes": storage.free_disk_bytes(),
        "public_upload_enabled": settings.public_upload_enabled,
        "max_file_size_bytes": settings.max_file_size,
    }


def list_files(search: str, status: str, page: int, page_size: int) -> dict:
    now = utc_now()
    query = ShareFile.select()
    if search:
        query = query.where(
            (ShareFile.original_name.contains(search)) | (ShareFile.code.contains(search))
        )
    if status == "active":
        query = query.where(ShareFile.deleted_at.is_null() & (ShareFile.expires_at > now))
    elif status == "expired":
        query = query.where(ShareFile.deleted_at.is_null() & (ShareFile.expires_at <= now))
    elif status == "deleted":
        query = query.where(ShareFile.deleted_at.is_null(False))

    total = query.count()
    rows = query.order_by(ShareFile.created_at.desc()).paginate(page, page_size)
    items = [
        {
            "id": item.id,
            "code": item.code,
            "file_name": item.original_name,
            "size": item.size,
            "download_limit": item.download_limit,
            "download_count": item.download_count,
            "created_at": item.created_at,
            "expires_at": item.expires_at,
            "deleted_at": item.deleted_at,
        }
        for item in rows
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
    }


def delete_file(file_id: str) -> dict:
    share = ShareFile.get_or_none(ShareFile.id == file_id)
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在")

    if share.deleted_at is None:
        ShareFile.update(deleted_at=utc_now()).where(ShareFile.id == share.id).execute()
    UploadSession.delete().where(
        (UploadSession.status == UploadState.COMPLETED) & (UploadSession.share_id == share.id)
    ).execute()
    storage.remove_share_file(share.relative_path)
    return {"file_id": share.id, "deleted": True}
