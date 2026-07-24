from __future__ import annotations

from pathlib import Path

from peewee import DatabaseProxy, SqliteDatabase

database_proxy = DatabaseProxy()
database: SqliteDatabase | None = None


def initialize_database(path: Path) -> SqliteDatabase:
    global database
    database = SqliteDatabase(
        path,
        pragmas={
            "journal_mode": "wal",
            "foreign_keys": 1,
            "busy_timeout": 5000,
            "synchronous": 1,
        },
        check_same_thread=False,
    )
    database_proxy.initialize(database)
    return database


def create_tables() -> None:
    # Import here to avoid a circular import during model declaration.
    from app.models import MODELS

    database_proxy.create_tables(MODELS, safe=True)


def close_database() -> None:
    if database is not None and not database.is_closed():
        database.close()
