#!/usr/bin/env python3
"""Regenerate the PostgreSQL schema from the SQLAlchemy models.

    python scripts/generate_migration.py > migrations/0001_initial.sql

The models in ``src/db/tables.py`` are the source of truth; this keeps the
checked-in SQL honest rather than hand-maintained.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.dialects import postgresql  # noqa: E402
from sqlalchemy.schema import CreateIndex, CreateTable  # noqa: E402

from src.db.tables import Base  # noqa: E402

HEADER = """-- LaunchTrace initial schema (PostgreSQL / Supabase).
--
-- Generated from src/db/tables.py by scripts/generate_migration.py.
-- Do not hand-edit: change the models and regenerate.
--
-- Apply with:
--     psql "$DATABASE_URL" -f migrations/0001_initial.sql
-- or paste into the Supabase SQL editor.

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
