from collections.abc import Iterator

from sqlalchemy import MetaData, Table, create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings

engine = create_engine(settings.db_url, pool_pre_ping=True, pool_recycle=1800, future=True, isolation_level="READ COMMITTED")
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)

metadata = MetaData()
_tables: dict[str, Table] = {}


def table(name: str) -> Table:
    """Reflect a table from the live Bugzilla schema (Bugzilla/DB/Schema.pm) on first use."""
    if name not in _tables:
        _tables[name] = Table(name, metadata, autoload_with=engine)
    return _tables[name]


def get_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
