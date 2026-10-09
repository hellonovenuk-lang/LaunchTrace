#!/usr/bin/env python3
"""Regenerate the full PostgreSQL schema from the SQLAlchemy models.

    python scripts/generate_migration.py > migrations/0001_initial.sql

Alembic (``src/db/alembic/``) is the canonical, versioned schema: production
databases are migrated with ``alembic upgrade head`` or
``python -m src.pipeline init-db``. This file is the full current schema in one
script, kept for pasting into the Supabase SQL editor. CI checks it is in step
with the models.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from src.db.tables import Base  # noqa: E402

HEADER = """-- LaunchTrace full schema (PostgreSQL / Supabase), as of the newest revision.
--
-- Generated from src/db/tables.py by scripts/generate_migration.py.
-- Do not hand-edit: change the models, add an Alembic revision, and regenerate.
--
-- Alembic (src/db/alembic/) is canonical. Prefer:
--     DATABASE_URL=... alembic upgrade head      (or: python -m src.pipeline init-db)
-- This file is the convenience copy for pasting into the Supabase SQL editor:
--     psql "$DATABASE_URL" -f migrations/0001_initial.sql
-- Only for a NEW, empty database. It has no alembic_version table; the first
-- init-db recognises the schema as complete and stamps it at head. For an
-- existing database never paste this: run init-db, which migrates in place.

BEGIN;
"""

FOOTER = """
COMMIT;
"""


def main() -> None:
    print(HEADER)
    dialect = postgresql.dialect()
    for table in Base.metadata.sorted_tables:
        statement = str(CreateTable(table).compile(dialect=dialect)).strip()
        print(statement.replace("CREATE TABLE", "CREATE TABLE IF NOT EXISTS", 1) + ";")
        print()
        for index in sorted(table.indexes, key=lambda i: i.name or ""):
            index_sql = str(CreateIndex(index).compile(dialect=dialect)).strip()
            print(index_sql.replace("CREATE INDEX", "CREATE INDEX IF NOT EXISTS", 1) + ";")
        if table.indexes:
            print()
    print(FOOTER)


if __name__ == "__main__":
    main()
