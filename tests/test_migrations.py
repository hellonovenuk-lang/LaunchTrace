"""Alembic migrations: they build the models' schema, round-trip, and never lose data."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import Engine

from src.db.baseline import BASELINE_REVISION, BASELINE_SCHEMA
from src.db.engine import (
    alembic_config,
    baseline_metadata,
    current_revision,
    get_engine,
    head_revision,
    init_db,
)
from src.db.tables import Base

NEW_TABLES = {"brands", "observations", "stage_changes"}


def _url(tmp_path: Path, name: str = "m.sqlite") -> str:
    return f"sqlite:///{tmp_path / name}"


def _revision(engine: Engine) -> str | None:
    with engine.connect() as connection:
        return current_revision(connection)


def _upgrade(url: str, target: str = "head") -> None:
    command.upgrade(alembic_config(url), target)


def _downgrade(url: str, target: str) -> None:
    command.downgrade(alembic_config(url), target)


def _schema(engine: Engine) -> dict:
    """Everything about a schema that matters, in comparable form."""
    inspector = inspect(engine)
    out: dict = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        out[table] = {
            "columns": sorted(
                (c["name"], str(c["type"]), c["nullable"], bool(c.get("primary_key")))
                for c in inspector.get_columns(table)
            ),
            "indexes": sorted(
                (i["name"], tuple(i["column_names"]), bool(i["unique"]))
                for i in inspector.get_indexes(table)
            ),
            "uniques": sorted(
                tuple(u["column_names"]) for u in inspector.get_unique_constraints(table)
            ),
            "foreign_keys": sorted(
                (
                    tuple(f["constrained_columns"]),
                    f["referred_table"],
                    tuple(f["referred_columns"]),
                    (f.get("options") or {}).get("ondelete"),
                )
                for f in inspector.get_foreign_keys(table)
            ),
            "pk": tuple(inspector.get_pk_constraint(table)["constrained_columns"]),
        }
    return out


def _drift(engine: Engine) -> list:
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection), Base.metadata)


class TestUpgrade:
    def test_upgrade_head_on_an_empty_database(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url)
        engine = get_engine(url)
        assert _revision(engine) == head_revision()
        assert set(inspect(engine).get_table_names()) >= NEW_TABLES
        assert set(BASELINE_SCHEMA) <= set(inspect(engine).get_table_names())

    def test_models_and_migrations_do_not_drift(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url)
        assert _drift(get_engine(url)) == []

    def test_downgrade_to_base_and_back_up(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url)
        _downgrade(url, "base")
        engine = get_engine(url)
        assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
        assert _revision(engine) is None
        _upgrade(url)
        assert _revision(engine) == head_revision()
        assert _drift(engine) == []

    def test_baseline_revision_creates_exactly_the_frozen_baseline(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url, BASELINE_REVISION)
        inspector = inspect(get_engine(url))
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert tables == set(BASELINE_SCHEMA)
        for table, columns in BASELINE_SCHEMA.items():
            assert [c["name"] for c in inspector.get_columns(table)] == list(columns), table


class TestDowngradeKeepsData:
    def test_downgrading_brands_leaves_baseline_rows_intact(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url)
        engine = get_engine(url)
        now = datetime.now(UTC)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO brands (id, brand_uid, brand_key, brand_name, applicant_type,"
                    " first_seen_journal, first_seen_at, last_seen_journal, current_stage,"
                    " current_score, current_band, created_at, updated_at) VALUES (1, 'b_1',"
                    " 'ch:1', 'X', 'corporate', '2025-050', :now, '2025-050', 'unknown', 50,"
                    " 'MEDIUM', :now, :now)"
                ),
                {"now": now},
            )
            c.execute(
                text(
                    "INSERT INTO opportunities (dedupe_key, run_id, journal_number,"
                    " trademark_number, applicant_type, launch_stage, retail_presence,"
                    " launchtrace_score, score_band, score_reasons, buying_intent, nice_classes,"
                    " evidence_urls, review_state, delivered, suppressed, created_at, brand_id)"
                    " VALUES ('k1', 'run_1', '2025-050', 'UK1', 'corporate', 'unknown',"
                    " 'unknown', 61, 'MEDIUM', '[]', '{}', '[30]', '[]', 'pending', 0, 0, :now, 1)"
                ),
                {"now": now},
            )
            c.execute(
                text(
                    "INSERT INTO score_events (dedupe_key, run_id, score, band, reasons,"
                    " negative_reasons, scoring_config_version, created_at, brand_id)"
                    " VALUES ('k1', 'run_1', 61, 'MEDIUM', '[]', '[]', '1.0', :now, 1)"
                ),
                {"now": now},
            )
            c.execute(
                text(
                    "INSERT INTO customers (company, plan_key, subscription_status,"
                    " delivery_enabled, founding_customer, created_at)"
                    " VALUES ('PackCo', 'founding_monthly', 'active', 1, 1, :now)"
                ),
                {"now": now},
            )

        _downgrade(url, BASELINE_REVISION)

        inspector = inspect(engine)
        assert not NEW_TABLES & set(inspector.get_table_names())
        assert "brand_id" not in {c["name"] for c in inspector.get_columns("opportunities")}
        assert "brand_id" not in {c["name"] for c in inspector.get_columns("score_events")}
        with engine.connect() as c:
            assert c.execute(
                text("SELECT trademark_number, launchtrace_score FROM opportunities")
            ).all() == [("UK1", 61)]
            assert c.execute(text("SELECT score FROM score_events")).scalar_one() == 61
            assert c.execute(text("SELECT company FROM customers")).scalar_one() == "PackCo"
        assert _revision(engine) == BASELINE_REVISION

        _upgrade(url)
        with engine.connect() as c:
            assert c.execute(text("SELECT brand_id FROM opportunities")).scalar_one() is None


class TestInitDb:
    def test_fresh_sqlite_fast_path_equals_alembic_upgrade(self, tmp_path):
        fast, migrated = _url(tmp_path, "fast.sqlite"), _url(tmp_path, "migrated.sqlite")
        init_db(fast)
        _upgrade(migrated)
        assert _revision(get_engine(fast)) == head_revision()
        assert _schema(get_engine(fast)) == _schema(get_engine(migrated))
        assert _drift(get_engine(fast)) == []

    def test_init_db_at_head_is_a_no_op(self, tmp_path):
        url = _url(tmp_path)
        init_db(url)
        with get_engine(url).begin() as c:
            c.execute(
                text(
                    "INSERT INTO suppression_rules (rule_type, value, active, created_at)"
                    " VALUES ('company', 'X', 1, '2026-01-01')"
                )
            )
        init_db(url)
        with get_engine(url).connect() as c:
            assert c.execute(text("SELECT count(*) FROM suppression_rules")).scalar_one() == 1

    def test_a_database_made_by_create_all_is_stamped_not_rebuilt(self, tmp_path):
        url = _url(tmp_path)
        engine = get_engine(url)
        Base.metadata.create_all(engine)
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO suppression_rules (rule_type, value, active, created_at)"
                    " VALUES ('company', 'X', 1, '2026-01-01')"
                )
            )
        init_db(url)
        assert _revision(engine) == head_revision()
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM suppression_rules")).scalar_one() == 1

    def test_init_db_upgrades_a_database_at_the_baseline(self, tmp_path):
        url = _url(tmp_path)
        _upgrade(url, BASELINE_REVISION)
        init_db(url)
        engine = get_engine(url)
        assert _revision(engine) == head_revision()
        assert _drift(engine) == []


class TestLegacyAdoption:
    """A database created before Alembic: tables present, no alembic_version."""

    def _legacy(self, url: str) -> Engine:
        """The pre-Alembic schema, a little older still: one column and one table missing."""
        engine = create_engine(url)
        metadata = baseline_metadata()
        tables = [t for name, t in metadata.tables.items() if name != "webhook_events"]
        metadata.create_all(engine, tables=tables)
        with engine.begin() as c:
            # SQLite >= 3.35 can drop a plain column: simulate a database from
            # before customers.prospect_id existed.
            c.execute(text("DROP INDEX ix_customers_prospect_id"))
            c.execute(text("ALTER TABLE customers DROP COLUMN prospect_id"))
            c.execute(
                text(
                    "INSERT INTO journals (journal_number, source_name, publication_date,"
                    " byte_size, record_count, retrieved_at, processing_status)"
                    " VALUES ('2025-050', 'fixture_journal_xml', '2025-12-12', 1, 8,"
                    " '2025-12-12 10:00:00', 'processed')"
                )
            )
            c.execute(
                text(
                    "INSERT INTO trademark_records (journal_number, dedupe_key, trademark_number,"
                    " applicant_name, nice_classes, goods_text_available, series_count,"
                    " source_name, created_at) VALUES ('2025-050', 'k1', 'UK1', 'Acme Ltd',"
                    " '[30]', 0, 0, 'fixture_journal_xml', '2025-12-12 10:00:00')"
                )
            )
            c.execute(
                text(
                    "INSERT INTO customers (company, plan_key, subscription_status,"
                    " delivery_enabled, founding_customer, created_at)"
                    " VALUES ('PackCo', 'founding_monthly', 'active', 1, 1, '2025-01-01')"
                )
            )
        engine.dispose()
        return engine

    def test_legacy_database_is_adopted_without_data_loss(self, tmp_path):
        url = _url(tmp_path)
        self._legacy(url)
        assert "alembic_version" not in inspect(create_engine(url)).get_table_names()

        init_db(url)

        engine = get_engine(url)
        assert _revision(engine) == head_revision()
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        assert {"webhook_events"} | NEW_TABLES <= tables
        assert "prospect_id" in {c["name"] for c in inspector.get_columns("customers")}
        assert "brand_id" in {c["name"] for c in inspector.get_columns("opportunities")}
        with engine.connect() as c:
            assert c.execute(text("SELECT journal_number FROM journals")).scalar_one() == "2025-050"
            assert c.execute(text("SELECT applicant_name FROM trademark_records")).scalar_one() == (
                "Acme Ltd"
            )
            assert c.execute(text("SELECT company FROM customers")).scalar_one() == "PackCo"
        # Everything the models need is there; nothing the models define is missing.
        missing = [d for d in _drift(engine) if isinstance(d, tuple) and d[0].startswith("add_")]
        assert missing == []

    def test_adoption_is_idempotent(self, tmp_path):
        url = _url(tmp_path)
        self._legacy(url)
        init_db(url)
        init_db(url)
        with get_engine(url).connect() as c:
            assert c.execute(text("SELECT count(*) FROM journals")).scalar_one() == 1


class TestAlembicEnv:
    def test_alembic_env_takes_the_url_from_settings(self, tmp_path, monkeypatch):
        """``alembic upgrade head`` with no URL uses DATABASE_URL, like the app."""
        from src.settings import get_settings

        url = _url(tmp_path, "from_env.sqlite")
        monkeypatch.setenv("DATABASE_URL", url)
        get_settings.cache_clear()
        try:
            command.upgrade(alembic_config(), "head")
        finally:
            get_settings.cache_clear()
        assert _revision(get_engine(url)) == head_revision()


@pytest.mark.skipif(
    not os.environ.get("LAUNCHTRACE_TEST_POSTGRES_URL"),
    reason="set LAUNCHTRACE_TEST_POSTGRES_URL to run against a real PostgreSQL",
)
def test_round_trip_on_postgres():
    url = os.environ["LAUNCHTRACE_TEST_POSTGRES_URL"]
    _upgrade(url)
    _downgrade(url, "base")
    _upgrade(url)
    engine = create_engine(url)
    assert _revision(engine) == head_revision()
    assert _drift(engine) == []
    engine.dispose()


def test_journal_dates_are_still_dates_after_migration(tmp_path):
    """A Date column must come back as a date, not a string, on the migrated schema."""
    url = _url(tmp_path)
    _upgrade(url)
    from sqlalchemy.orm import Session

    from src.db.tables import Journal

    with Session(get_engine(url)) as session:
        session.add(
            Journal(
                journal_number="2025-050",
                publication_date=date(2025, 12, 12),
            )
        )
        session.commit()
        row = session.query(Journal).one()
        assert row.publication_date == date(2025, 12, 12)


class TestIntermediateAdoption:
    """LOW-4 (D-706): an unstamped database already at a later revision's schema."""

    def test_everything_but_outcomes_is_stamped_at_0002_and_upgraded(self, tmp_path):
        url = _url(tmp_path)
        engine = create_engine(url)
        Base.metadata.create_all(
            engine, tables=[t for t in Base.metadata.sorted_tables if t.name != "outcomes"]
        )
        with engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO suppression_rules (rule_type, value, active, created_at)"
                    " VALUES ('company', 'X', 1, '2026-01-01')"
                )
            )
        engine.dispose()

        init_db(url)

        engine = get_engine(url)
        assert _revision(engine) == head_revision()
        assert "outcomes" in inspect(engine).get_table_names()
        assert _drift(engine) == []
        with engine.connect() as c:
            assert c.execute(text("SELECT count(*) FROM suppression_rules")).scalar_one() == 1

    def test_highest_present_revision_walks_up_from_the_baseline(self, tmp_path):
        from src.db.engine import _highest_present_revision

        url = _url(tmp_path)
        engine = create_engine(url)
        for target in (BASELINE_REVISION, "0002_brands", "0003_outcomes"):
            _upgrade(url, target)
            with engine.connect() as c:
                assert _highest_present_revision(c) == target

    def test_revision_objects_cover_every_revision(self):
        from alembic.script import ScriptDirectory

        from src.db.engine import REVISION_OBJECTS

        script = ScriptDirectory.from_config(alembic_config("sqlite://"))
        later = [r.revision for r in reversed(list(script.walk_revisions()))][1:]
        assert [name for name, _, _ in REVISION_OBJECTS] == later
