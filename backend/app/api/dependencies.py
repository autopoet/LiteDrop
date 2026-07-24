from __future__ import annotations

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import AppError
from app.services import admin as admin_service

bearer = HTTPBearer(auto_error=False)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def require_admin(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> str:
    if credentials is None:
        raise AppError(401, "ADMIN_UNAUTHORIZED", "请先登录管理员账号")
    return admin_service.verify_token(credentials.credentials)
