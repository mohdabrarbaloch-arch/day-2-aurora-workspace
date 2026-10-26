"""Database engine and session management.

SQLite for zero-setup local runs, Postgres in production — the same SQLAlchemy
models serve both because nothing here is dialect specific.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def _engine_kwargs(url: str) -> dict[str, Any]:
    if url.startswith("sqlite"):
        # check_same_thread=False is required because FastAPI serves requests
        # from a thread pool; SQLite otherwise refuses cross-thread use.
        return {"connect_args": {"check_same_thread": False}}
    return {"pool_pre_ping": True, "pool_size": 5, "max_overflow": 10}


engine: Engine = create_engine(
    settings.database_url,
    echo=settings.sql_echo,
    future=True,
    **_engine_kwargs(settings.database_url),
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    """Enable foreign keys and WAL on SQLite.

    SQLite ignores FK constraints unless asked, which would silently allow
    orphan rows; WAL keeps reads from blocking during writes.
    """
    if "sqlite3" not in type(dbapi_connection).__module__:
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """Create tables. Real deployments should use migrations instead."""
    from app import models  # noqa: F401  (registers mappers)

    Base.metadata.create_all(bind=engine)
