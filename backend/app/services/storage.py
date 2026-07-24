from __future__ import annotations

import hashlib
import os
import re
import shutil
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings
from app.core.errors import AppError

BUFFER_SIZE = 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")


def safe_filename(file_name: str) -> str:
    """Keep a display name, never a client-controlled disk path."""
    name = file_name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(char for char in name if char >= " " and char not in '<>:"/\\|?*')
    name = name.strip(" .")
    if not name:
        raise AppError(400, "INVALID_REQUEST", "文件名不能为空")
    return name[:255]


def upload_dir(upload_id: str) -> Path:
    return settings.storage_root / "uploads" / upload_id


def part_path(upload_id: str, part_number: int) -> Path:
    return upload_dir(upload_id) / f"{part_number:06d}.part"


def merge_temp_path(upload_id: str) -> Path:
    return settings.storage_root / "merging" / f"{upload_id}.merge.tmp"


def final_relative_path(share_id: str, now) -> Path:
    return Path("files") / f"{now:%Y}" / f"{now:%m}" / f"{share_id}.bin"


def resolve_relative_path(relative_path: str) -> Path:
    root = settings.storage_root.resolve()
    path = (root / relative_path).resolve()
    if not path.is_relative_to(root):
        raise AppError(500, "STORAGE_PATH_INVALID", "存储路径无效")
    return path


def free_disk_bytes() -> int:
    return shutil.disk_usage(settings.storage_root).free


def require_disk_space(required_bytes: int) -> None:
    if free_disk_bytes() < required_bytes + settings.disk_reserve:
        raise AppError(507, "INSUFFICIENT_STORAGE", "服务器磁盘空间不足")


def write_chunk(
    upload_id: str,
    part_number: int,
    source: UploadFile,
    expected_size: int,
    expected_sha256: str,
) -> tuple[int, str]:
    if not SHA256_PATTERN.fullmatch(expected_sha256):
        raise AppError(422, "CHUNK_HASH_MISMATCH", "分片哈希格式不正确")

    directory = upload_dir(upload_id)
    directory.mkdir(parents=True, exist_ok=True)
    destination = part_path(upload_id, part_number)
    temporary = directory / f".{part_number}.{uuid4().hex}.tmp"
    digest = hashlib.sha256()
    size = 0

    try:
        with temporary.open("xb") as output:
            while data := source.file.read(BUFFER_SIZE):
                size += len(data)
                if size > expected_size:
                    raise AppError(422, "CHUNK_SIZE_MISMATCH", "分片大小不正确")
                output.write(data)
                digest.update(data)
            output.flush()
            os.fsync(output.fileno())

        actual_sha256 = digest.hexdigest()
        if size != expected_size:
            raise AppError(422, "CHUNK_SIZE_MISMATCH", "分片大小不正确")
        if actual_sha256.lower() != expected_sha256.lower():
            raise AppError(422, "CHUNK_HASH_MISMATCH", "分片哈希校验失败")

        os.replace(temporary, destination)
        return size, actual_sha256
    finally:
        temporary.unlink(missing_ok=True)


def remove_upload_files(upload_id: str) -> None:
    directory = upload_dir(upload_id)
    if directory.exists():
        shutil.rmtree(directory)
    merge_temp_path(upload_id).unlink(missing_ok=True)


def remove_share_file(relative_path: str) -> None:
    resolve_relative_path(relative_path).unlink(missing_ok=True)
