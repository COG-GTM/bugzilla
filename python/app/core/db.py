"""SQLAlchemy 2.x engine/session plumbing. One session per request, autocommit off."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

_engine = create_engine(
    settings().database_url,
    pool_pre_ping=True,
    pool_recycle=1800,
    isolation_level="READ COMMITTED",
    future=True,
)
SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=False)


def engine():
    return _engine


def get_session() -> Iterator[Session]:
    """FastAPI dependency: commit on success, roll back on any exception."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
