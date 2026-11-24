"""SQLAlchemy 2.x on the existing Bugzilla schema. Tables are reflected from
the live database (the schema is owned by Bugzilla/DB/Schema.pm and
checksetup.pl), so there is no model file for modules to fight over:
``from app.core.db import t`` then ``t.bugs``, ``t.longdescs``..."""
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.orm import Session

from .config import database_url

engine = create_engine(database_url(), pool_pre_ping=True, pool_recycle=1800)
metadata = MetaData()


class _Tables:
    def __getattr__(self, name):
        if name not in metadata.tables:
            metadata.reflect(engine, only=[name])
        return metadata.tables[name]


t = _Tables()


def get_session():
    """One session per request. Services commit at the same points as Perl."""
    with Session(engine) as session:
        # Perl formats and compares every timestamp in the server time zone (UTC in docker).
        session.execute(text("SET time_zone = '+00:00'"))
        yield session


def db_now(session):
    """Perl stamps every write with the database clock, not the web server's."""
    return session.execute(text("SELECT LOCALTIMESTAMP(0)")).scalar_one()
