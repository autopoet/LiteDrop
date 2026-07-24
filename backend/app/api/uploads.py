from __future__ import annotations

from fastapi import APIRouter, File, Form, Header, Request, UploadFile

from app.api.common import success
from app.api.dependencies import client_ip
from app.schemas import UploadCreate
from app.services import uploads

router = APIRouter(prefix="/api/uploads", tags=["uploads"])


@router.post("")
def create_upload(
    request: Request,
    payload: UploadCreate,
    upload_code: str | None = Header(default=None, alias="X-Upload-Code"),
):
    data = uploads.create_upload(payload, upload_code, client_ip(request))
    return success(request, data, status_code=201)


@router.get("/{upload_id}")
def get_upload(request: Request, upload_id: str):
    return success(request, uploads.upload_view(upload_id))


@router.put("/{upload_id}/chunks/{part_number}")
def upload_part(
    request: Request,
    upload_id: str,
    part_number: int,
    chunk: UploadFile = File(),
    chunk_sha256: str = Form(),
):
    data = uploads.save_part(upload_id, part_number, chunk, chunk_sha256)
    return success(request, data)


@router.post("/{upload_id}/complete")
def complete_upload(request: Request, upload_id: str):
    return success(request, uploads.complete_upload(upload_id))


@router.delete("/{upload_id}")
def cancel_upload(request: Request, upload_id: str):
    return success(request, uploads.cancel_upload(upload_id))
