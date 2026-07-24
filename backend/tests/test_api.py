from __future__ import annotations

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.errors import AppError
from app.core.security import hash_password
from app.core.time import utc_now
from app.main import app
from app.models import ShareFile, UploadSession
from app.services import cleanup, shares, storage


def _set_setting(name: str, value) -> None:
    # Settings is frozen in production; tests deliberately replace only paths/limits.
    object.__setattr__(settings, name, value)


@pytest.fixture
def client(tmp_path: Path):
    _set_setting("database_path", tmp_path / "data" / "test.db")
    _set_setting("storage_root", tmp_path / "storage")
    _set_setting("chunk_size", 4)
    _set_setting("max_chunk_size", 8)
    _set_setting("max_file_size", 32)
    _set_setting("storage_quota", 1024)
    _set_setting("disk_reserve", 0)
    _set_setting("public_upload_enabled", True)
    _set_setting("upload_access_code", "upload-code")
    _set_setting("admin_username", "admin")
    _set_setting("admin_password_hash", hash_password("admin-password"))
    _set_setting("cleanup_interval_minutes", 60)

    with TestClient(app) as test_client:
        yield test_client


def _data(response):
    assert response.status_code < 400, response.text
    body = response.json()
    assert body["success"] is True
    assert body["request_id"]
    return body["data"]


def _start_upload(
    client: TestClient, file_name: str, size: int, download_limit: int = 1
) -> dict:
    response = client.post(
        "/api/uploads",
        headers={"X-Upload-Code": "upload-code"},
        json={
            "file_name": file_name,
            "total_size": size,
            "expire_hours": 1,
            "download_limit": download_limit,
        },
    )
    return _data(response)


def test_upload_resume_share_and_range_download(client: TestClient):
    content = b"abcdefghij"
    upload = _start_upload(client, "../demo.txt", len(content))
    assert upload["chunk_size"] == 4
    assert upload["total_chunks"] == 3

    for part_number in range(upload["total_chunks"]):
        chunk = content[part_number * 4 : (part_number + 1) * 4]
        digest = hashlib.sha256(chunk).hexdigest()
        response = client.put(
            f"/api/uploads/{upload['upload_id']}/chunks/{part_number}",
            files={"chunk": (f"{part_number}.part", chunk)},
            data={"chunk_sha256": digest},
        )
        assert _data(response)["idempotent"] is False

        if part_number == 0:
            repeated = client.put(
                f"/api/uploads/{upload['upload_id']}/chunks/0",
                files={"chunk": ("0.part", chunk)},
                data={"chunk_sha256": digest},
            )
            assert _data(repeated)["idempotent"] is True

    status = _data(client.get(f"/api/uploads/{upload['upload_id']}"))
    assert status["uploaded_parts"] == [0, 1, 2]
    assert status["uploaded_bytes"] == len(content)

    completed = _data(
        client.post(f"/api/uploads/{upload['upload_id']}/complete")
    )
    assert completed["file_name"] == "demo.txt"
    assert completed["sha256"] == hashlib.sha256(content).hexdigest()
    assert len(completed["code"]) == 6

    # Completing twice is idempotent and returns the original pickup code.
    repeated = _data(
        client.post(f"/api/uploads/{upload['upload_id']}/complete")
    )
    assert repeated["code"] == completed["code"]

    share = _data(client.get(f"/api/shares/{completed['code']}"))
    assert share["remaining_downloads"] == 1

    ticket = _data(
        client.post(f"/api/shares/{completed['code']}/download-ticket")
    )
    download = client.get(ticket["download_url"])
    assert download.status_code == 200
    assert download.content == content

    resumed = client.get(
        ticket["download_url"], headers={"Range": "bytes=0-3"}
    )
    assert resumed.status_code == 206
    assert resumed.content == content[:4]

    exhausted = client.post(
        f"/api/shares/{completed['code']}/download-ticket"
    )
    assert exhausted.status_code == 410
    assert exhausted.json()["error"]["code"] == "DOWNLOAD_LIMIT_REACHED"


def test_validation_admin_and_cleanup_contract(client: TestClient):
    denied = client.post(
        "/api/uploads",
        headers={"X-Upload-Code": "wrong"},
        json={
            "file_name": "demo.bin",
            "total_size": 4,
            "expire_hours": 1,
            "download_limit": 1,
        },
    )
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "INVALID_UPLOAD_ACCESS_CODE"

    too_large = client.post(
        "/api/uploads",
        headers={"X-Upload-Code": "upload-code"},
        json={
            "file_name": "large.bin",
            "total_size": 33,
            "expire_hours": 1,
            "download_limit": 1,
        },
    )
    assert too_large.status_code == 413

    upload = _start_upload(client, "small.bin", 4)
    bad_chunk = client.put(
        f"/api/uploads/{upload['upload_id']}/chunks/0",
        files={"chunk": ("0.part", b"data")},
        data={"chunk_sha256": "0" * 64},
    )
    assert bad_chunk.status_code == 422
    assert bad_chunk.json()["error"]["code"] == "CHUNK_HASH_MISMATCH"

    cancelled = _data(client.delete(f"/api/uploads/{upload['upload_id']}"))
    assert cancelled["status"] == "cancelled"

    login = _data(
        client.post(
            "/api/admin/login",
            json={"username": "admin", "password": "admin-password"},
        )
    )
    headers = {"Authorization": f"Bearer {login['access_token']}"}
    overview = _data(client.get("/api/admin/overview", headers=headers))
    assert overview["completed_bytes"] == 0
    assert overview["uploading_bytes"] == 0
    assert overview["public_upload_enabled"] is True

    files = _data(client.get("/api/admin/files?q=small", headers=headers))
    assert files == {"items": [], "total": 0}

    cleanup_result = _data(client.post("/api/admin/cleanup", headers=headers))
    assert set(cleanup_result) == {
        "uploads_deleted",
        "files_deleted",
        "tmp_files_deleted",
    }


def test_health(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_download_limit_is_atomic_and_cleanup_skips_merging(
    client: TestClient,
):
    now = utc_now()
    ShareFile.create(
        id=str(uuid4()),
        code="123456",
        original_name="race.bin",
        stored_name="race.bin",
        relative_path="files/race.bin",
        size=4,
        sha256=hashlib.sha256(b"data").hexdigest(),
        download_limit=1,
        expires_at=now + timedelta(hours=1),
    )

    def request_ticket() -> str:
        try:
            shares.create_download_ticket("123456")
            return "ok"
        except AppError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: request_ticket(), range(2)))
    assert sorted(results) == ["DOWNLOAD_LIMIT_REACHED", "ok"]

    merging = UploadSession.create(
        id=str(uuid4()),
        file_name="merging.bin",
        total_size=4,
        chunk_size=4,
        total_chunks=1,
        status="merging",
        expire_hours=1,
        download_limit=1,
        expires_at=now - timedelta(minutes=1),
    )
    storage.upload_dir(merging.id).mkdir(parents=True)
    cleanup.run_cleanup()
    assert UploadSession.get_or_none(UploadSession.id == merging.id) is not None
