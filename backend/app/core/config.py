from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.security import is_password_hash

MIB = 1024 * 1024
GIB = 1024 * MIB

# This is a product hard limit, not only a default setting.
HARD_MAX_FILE_SIZE = 200 * MIB


def _bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True, slots=True)
class Settings:
    app_name: str
    app_env: str
    app_secret: str
    download_secret: str
    database_path: Path
    storage_root: Path
    chunk_size: int
    max_chunk_size: int
    max_file_size: int
    storage_quota: int
    disk_reserve: int
    upload_session_ttl_minutes: int
    default_expire_hours: int
    max_expire_hours: int
    download_ticket_ttl_minutes: int
    max_download_limit: int
    public_upload_enabled: bool
    upload_access_code: str
    admin_username: str
    admin_password_hash: str
    admin_token_expire_hours: int
    allowed_origins: tuple[str, ...]
    cleanup_interval_minutes: int

    @classmethod
    def from_env(cls) -> Settings:
        origins = tuple(
            item.strip()
            for item in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173").split(",")
            if item.strip()
        )
        return cls(
            app_name=os.getenv("APP_NAME", "CodeDrop"),
            app_env=os.getenv("APP_ENV", "development"),
            app_secret=os.getenv("APP_SECRET", "dev-app-secret-change-me"),
            download_secret=os.getenv("DOWNLOAD_SECRET", "dev-download-secret-change-me"),
            database_path=Path(os.getenv("DATABASE_PATH", "data/codedrop.db")),
            storage_root=Path(os.getenv("STORAGE_ROOT", "storage")),
            chunk_size=_int("CHUNK_SIZE_BYTES", 5 * MIB),
            max_chunk_size=_int("MAX_CHUNK_SIZE_BYTES", 6 * MIB),
            max_file_size=_int("MAX_FILE_SIZE_BYTES", HARD_MAX_FILE_SIZE),
            storage_quota=_int("STORAGE_QUOTA_BYTES", 5 * GIB),
            disk_reserve=_int("DISK_RESERVE_BYTES", 2 * GIB),
            upload_session_ttl_minutes=_int("UPLOAD_SESSION_TTL_MINUTES", 120),
            default_expire_hours=_int("DEFAULT_SHARE_EXPIRE_HOURS", 6),
            max_expire_hours=_int("MAX_SHARE_EXPIRE_HOURS", 24),
            download_ticket_ttl_minutes=_int("DOWNLOAD_TICKET_TTL_MINUTES", 30),
            max_download_limit=_int("MAX_DOWNLOAD_LIMIT", 5),
            public_upload_enabled=_bool("PUBLIC_UPLOAD_ENABLED", True),
            upload_access_code=os.getenv("UPLOAD_ACCESS_CODE", ""),
            admin_username=os.getenv("ADMIN_USERNAME", "admin"),
            admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", ""),
            admin_token_expire_hours=_int("ADMIN_TOKEN_EXPIRE_HOURS", 8),
            allowed_origins=origins,
            cleanup_interval_minutes=_int("CLEANUP_INTERVAL_MINUTES", 30),
        )

    def validate(self) -> None:
        errors: list[str] = []
        environment = self.app_env.strip().lower()
        if environment not in {"development", "test", "production"}:
            errors.append("APP_ENV must be development, test or production")
        positive_values = {
            "CHUNK_SIZE_BYTES": self.chunk_size,
            "MAX_CHUNK_SIZE_BYTES": self.max_chunk_size,
            "MAX_FILE_SIZE_BYTES": self.max_file_size,
            "STORAGE_QUOTA_BYTES": self.storage_quota,
            "UPLOAD_SESSION_TTL_MINUTES": self.upload_session_ttl_minutes,
            "DEFAULT_SHARE_EXPIRE_HOURS": self.default_expire_hours,
            "MAX_SHARE_EXPIRE_HOURS": self.max_expire_hours,
            "DOWNLOAD_TICKET_TTL_MINUTES": self.download_ticket_ttl_minutes,
            "MAX_DOWNLOAD_LIMIT": self.max_download_limit,
            "ADMIN_TOKEN_EXPIRE_HOURS": self.admin_token_expire_hours,
            "CLEANUP_INTERVAL_MINUTES": self.cleanup_interval_minutes,
        }
        for name, value in positive_values.items():
            if value <= 0:
                errors.append(f"{name} must be greater than 0")

        if self.disk_reserve < 0:
            errors.append("DISK_RESERVE_BYTES must be at least 0")
        if self.chunk_size > self.max_chunk_size:
            errors.append("CHUNK_SIZE_BYTES cannot exceed MAX_CHUNK_SIZE_BYTES")
        if self.max_file_size > HARD_MAX_FILE_SIZE:
            errors.append(f"MAX_FILE_SIZE_BYTES cannot exceed {HARD_MAX_FILE_SIZE}")
        if self.max_file_size > self.storage_quota:
            errors.append("MAX_FILE_SIZE_BYTES cannot exceed STORAGE_QUOTA_BYTES")
        if self.default_expire_hours > self.max_expire_hours:
            errors.append("DEFAULT_SHARE_EXPIRE_HOURS cannot exceed MAX_SHARE_EXPIRE_HOURS")

        if environment == "production":
            for name, secret in (
                ("APP_SECRET", self.app_secret),
                ("DOWNLOAD_SECRET", self.download_secret),
            ):
                lowered = secret.strip().lower()
                if len(secret.strip()) < 32 or "change-me" in lowered or lowered.startswith("dev-"):
                    errors.append(f"{name} must be a strong, non-default production secret")
            access_code = self.upload_access_code.strip().lower()
            if not access_code or "change-me" in access_code:
                errors.append("UPLOAD_ACCESS_CODE must be non-default in production")
            if not is_password_hash(self.admin_password_hash.strip()):
                errors.append("ADMIN_PASSWORD_HASH must be a valid pbkdf2_sha256 hash")

        if errors:
            details = "\n".join(f"- {message}" for message in errors)
            raise ValueError(f"Invalid configuration:\n{details}")

    def prepare_directories(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.storage_root.mkdir(parents=True, exist_ok=True)
        for name in ("uploads", "merging", "files"):
            (self.storage_root / name).mkdir(exist_ok=True)


settings = Settings.from_env()
