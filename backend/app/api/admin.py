from __future__ import annotations

import hmac
from collections import defaultdict, deque
from datetime import datetime, timedelta
from threading import Lock

from fastapi import APIRouter, Depends, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from peewee import fn

from app.api.common import success
from app.core.config import settings
from app.core.errors import AppError
from app.core.security import create_token, decode_token, verify_password
from app.core.time import utc_now
from app.models import ShareFile, UploadPart, UploadSession
from app.schemas import AdminLogin
from app.services import cleanup, storage

router = APIRouter(prefix="/api/admin", tags=["admin"])
bearer = HTTPBearer(auto_error=False)

_login_failures: dict[str, deque[datetime]] = defaultdict(deque)
_login_lock = Lock()
_login_window = timedelta(minutes=5)
_max_login_failures = 5


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _check_login_rate(client_ip: str) -> None:
    now = utc_now()
    with _login_lock:
        attempts = _login_failures[client_ip]
        while attempts and attempts[0] <= now - _login_window:
            attempts.popleft()
        if len(attempts) >= _max_login_failures:
            raise AppError(429, "TOO_MANY_REQUESTS", "登录失败次数过多，请稍后再试")


def _record_login_failure(client_ip: str) -> None:
    with _login_lock:
        _login_failures[client_ip].append(utc_now())


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None:
        raise AppError(401, "ADMIN_UNAUTHORIZED", "请先登录管理员账号")
    try:
        payload = decode_token(
            settings.app_secret, credentials.credentials, "admin"
        )
    except ValueError as exc:
        raise AppError(401, "ADMIN_UNAUTHORIZED", str(exc)) from exc
    return str(payload["sub"])


@router.post("/login")
def login(request: Request, payload: AdminLogin):
    client_ip = _client_ip(request)
    _check_login_rate(client_ip)
    valid_username = hmac.compare_digest(payload.username, settings.admin_username)
    if not (
        valid_username
        and settings.admin_password_hash
        and verify_password(payload.password, settings.admin_password_hash)
    ):
        _record_login_failure(client_ip)
        raise AppError(401, "ADMIN_UNAUTHORIZED", "用户名或密码错误")

    with _login_lock:
        _login_failures.pop(client_ip, None)
    lifetime = timedelta(hours=settings.admin_token_expire_hours)
    token = create_token(settings.app_secret, "admin", payload.username, lifetime)
    return success(
        request,
        {
            "access_token": token,
            "token_type": "bearer",
            "expires_at": utc_now() + lifetime,
        },
    )


@router.get("/overview", dependencies=[Depends(require_admin)])
def overview(request: Request):
    active_files = ShareFile.select().where(ShareFile.deleted_at.is_null())
    file_bytes = (
        ShareFile.select(fn.COALESCE(fn.SUM(ShareFile.size), 0))
        .where(ShareFile.deleted_at.is_null())
        .scalar()
    )
    upload_bytes = UploadPart.select(
        fn.COALESCE(fn.SUM(UploadPart.size), 0)
    ).scalar()
    return success(
        request,
        {
            "file_count": active_files.count(),
            "completed_bytes": int(file_bytes or 0),
            "uploading_bytes": int(upload_bytes or 0),
            "storage_quota_bytes": settings.storage_quota,
            "free_disk_bytes": storage.free_disk_bytes(),
            "public_upload_enabled": settings.public_upload_enabled,
            "max_file_size_bytes": settings.max_file_size,
        },
    )


@router.get("/files", dependencies=[Depends(require_admin)])
def list_files(
    request: Request,
    q: str = "",
    status: str = Query(default="all", pattern="^(all|active|expired|deleted)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    now = utc_now()
    query = ShareFile.select()
    if q:
        query = query.where(
            (ShareFile.original_name.contains(q))
            | (ShareFile.code.contains(q))
        )
    if status == "active":
        query = query.where(
            ShareFile.deleted_at.is_null() & (ShareFile.expires_at > now)
        )
    elif status == "expired":
        query = query.where(
            ShareFile.deleted_at.is_null() & (ShareFile.expires_at <= now)
        )
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
    return success(request, {"items": items, "total": total})


@router.delete("/files/{file_id}", dependencies=[Depends(require_admin)])
def delete_file(request: Request, file_id: str):
    share = ShareFile.get_or_none(ShareFile.id == file_id)
    if share is None:
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在")
    if share.deleted_at is None:
        ShareFile.update(deleted_at=utc_now()).where(
            ShareFile.id == share.id
        ).execute()
    UploadSession.delete().where(
        (UploadSession.status == "completed")
        & (UploadSession.share_id == share.id)
    ).execute()
    storage.remove_share_file(share.relative_path)
    return success(request, {"file_id": share.id, "deleted": True})


@router.post("/cleanup", dependencies=[Depends(require_admin)])
def manual_cleanup(request: Request):
    return success(request, cleanup.run_cleanup())
