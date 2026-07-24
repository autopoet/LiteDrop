from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import HARD_MAX_FILE_SIZE, Settings, settings
from app.core.database import close_database, create_tables, initialize_database
from app.core.security import PBKDF2_MAX_ITERATIONS, hash_password, verify_password
from app.core.time import utc_now
from app.main import app
from app.models import ShareFile, UploadPart, UploadSession
from app.services import cleanup, recovery, storage


def _set_setting(name: str, value: object) -> None:
    object.__setattr__(settings, name, value)


@pytest.fixture
def runtime(tmp_path: Path):
    """Give each recovery/cleanup test its own database and storage tree."""
    overrides = {
        "app_env": "development",
        "database_path": tmp_path / "data" / "test.db",
        "storage_root": tmp_path / "storage",
        "chunk_size": 4,
        "max_chunk_size": 8,
        "max_file_size": 32,
        "storage_quota": 1024,
        "disk_reserve": 0,
        "upload_session_ttl_minutes": 120,
        "default_expire_hours": 1,
        "max_expire_hours": 24,
        "download_ticket_ttl_minutes": 30,
        "max_download_limit": 5,
        "admin_token_expire_hours": 8,
        "cleanup_interval_minutes": 60,
    }
    previous = {name: getattr(settings, name) for name in overrides}
    for name, value in overrides.items():
        _set_setting(name, value)

    settings.prepare_directories()
    initialize_database(settings.database_path)
    create_tables()
    try:
        yield
    finally:
        close_database()
        for name, value in previous.items():
            _set_setting(name, value)


def _upload(
    *,
    status: str,
    updated_at=None,
    expires_at=None,
    share_id: str | None = None,
) -> UploadSession:
    now = utc_now()
    return UploadSession.create(
        id=str(uuid4()),
        file_name="demo.bin",
        total_size=4,
        chunk_size=4,
        total_chunks=1,
        status=status,
        expire_hours=1,
        download_limit=1,
        share_id=share_id,
        updated_at=updated_at or now,
        expires_at=expires_at or now + timedelta(hours=1),
    )


def _write_part(upload: UploadSession, content: bytes = b"data") -> Path:
    path = storage.part_path(upload.id, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    UploadPart.create(
        upload=upload,
        part_number=0,
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    return path


def _write_merge_tmp(upload: UploadSession) -> Path:
    path = storage.merge_temp_path(upload.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"unfinished merge")
    return path


def _share(*, code: str, expires_at) -> tuple[ShareFile, Path]:
    share_id = str(uuid4())
    relative_path = f"files/2026/07/{share_id}.bin"
    path = storage.resolve_relative_path(relative_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"data")
    share = ShareFile.create(
        id=share_id,
        code=code,
        original_name=f"{code}.bin",
        stored_name=f"{share_id}.bin",
        relative_path=relative_path,
        size=4,
        sha256=hashlib.sha256(b"data").hexdigest(),
        download_limit=1,
        expires_at=expires_at,
    )
    return share, path


def test_startup_recovers_interrupted_merge_and_keeps_parts(runtime):
    upload = _upload(status="merging")
    part_path = _write_part(upload)
    merge_tmp = _write_merge_tmp(upload)

    # Reopening through the application lifespan simulates a process restart.
    close_database()
    with TestClient(app):
        recovered = UploadSession.get_by_id(upload.id)
        assert recovered.status == "failed"
        assert part_path.read_bytes() == b"data"
        assert UploadPart.select().where(UploadPart.upload == upload.id).count() == 1
        assert not merge_tmp.exists()


def test_scheduled_cleanup_recovers_only_stale_merges(runtime):
    now = utc_now()
    stale = _upload(
        status="merging", updated_at=now - recovery.STALE_MERGE_AFTER - timedelta(seconds=1)
    )
    fresh = _upload(
        status="merging", updated_at=now - recovery.STALE_MERGE_AFTER + timedelta(seconds=1)
    )
    stale_part = _write_part(stale)
    fresh_part = _write_part(fresh)
    stale_tmp = _write_merge_tmp(stale)
    fresh_tmp = _write_merge_tmp(fresh)

    result = cleanup.run_cleanup()

    assert UploadSession.get_by_id(stale.id).status == "failed"
    assert UploadSession.get_by_id(fresh.id).status == "merging"
    assert stale_part.exists() and fresh_part.exists()
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()
    assert result.keys() == {
        "uploads_deleted",
        "files_deleted",
        "tmp_files_deleted",
    }


def test_recovery_removes_temp_before_allowing_retry(runtime, monkeypatch):
    upload = _upload(status="merging")
    merge_tmp = _write_merge_tmp(upload)
    real_unlink = Path.unlink

    def checked_unlink(path: Path, *, missing_ok: bool = False):
        assert UploadSession.get_by_id(upload.id).status == "merging"
        return real_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", checked_unlink)

    assert recovery.recover_interrupted_merges() == 1
    assert UploadSession.get_by_id(upload.id).status == "failed"
    assert not merge_tmp.exists()


def _valid_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "development",
        "chunk_size": 4,
        "max_chunk_size": 8,
        "max_file_size": 32,
        "storage_quota": HARD_MAX_FILE_SIZE * 2,
        "disk_reserve": 0,
        "upload_session_ttl_minutes": 120,
        "default_expire_hours": 6,
        "max_expire_hours": 24,
        "download_ticket_ttl_minutes": 30,
        "max_download_limit": 5,
        "admin_token_expire_hours": 8,
        "cleanup_interval_minutes": 30,
    }
    values.update(changes)
    return replace(Settings.from_env(), **values)


@pytest.mark.parametrize(
    ("changes", "case"),
    [
        ({"app_env": "prodution"}, "unknown environment"),
        ({"chunk_size": 0}, "zero chunk"),
        ({"max_chunk_size": 0}, "zero max chunk"),
        ({"max_file_size": 0}, "zero max file"),
        ({"storage_quota": 0}, "zero quota"),
        ({"disk_reserve": -1}, "negative reserve"),
        ({"upload_session_ttl_minutes": 0}, "zero upload ttl"),
        ({"default_expire_hours": 0}, "zero default expiry"),
        ({"max_expire_hours": 0}, "zero max expiry"),
        ({"download_ticket_ttl_minutes": 0}, "zero ticket ttl"),
        ({"max_download_limit": 0}, "zero download limit"),
        ({"admin_token_expire_hours": 0}, "zero admin ttl"),
        ({"cleanup_interval_minutes": 0}, "zero cleanup interval"),
        ({"chunk_size": 9, "max_chunk_size": 8}, "chunk exceeds request limit"),
        (
            {"max_file_size": HARD_MAX_FILE_SIZE + 1},
            "file exceeds product hard limit",
        ),
        ({"max_file_size": 33, "storage_quota": 32}, "file exceeds quota"),
        (
            {"default_expire_hours": 25, "max_expire_hours": 24},
            "default expiry exceeds maximum",
        ),
    ],
    ids=lambda value: value if isinstance(value, str) else None,
)
def test_settings_validate_rejects_invalid_boundaries(changes, case):
    del case  # The readable parameter is used as the pytest case id.
    with pytest.raises(ValueError):
        _valid_settings(**changes).validate()


def test_settings_validate_accepts_inclusive_boundaries():
    config = _valid_settings(
        chunk_size=1,
        max_chunk_size=1,
        max_file_size=HARD_MAX_FILE_SIZE,
        storage_quota=HARD_MAX_FILE_SIZE,
        disk_reserve=0,
        upload_session_ttl_minutes=1,
        default_expire_hours=1,
        max_expire_hours=1,
        download_ticket_ttl_minutes=1,
        max_download_limit=1,
        admin_token_expire_hours=1,
        cleanup_interval_minutes=1,
    )
    config.validate()


def _production_settings(**changes: object) -> Settings:
    values: dict[str, object] = {
        "app_env": " ProDucTion ",
        "app_secret": "a" * 32,
        "download_secret": "b" * 32,
        "upload_access_code": "private-upload-code",
        "admin_password_hash": hash_password("strong-admin-password"),
    }
    values.update(changes)
    return _valid_settings(**values)


def test_settings_validate_accepts_strong_production_config():
    _production_settings().validate()


@pytest.mark.parametrize(
    "changes",
    [
        {"app_secret": "a" * 31},
        {"app_secret": "dev-" + "a" * 32},
        {"app_secret": "a" * 32 + "change-me"},
        {"download_secret": "b" * 31},
        {"download_secret": "dev-" + "b" * 32},
        {"download_secret": "b" * 32 + "change-me"},
        {"upload_access_code": "   "},
        {"upload_access_code": "change-me-upload-code"},
    ],
)
def test_settings_validate_rejects_weak_production_secrets(changes):
    with pytest.raises(ValueError):
        _production_settings(**changes).validate()


def test_settings_validate_rejects_invalid_production_password_hashes():
    valid = hash_password("strong-admin-password")
    algorithm, _, salt, digest = valid.split("$")
    invalid_hashes = [
        "",
        f"not-{algorithm}$310000${salt}${digest}",
        f"{algorithm}$99999${salt}${digest}",
        f"{algorithm}${PBKDF2_MAX_ITERATIONS + 1}${salt}${digest}",
        f"{algorithm}$not-a-number${salt}${digest}",
        (f"{algorithm}$310000${base64.urlsafe_b64encode(b'short').decode()}${digest}"),
        (f"{algorithm}$310000${salt}${base64.urlsafe_b64encode(b'short').decode()}"),
    ]

    for invalid_hash in invalid_hashes:
        with pytest.raises(ValueError):
            _production_settings(admin_password_hash=invalid_hash).validate()


def test_verify_password_rejects_unsafe_iteration_counts():
    valid = hash_password("strong-admin-password")
    algorithm, _, salt, digest = valid.split("$")

    assert not verify_password(
        "strong-admin-password",
        f"{algorithm}$0${salt}${digest}",
    )
    assert not verify_password(
        "strong-admin-password",
        f"{algorithm}${PBKDF2_MAX_ITERATIONS + 1}${salt}${digest}",
    )


def test_cleanup_removes_completed_upload_leftovers(runtime):
    share, final_path = _share(code="100001", expires_at=utc_now() + timedelta(hours=1))
    upload = _upload(status="completed", share_id=share.id)
    part_path = _write_part(upload)
    merge_tmp = _write_merge_tmp(upload)

    cleanup.run_cleanup()

    assert UploadSession.get_by_id(upload.id).status == "completed"
    assert not UploadPart.select().where(UploadPart.upload == upload.id).exists()
    assert not part_path.parent.exists()
    assert not merge_tmp.exists()
    assert final_path.exists()
    assert ShareFile.get_or_none(ShareFile.id == share.id) is not None


def test_cleanup_deletes_only_old_orphan_files(runtime):
    files_dir = settings.storage_root / "files" / "2026" / "07"
    files_dir.mkdir(parents=True)
    old_orphan = files_dir / "old.bin"
    fresh_orphan = files_dir / "fresh.bin"
    old_orphan.write_bytes(b"old")
    fresh_orphan.write_bytes(b"fresh")
    old_timestamp = time.time() - timedelta(hours=2, seconds=10).total_seconds()
    fresh_timestamp = time.time() - timedelta(hours=1, minutes=59).total_seconds()
    os.utime(old_orphan, (old_timestamp, old_timestamp))
    os.utime(fresh_orphan, (fresh_timestamp, fresh_timestamp))

    result = cleanup.run_cleanup()

    assert not old_orphan.exists()
    assert fresh_orphan.exists()
    assert result["files_deleted"] == 1


def test_cleanup_honors_download_ticket_grace_period(runtime):
    now = utc_now()
    grace = timedelta(minutes=settings.download_ticket_ttl_minutes)
    within_grace, within_path = _share(
        code="100002", expires_at=now - grace + timedelta(seconds=10)
    )
    beyond_grace, beyond_path = _share(
        code="100003", expires_at=now - grace - timedelta(seconds=10)
    )
    within_upload = _upload(status="completed", share_id=within_grace.id)
    beyond_upload = _upload(status="completed", share_id=beyond_grace.id)

    cleanup.run_cleanup()

    assert ShareFile.get_or_none(ShareFile.id == within_grace.id) is not None
    assert UploadSession.get_or_none(UploadSession.id == within_upload.id) is not None
    assert within_path.exists()

    assert ShareFile.get_or_none(ShareFile.id == beyond_grace.id) is None
    assert UploadSession.get_or_none(UploadSession.id == beyond_upload.id) is None
    assert not beyond_path.exists()
