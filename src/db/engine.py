"""Database engine and session management.

PostgreSQL (Supabase or any other host) is the production target.  SQLite is
supported so the project runs locally and in CI with no database server.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from src.db.tables import Base
from src.logging_setup import get_logger
from src.settings import get_settings

log = get_logger(__name__)


def get_engine(database_url: str | None = None) -> Engine:
    """Engine for a database URL, created once per URL.

    The URL is resolved from settings *before* the cache is consulted: caching
    on the literal ``None`` argument would pin the first engine created and
    silently ignore a later configuration change.
    """
    return _engine_for(database_url or get_settings().database_url)


@lru_cache(maxsize=8)
def _engine_for(url: str) -> Engine:
    kwargs: dict = {"future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        kwargs.pop("pool_pre_ping")
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, _record):  # type: ignore[no-untyped-def]
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.close()

    return engine


def _session_factory(database_url: str | None = None) -> sessionmaker[Session]:
    return _factory_for(database_url or get_settings().database_url)


@lru_cache(maxsize=8)
def _factory_for(url: str) -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(url), expire_on_commit=False, future=True)


def get_session(database_url: str | None = None) -> Session:
    return _session_factory(database_url)()


@contextmanager
def session_scope(database_url: str | None = None) -> Iterator[Session]:
    session = get_session(database_url)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db(database_url: str | None = None) -> None:
    """Create any missing tables, and add any missing columns to existing ones.

    For PostgreSQL the versioned SQL in ``migrations/`` is the canonical
    schema; this is the convenience path for local SQLite work and tests.

    ``create_all`` only creates what is absent entirely, so a database created
    by an earlier version keeps its old columns and every query against a new
    one fails. Rather than asking an owner to delete their database — which
    would take their customer records with it — missing columns are added in
    place. Additive only: nothing is dropped, renamed or retyped here.
    """
    engine = get_engine(database_url)
    Base.metadata.create_all(engine)
    added = _add_missing_columns(engine)
    log.info("db.initialised", url=str(engine.url).split("@")[-1], columns_added=len(added))


def _add_missing_columns(engine: Engine) -> list[str]:
    """Bring existing tables up to the current model, additively."""
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    dialect = engine.dialect
    added: list[str] = []

    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            if table.name not in existing_tables:
                continue
            present = {column["name"] for column in inspector.get_columns(table.name)}
            for column in table.columns:
                if column.name in present:
                    continue
                if not column.nullable and column.default is None:
                    # Adding a NOT NULL column with no default to a table that
                    # already has rows cannot succeed. Say so rather than
                    # failing with a database error nobody can act on.
                    log.warning(
                        "db.column_needs_migration",
                        table=table.name,
                        column=column.name,
                        detail="not nullable and has no default — add it in migrations/",
                    )
                    continue
                spec = column.type.compile(dialect=dialect)
                connection.execute(
                    text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {spec}")
                )
                added.append(f"{table.name}.{column.name}")
                log.info("db.column_added", table=table.name, column=column.name)
    return added
