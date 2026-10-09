"""Revision 0003_outcomes: up, down (keeping every other row), and no drift."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect, text

from src.db.engine import alembic_config, current_revision, get_engine, head_revision, init_db
from src.db.tables import Base


def _url(tmp_path: Path) -> str:
    return f"sqlite:///{tmp_path / 'outcomes.sqlite'}"


def _revision(url: str) -> str | None:
    with get_engine(url).connect() as c:
        return current_revision(c)


def test_head_is_0003_outcomes():
    assert head_revision() == "0003_outcomes"


def test_upgrade_creates_outcomes_without_drift(tmp_path):
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    inspector = inspect(get_engine(url))
    assert "outcomes" in inspector.get_table_names()
    columns = {c["name"] for c in inspector.get_columns("outcomes")}
    assert columns == {
        "id",
        "brand_id",
        "horizon_months",
        "label",
        "criteria_met",
        "evidence",
        "labelled_at",
        "as_of_date",
        "labeller_version",
    }
    uniques = [tuple(u["column_names"]) for u in inspector.get_unique_constraints("outcomes")]
    assert ("brand_id", "horizon_months", "labeller_version") in uniques
    fks = inspector.get_foreign_keys("outcomes")
    assert fks[0]["referred_table"] == "brands"
    assert (fks[0].get("options") or {}).get("ondelete") == "CASCADE"
    with get_engine(url).connect() as c:
        assert compare_metadata(MigrationContext.configure(c), Base.metadata) == []


def test_downgrade_to_0002_drops_only_outcomes(tmp_path):
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "head")
    now = datetime.now(UTC)
    with get_engine(url).begin() as c:
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
                "INSERT INTO outcomes (brand_id, horizon_months, label, criteria_met, evidence,"
                " labelled_at, as_of_date, labeller_version) VALUES (1, 3, 'unknown', '{}',"
                " '[]', :now, '2026-10-09', '1')"
            ),
            {"now": now},
        )

    command.downgrade(alembic_config(url), "0002_brands")

    assert _revision(url) == "0002_brands"
    tables = set(inspect(get_engine(url)).get_table_names())
    assert "outcomes" not in tables
    assert {"brands", "observations", "stage_changes"} <= tables
    with get_engine(url).connect() as c:
        assert c.execute(text("SELECT brand_key FROM brands")).scalar_one() == "ch:1"

    command.upgrade(alembic_config(url), "head")
    assert _revision(url) == "0003_outcomes"


def test_init_db_brings_a_0002_database_to_head(tmp_path):
    url = _url(tmp_path)
    command.upgrade(alembic_config(url), "0002_brands")
    init_db(url)
    assert _revision(url) == "0003_outcomes"
    assert "outcomes" in inspect(get_engine(url)).get_table_names()
