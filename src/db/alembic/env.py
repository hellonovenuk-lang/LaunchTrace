"""Alembic environment for LaunchTrace.

Where the database comes from, in order:

1. ``config.attributes["connection"]`` — an open SQLAlchemy connection handed
   in by ``src.db.engine.init_db`` (and by the tests);
2. ``config.attributes["database_url"]`` or the ``sqlalchemy.url`` main option;
3. ``src.settings.get_settings().database_url`` — i.e. ``DATABASE_URL``, or the
   local SQLite file when it is unset.

``render_as_batch`` is on for SQLite, which cannot ALTER most things in place.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection, create_engine, pool

from src.db.tables import Base

config = context.config

# Only configure logging from the ini file when run from the CLI; a programmatic
# upgrade must not reconfigure the application's logging.
if config.config_file_name is not None and not config.attributes.get("connection"):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    from src.settings import get_settings, normalise_database_url

    url = config.attributes.get("database_url") or config.get_main_option("sqlalchemy.url")
    if url:
        return normalise_database_url(str(url))

    return get_settings().database_url


def _configure(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        render_as_batch=connection.dialect.name == "sqlite",
        compare_type=True,
    )


def run_migrations_offline() -> None:
    url = _url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=url.startswith("sqlite"),
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(_url(), poolclass=pool.NullPool, future=True)
    try:
        with engine.connect() as conn:
            _configure(conn)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
