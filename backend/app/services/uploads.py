from __future__ import annotations

import hashlib
import hmac
import math
import mimetypes
import os
import secrets
from datetime import timedelta
from threading import Lock
from uuid import uuid4

from fastapi import UploadFile
from peewee import IntegrityError, fn

from app.core.config import settings
from app.core.database import database_proxy
from app.core.errors import AppError
from app.core.time import utc_now
from app.models import ShareFile, UploadPart, UploadSession
from app.schemas import UploadCreate
from app.services import storage

_part_locks: dict[tuple[str, int], Lock] = {}
_part_locks_guard = Lock()


def hash_client_ip(client_ip: str) -> str:
    return hmac.new(
        settings.app_secret.encode(), client_ip.encode(), hashlib.sha256
    ).hexdigest()


def _part_lock(upload_id: str, part_number: int) -> Lock:
    # A single process is deployed, so this small lock prevents duplicate part races.
    key = (upload_id, part_number)
    with _part_locks_guard:
        return _part_locks.setdefault(key, Lock())


def clear_part_locks(upload_id: str) -> None:
    with _part_locks_guard:
        keys = [key for key in _part_locks if key[0] == upload_id]
        for key in keys:
            _part_locks.pop(key, None)


def _get_upload(upload_id: str) -> UploadSession:
    upload = UploadSession.get_or_none(UploadSession.id == upload_id)
    if upload is None:
        raise AppError(404, "UPLOAD_NOT_FOUND", "上传任务不存在")
    return upload


def _stored_bytes() -> int:
    file_bytes = (
        ShareFile.select(fn.COALESCE(fn.SUM(ShareFile.size), 0))
        .where(ShareFile.deleted_at.is_null())
        .scalar()
    )
    upload_bytes = UploadPart.select(
        fn.COALESCE(fn.SUM(UploadPart.size), 0)
    ).scalar()
    return int(file_bytes or 0) + int(upload_bytes or 0)


def create_upload(
    payload: UploadCreate, access_code: str | None, client_ip: str
) -> dict:
    if not settings.public_upload_enabled:
        raise AppError(403, "UPLOAD_DISABLED", "服务器已关闭上传")
    if settings.upload_access_code and not hmac.compare_digest(
        access_code or "", settings.upload_access_code
    ):
        raise AppError(401, "INVALID_UPLOAD_ACCESS_CODE", "上传口令错误")

    file_name = storage.safe_filename(payload.file_name)
    if payload.total_size > settings.max_file_size:
        raise AppError(
            413,
            "FILE_TOO_LARGE",
            f"单个文件不能超过 {settings.max_file_size // 1024 // 1024} MiB",
        )
    if payload.expire_hours > settings.max_expire_hours:
        raise AppError(422, "INVALID_REQUEST", "保存时间超过服务器限制")
    if payload.download_limit > settings.max_download_limit:
        raise AppError(422, "INVALID_REQUEST", "下载次数超过服务器限制")

    now = utc_now()
    ip_hash = hash_client_ip(client_ip)
    active_exists = (
        UploadSession.select()
        .where(
            (UploadSession.client_ip_hash == ip_hash)
            & (UploadSession.status.in_(("uploading", "merging", "failed")))
            & (UploadSession.expires_at > now)
        )
        .exists()
    )
    if active_exists:
        raise AppError(409, "ACTIVE_UPLOAD_EXISTS", "当前网络已有未完成的上传")

    if _stored_bytes() + payload.total_size > settings.storage_quota:
        raise AppError(507, "STORAGE_QUOTA_REACHED", "服务器存储配额已满")
    storage.require_disk_space(payload.total_size * 2)

    total_chunks = math.ceil(payload.total_size / settings.chunk_size)
    upload = UploadSession.create(
        id=str(uuid4()),
        file_name=file_name,
        total_size=payload.total_size,
        chunk_size=settings.chunk_size,
        total_chunks=total_chunks,
        expire_hours=payload.expire_hours,
        download_limit=payload.download_limit,
        client_ip_hash=ip_hash,
        expires_at=now + timedelta(minutes=settings.upload_session_ttl_minutes),
    )
    return {
        "upload_id": upload.id,
        "chunk_size": upload.chunk_size,
        "total_chunks": upload.total_chunks,
        "expires_at": upload.expires_at,
    }


def upload_view(upload_id: str) -> dict:
    upload = _get_upload(upload_id)
    recorded_parts = list(
        UploadPart.select(UploadPart.part_number, UploadPart.size)
        .where(UploadPart.upload == upload)
        .order_by(UploadPart.part_number)
        .dicts()
    )
    parts = [
        part
        for part in recorded_parts
        if storage.part_path(upload.id, part["part_number"]).is_file()
        and storage.part_path(upload.id, part["part_number"]).stat().st_size
        == part["size"]
    ]
    return {
        "upload_id": upload.id,
        "file_name": upload.file_name,
        "total_size": upload.total_size,
        "chunk_size": upload.chunk_size,
        "total_chunks": upload.total_chunks,
        "uploaded_parts": [part["part_number"] for part in parts],
        "uploaded_bytes": sum(part["size"] for part in parts),
        "status": upload.status,
        "expires_at": upload.expires_at,
    }


def save_part(
    upload_id: str,
    part_number: int,
    chunk: UploadFile,
    chunk_sha256: str,
) -> dict:
    upload = _get_upload(upload_id)
    if not 0 <= part_number < upload.total_chunks:
        raise AppError(422, "INVALID_CHUNK_INDEX", "分片编号无效")

    expected_size = (
        upload.chunk_size
        if part_number < upload.total_chunks - 1
        else upload.total_size - upload.chunk_size * (upload.total_chunks - 1)
    )

    with _part_lock(upload_id, part_number):
        upload = _get_upload(upload_id)
        if upload.status != "uploading" or upload.expires_at <= utc_now():
            raise AppError(409, "UPLOAD_STATE_CONFLICT", "当前上传状态不允许写入分片")

        existing = UploadPart.get_or_none(
            (UploadPart.upload == upload)
            & (UploadPart.part_number == part_number)
        )
        if existing:
            existing_path = storage.part_path(upload.id, part_number)
            file_is_intact = (
                existing_path.is_file()
                and existing_path.stat().st_size == existing.size
            )
            if file_is_intact and existing.sha256.lower() == chunk_sha256.lower():
                return {"part_number": part_number, "idempotent": True}
            if file_is_intact:
                raise AppError(409, "CHUNK_CONFLICT", "该编号分片与已上传内容不同")
            existing.delete_instance()
            existing_path.unlink(missing_ok=True)

        size, actual_sha256 = storage.write_chunk(
            upload.id, part_number, chunk, expected_size, chunk_sha256
        )
        # Completion may have started while the file was being streamed.
        upload = _get_upload(upload_id)
        if upload.status != "uploading":
            storage.part_path(upload_id, part_number).unlink(missing_ok=True)
            raise AppError(409, "UPLOAD_STATE_CONFLICT", "文件已开始合并")

        UploadPart.create(
            upload=upload,
            part_number=part_number,
            size=size,
            sha256=actual_sha256,
        )
        UploadSession.update(updated_at=utc_now()).where(
            UploadSession.id == upload.id
        ).execute()
        return {"part_number": part_number, "idempotent": False}


def _missing_parts(upload: UploadSession) -> tuple[list[int], list[UploadPart]]:
    parts = list(
        UploadPart.select()
        .where(UploadPart.upload == upload)
        .order_by(UploadPart.part_number)
    )
    by_number = {part.part_number: part for part in parts}
    missing = [
        number
        for number in range(upload.total_chunks)
        if number not in by_number
        or not storage.part_path(upload.id, number).is_file()
    ]
    ordered = [by_number[number] for number in range(upload.total_chunks) if number in by_number]
    if sum(part.size for part in ordered) != upload.total_size:
        missing = missing or list(range(upload.total_chunks))
    return missing, ordered


def _completed_result(upload: UploadSession) -> dict:
    share = ShareFile.get_or_none(ShareFile.id == upload.share_id)
    if share is None:
        raise AppError(500, "SHARE_RECORD_MISSING", "分享记录不存在")
    return {
        "code": share.code,
        "file_name": share.original_name,
        "size": share.size,
        "sha256": share.sha256,
        "expires_at": share.expires_at,
        "download_limit": share.download_limit,
    }


def complete_upload(upload_id: str) -> dict:
    upload = _get_upload(upload_id)
    if upload.status == "completed":
        return _completed_result(upload)
    if upload.status == "merging":
        raise AppError(409, "UPLOAD_ALREADY_MERGING", "文件正在合并")
    if upload.status not in ("uploading", "failed"):
        raise AppError(409, "UPLOAD_STATE_CONFLICT", "当前上传状态不能完成")

    claimed = (
        UploadSession.update(status="merging", updated_at=utc_now())
        .where(
            (UploadSession.id == upload_id)
            & (UploadSession.status.in_(("uploading", "failed")))
        )
        .execute()
    )
    if claimed != 1:
        upload = _get_upload(upload_id)
        if upload.status == "completed":
            return _completed_result(upload)
        if upload.status == "merging":
            raise AppError(409, "UPLOAD_ALREADY_MERGING", "文件正在合并")
        raise AppError(409, "UPLOAD_STATE_CONFLICT", "当前上传状态不能完成")

    upload = _get_upload(upload_id)
    missing, parts = _missing_parts(upload)
    if missing:
        UploadSession.update(status="uploading", updated_at=utc_now()).where(
            (UploadSession.id == upload_id)
            & (UploadSession.status == "merging")
        ).execute()
        raise AppError(
            422,
            "UPLOAD_INCOMPLETE",
            f"缺少分片：{', '.join(map(str, missing[:10]))}",
        )

    temporary = storage.merge_temp_path(upload.id)
    share_id = str(uuid4())
    now = utc_now()
    relative_path = storage.final_relative_path(share_id, now)
    destination = storage.resolve_relative_path(str(relative_path))

    try:
        storage.require_disk_space(upload.total_size)
        temporary.unlink(missing_ok=True)
        digest = hashlib.sha256()
        merged_size = 0
        with temporary.open("xb") as output:
            for part in parts:
                with storage.part_path(upload.id, part.part_number).open("rb") as source:
                    while data := source.read(storage.BUFFER_SIZE):
                        output.write(data)
                        digest.update(data)
                        merged_size += len(data)
            output.flush()
            os.fsync(output.fileno())

        if merged_size != upload.total_size:
            raise AppError(422, "UPLOAD_INCOMPLETE", "合并后的文件大小不正确")

        destination.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, destination)
        expires_at = now + timedelta(hours=upload.expire_hours)
        mime_type = mimetypes.guess_type(upload.file_name)[0]

        for _ in range(20):
            code = f"{secrets.randbelow(1_000_000):06d}"
            try:
                with database_proxy.atomic():
                    share = ShareFile.create(
                        id=share_id,
                        code=code,
                        original_name=upload.file_name,
                        stored_name=destination.name,
                        relative_path=str(relative_path).replace("\\", "/"),
                        size=upload.total_size,
                        sha256=digest.hexdigest(),
                        mime_type=mime_type,
                        download_limit=upload.download_limit,
                        expires_at=expires_at,
                    )
                    UploadSession.update(
                        status="completed",
                        share_id=share.id,
                        updated_at=utc_now(),
                    ).where(UploadSession.id == upload.id).execute()
                break
            except IntegrityError:
                continue
        else:
            raise AppError(500, "CODE_GENERATION_FAILED", "取件码生成失败")

        UploadPart.delete().where(UploadPart.upload == upload).execute()
        storage.remove_upload_files(upload.id)
        clear_part_locks(upload.id)
        return _completed_result(_get_upload(upload.id))
    except Exception:
        temporary.unlink(missing_ok=True)
        if destination.exists() and not ShareFile.select().where(
            ShareFile.id == share_id
        ).exists():
            destination.unlink(missing_ok=True)
        UploadSession.update(status="failed", updated_at=utc_now()).where(
            (UploadSession.id == upload_id)
            & (UploadSession.status == "merging")
        ).execute()
        raise


def cancel_upload(upload_id: str) -> dict:
    upload = _get_upload(upload_id)
    claimed = (
        UploadSession.update(status="cancelled", updated_at=utc_now())
        .where(
            (UploadSession.id == upload.id)
            & (UploadSession.status.in_(("uploading", "failed")))
        )
        .execute()
    )
    if claimed != 1:
        raise AppError(409, "UPLOAD_STATE_CONFLICT", "当前上传状态不能取消")
    UploadPart.delete().where(UploadPart.upload == upload).execute()
    storage.remove_upload_files(upload.id)
    clear_part_locks(upload.id)
    return {"upload_id": upload.id, "status": "cancelled"}
