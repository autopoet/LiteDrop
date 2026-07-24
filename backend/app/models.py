from __future__ import annotations

from peewee import (
    AutoField,
    BigIntegerField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    Model,
    TextField,
)

from app.core.database import database_proxy
from app.core.time import utc_now


class BaseModel(Model):
    class Meta:
        database = database_proxy


class UploadSession(BaseModel):
    id = CharField(primary_key=True, max_length=36)
    file_name = TextField()
    total_size = BigIntegerField()
    chunk_size = IntegerField()
    total_chunks = IntegerField()
    status = CharField(default="uploading", index=True)
    expire_hours = IntegerField()
    download_limit = IntegerField()
    client_ip_hash = CharField(null=True, index=True)
    share_id = CharField(null=True)
    created_at = DateTimeField(default=utc_now, index=True)
    updated_at = DateTimeField(default=utc_now)
    expires_at = DateTimeField(index=True)


class UploadPart(BaseModel):
    id = AutoField()
    upload = ForeignKeyField(
        UploadSession, backref="parts", on_delete="CASCADE", column_name="upload_id"
    )
    part_number = IntegerField()
    size = IntegerField()
    sha256 = CharField(max_length=64)
    created_at = DateTimeField(default=utc_now)

    class Meta:
        indexes = ((("upload", "part_number"), True),)


class ShareFile(BaseModel):
    id = CharField(primary_key=True, max_length=36)
    code = CharField(max_length=6, unique=True, index=True)
    original_name = TextField()
    stored_name = TextField(unique=True)
    relative_path = TextField()
    size = BigIntegerField()
    sha256 = CharField(max_length=64)
    mime_type = TextField(null=True)
    download_limit = IntegerField()
    download_count = IntegerField(default=0)
    created_at = DateTimeField(default=utc_now, index=True)
    expires_at = DateTimeField(index=True)
    deleted_at = DateTimeField(null=True, index=True)


MODELS = (UploadSession, UploadPart, ShareFile)
