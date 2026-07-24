from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class UploadCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    total_size: int = Field(gt=0)
    expire_hours: int = Field(default=6, gt=0)
    download_limit: int = Field(default=3, gt=0)


class AdminLogin(BaseModel):
    username: str
    password: str


class UploadView(BaseModel):
    upload_id: str
    file_name: str
    total_size: int
    chunk_size: int
    total_chunks: int
    uploaded_parts: list[int]
    uploaded_bytes: int
    status: str
    expires_at: datetime
