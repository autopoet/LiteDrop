from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request

from app.api.common import success
from app.api.dependencies import client_ip, require_admin
from app.schemas import AdminLogin
from app.services import admin as admin_service
from app.services import cleanup

router = APIRouter(prefix="/api/admin", tags=["admin"])
admin_required = [Depends(require_admin)]


@router.post("/login")
def login(request: Request, payload: AdminLogin):
    data = admin_service.login(payload.username, payload.password, client_ip(request))
    return success(request, data)


@router.get("/overview", dependencies=admin_required)
def overview(request: Request):
    return success(request, admin_service.overview())


@router.get("/files", dependencies=admin_required)
def list_files(
    request: Request,
    q: str = "",
    status: str = Query(default="all", pattern="^(all|active|expired|deleted)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    data = admin_service.list_files(q, status, page, page_size)
    return success(request, data)


@router.delete("/files/{file_id}", dependencies=admin_required)
def delete_file(request: Request, file_id: str):
    return success(request, admin_service.delete_file(file_id))


@router.post("/cleanup", dependencies=admin_required)
def manual_cleanup(request: Request):
    return success(request, cleanup.run_cleanup())
