from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

from app.api.common import success
from app.core.errors import AppError
from app.services import shares, storage

router = APIRouter(tags=["shares"])


@router.get("/api/shares/{code}")
def get_share(request: Request, code: str):
    return success(request, shares.share_view(code))


@router.post("/api/shares/{code}/download-ticket")
def create_download_ticket(request: Request, code: str):
    return success(request, shares.create_download_ticket(code))


@router.get("/api/downloads/{ticket}")
def download_file(ticket: str):
    share = shares.share_from_ticket(ticket)
    file_path = storage.resolve_relative_path(share.relative_path)
    if not file_path.is_file():
        raise AppError(404, "SHARE_NOT_FOUND", "文件不存在或已失效")
    return FileResponse(
        file_path,
        media_type=share.mime_type or "application/octet-stream",
        filename=share.original_name,
        content_disposition_type="attachment",
    )
