"""Database engine and session management.

PostgreSQL (Supabase or any other host) is the production target.  SQLite is
supported so the project runs locally and in CI with no database server.

The schema is owned by Alembic (``src/db/alembic/``). ``init_db`` brings any
database to the newest revision, adopting a pre-Alembic one without data loss.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import (
    Column,
    Connection,
    Engine,
    ForeignKey,
    Index,
    MetaData,
    Table,
    UniqueConstraint,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.orm import Session, sessionmaker

from src.db.baseline import BASELINE_REVISION, BASELINE_SCHEMA
from src.db.tables import Base
from src.logging_setup import get_logger
from src.settings import get_settings

log = get_logger(__name__)

ALEMBIC_DIR = Path(__file__).resolve().parent / "alembic"


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


# ---------------------------------------------------------------------------
# migrations
# ---------------------------------------------------------------------------


def alembic_config(database_url: str | None = None, connection: Connection | None = None) -> Config:
    """An Alembic ``Config`` for the packaged migration environment.

    Built in code rather than read from ``alembic.ini`` so it works wherever the
    package is installed, including the Docker image, which has no ini file.
    """
    cfg = Config()
    cfg.set_main_option("script_location", str(ALEMBIC_DIR))
    if database_url:
        cfg.attributes["database_url"] = database_url
    if connection is not None:
        cfg.attributes["connection"] = connection
    return cfg


@lru_cache(maxsize=1)
def head_revision() -> str:
    """The newest migration revision shipped with this code."""
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:  # pragma: no cover - the package always ships revisions
        raise RuntimeError(f"No Alembic revisions found in {ALEMBIC_DIR}")
    return head


def current_revision(connection: Connection) -> str | None:
    """The revision a database is stamped with, or None if it never has been."""
    return MigrationContext.configure(connection).get_current_revision()


def init_db(database_url: str | None = None) -> None:
    """Bring the database to the newest schema (``alembic upgrade head``).

    Starting points, none of which ever loses data:

    * **already at head** — nothing to do. One cheap query, so this stays safe
      to call at the top of every command and in every test.
    * **an empty SQLite file** — ``create_all`` then ``stamp head``. That is
      exactly the schema the migrations build (``tests/test_migrations.py``
      proves it) and much faster, which matters because the test suite and
      every CLI command call this.
    * **a pre-Alembic database** (tables present, no ``alembic_version``) — the
      old additive path brings it up to the frozen baseline (missing baseline
      tables created, missing baseline columns added); it is stamped
      ``0001_baseline`` and the later revisions run as normal. If it already
      has every table and column of the current models (made by ``create_all``
      from this very code), it is simply stamped ``head``.
    * anything else, including an empty PostgreSQL database — ``upgrade head``.
    """
    engine = get_engine(database_url)
    head = head_revision()
    with engine.connect() as connection:
        current = current_revision(connection)
    if current == head:
        return

    outcome = "upgraded"
    added: list[str] = []
    with engine.begin() as connection:
        cfg = alembic_config(connection=connection)
        existing = _user_tables(connection) if current is None else set()
        if current is None and not existing and engine.dialect.name == "sqlite":
            Base.metadata.create_all(connection)
            command.stamp(cfg, "head")
            outcome = "created"
        elif current is None and existing and _has_every_model_column(connection):
            command.stamp(cfg, "head")
            outcome = "stamped"
        elif current is None and existing:
            added = _adopt_legacy(connection)
            command.stamp(cfg, BASELINE_REVISION)
            command.upgrade(cfg, "head")
            outcome = "adopted"
        else:
            command.upgrade(cfg, "head")
    log.info(
        "db.initialised",
        url=str(engine.url).split("@")[-1],
        outcome=outcome,
        from_revision=current,
        to_revision=head,
        columns_added=len(added),
    )


def _user_tables(connection: Connection) -> set[str]:
    return {
        name
        for name in inspect(connection).get_table_names()
        if name != "alembic_version" and not name.startswith("sqlite_")
    }


def _has_every_model_column(connection: Connection) -> bool:
    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in tables:
            return False
        present = {c["name"] for c in inspector.get_columns(table.name)}
        if any(column.name not in present for column in table.columns):
            return False
    return True


def _adopt_legacy(connection: Connection) -> list[str]:
    """Bring a pre-Alembic database up to the frozen ``0001_baseline`` schema.

    Additive only: missing baseline tables are created and missing baseline
    columns are added. Nothing is dropped, renamed or retyped, and nothing a
    later revision adds is touched here — that revision adds it itself.
    """
    existing = _user_tables(connection)
    baseline = baseline_metadata()
    missing = [table for name, table in baseline.tables.items() if name not in existing]
    if missing:
        baseline.create_all(connection, tables=missing)
        for table in missing:
            log.info("db.table_created", table=table.name)
    added = _add_missing_columns(connection, BASELINE_SCHEMA)
    # A column added just now has no index yet; nor does an older index the
    # baseline gained. Indexes are pure additions, so create any that are absent.
    inspector = inspect(connection)
    for name in existing & set(baseline.tables):
        present = {index["name"] for index in inspector.get_indexes(name)}
        for index in baseline.tables[name].indexes:
            if index.name not in present:
                index.create(connection)
                log.info("db.index_created", table=name, index=index.name)
    return added


@lru_cache(maxsize=1)
def baseline_metadata() -> MetaData:
    """The baseline tables, copied from the models with later columns left out."""
    metadata = MetaData()
    for name, column_names in BASELINE_SCHEMA.items():
        source = Base.metadata.tables[name]
        keep = set(column_names)
        columns = [
            Column(
                column.name,
                column.type,
                *[
                    ForeignKey(fk.target_fullname, ondelete=fk.ondelete, name=fk.name)
                    for fk in column.foreign_keys
                ],
                primary_key=column.primary_key,
                nullable=column.nullable,
                index=column.index,
                unique=column.unique,
            )
            for column in source.columns
            if column.name in keep
        ]
        table = Table(name, metadata, *columns)
        for constraint in source.constraints:
            if isinstance(constraint, UniqueConstraint) and all(
                c.name in keep for c in constraint.columns
            ):
                table.append_constraint(
                    UniqueConstraint(*[c.name for c in constraint.columns], name=constraint.name)
                )
        present = {index.name for index in table.indexes}
        for index in source.indexes:
            if index.name in present or not all(c.name in keep for c in index.columns):
                continue
            Index(index.name, *[table.c[c.name] for c in index.columns], unique=index.unique)
    return metadata


def _add_missing_columns(
    connection: Connection, schema: dict[str, tuple[str, ...]] | None = None
) -> list[str]:
    """Add columns the models define but an existing table lacks, additively.

    ``schema`` restricts which tables and columns are considered — the frozen
    baseline, when adopting a pre-Alembic database.
    """
    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    dialect = connection.dialect
    added: list[str] = []

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        if schema is not None and table.name not in schema:
            continue
        allowed = set(schema[table.name]) if schema is not None else None
        present = {column["name"] for column in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in present:
                continue
            if allowed is not None and column.name not in allowed:
                continue
            if not column.nullable and column.default is None:
                # Adding a NOT NULL column with no default to a table that
                # already has rows cannot succeed. Say so rather than
                # failing with a database error nobody can act on.
                log.warning(
                    "db.column_needs_migration",
                    table=table.name,
                    column=column.name,
                    detail="not nullable and has no default — add it in an Alembic revision",
                )
                continue
            spec = column.type.compile(dialect=dialect)
            connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {column.name} {spec}"))
            added.append(f"{table.name}.{column.name}")
            log.info("db.column_added", table=table.name, column=column.name)
    return added
