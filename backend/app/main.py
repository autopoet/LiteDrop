from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from peewee import OperationalError

from app.api import admin, shares, uploads
from app.core.config import settings
from app.core.database import close_database, create_tables, initialize_database
from app.core.errors import AppError
from app.services import cleanup, storage

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def _cleanup_loop() -> None:
    while True:
        await asyncio.sleep(settings.cleanup_interval_minutes * 60)
        try:
            await asyncio.to_thread(cleanup.run_cleanup)
        except Exception:
            logger.exception("Scheduled cleanup failed")


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.prepare_directories()
    initialize_database(settings.database_path)
    create_tables()
    task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        close_database()


app = FastAPI(
    title=settings.app_name,
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.allowed_origins),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    request.state.request_id = request.headers.get("X-Request-ID") or str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def _error_response(
    request: Request, status_code: int, code: str, message: str
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "success": False,
            "error": {"code": code, "message": message},
            "request_id": getattr(request.state, "request_id", ""),
        },
    )


@app.exception_handler(AppError)
async def handle_app_error(request: Request, exc: AppError):
    return _error_response(request, exc.status_code, exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request: Request, _: RequestValidationError):
    return _error_response(request, 422, "INVALID_REQUEST", "请求参数不正确")


@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException):
    return _error_response(request, exc.status_code, "HTTP_ERROR", str(exc.detail))


@app.exception_handler(Exception)
async def handle_unexpected_error(request: Request, exc: Exception):
    logger.exception("Unhandled request error", exc_info=exc)
    return _error_response(request, 500, "INTERNAL_ERROR", "服务器内部错误")


@app.get("/health")
def health():
    database_status = "ok"
    try:
        from app.core.database import database_proxy

        database_proxy.execute_sql("SELECT 1")
    except OperationalError:
        database_status = "error"

    storage_status = "ok" if settings.storage_root.is_dir() else "error"
    return {
        "status": "ok" if database_status == storage_status == "ok" else "error",
        "database": database_status,
        "storage": storage_status,
        "free_disk_bytes": storage.free_disk_bytes(),
        "version": "1.0.0",
    }


app.include_router(uploads.router)
app.include_router(shares.router)
app.include_router(admin.router)
