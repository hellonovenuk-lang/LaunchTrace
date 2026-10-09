"""backtest ingest: the weekly path with nothing paid, idempotent, capped; and `backtest run`."""

from __future__ import annotations

import json
import shutil

import pytest
from sqlalchemy import func, select

import src.commands as commands
from src.backtest.ingest import (
    DEFERRED,
    PROCESSED,
    SKIPPED,
    UNAVAILABLE,
    backtest_settings_overrides,
    format_ingest,
    run_ingest,
)
from src.db import session_scope
from src.db.tables import Brand, Journal, Observation, OpportunityRow, Outcome, TrademarkRecordRow
from src.ingest.fixture import FixtureJournalSource
from src.settings import DATA_DIR, get_settings


@pytest.fixture
def bt_env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """An isolated database and cache; fixture Companies House; a recording pipeline factory."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'bt.sqlite'}")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    # A live search key in the environment must still never be used.
    monkeypatch.setenv("SEARCH_PROVIDER", "tavily")
    monkeypatch.setenv("SEARCH_API_KEY", "not-a-real-key")
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "not-a-real-key")
    monkeypatch.setenv("DOMAIN_LAYER_ENABLED", "true")
    monkeypatch.setenv("COMPANY_REGISTRY_PROVIDER", "fixture")
    get_settings.cache_clear()
    seen: list = []
    real = commands.Pipeline

    def factory(settings, source, **kwargs):  # type: ignore[no-untyped-def]
        seen.append((settings, source, kwargs))
        if source.name == "ukipo_journal_xml" and settings.journal_source == "ukipo_http":
            # Never download in a test: serve the fixture journal instead.
            source = FixtureJournalSource(settings)
        return real(settings=settings, source=source, **kwargs)

    monkeypatch.setattr(commands, "Pipeline", factory)
    yield seen
    get_settings.cache_clear()


def _count(model) -> int:  # type: ignore[no-untyped-def]
    with session_scope() as session:
        return int(session.execute(select(func.count()).select_from(model)).scalar_one())


def test_overrides_switch_off_everything_paid_or_present_tense():
    o = backtest_settings_overrides("local", with_domain_layer=False)
    assert o["search_provider"] == "none" and o["search_api_key"] == ""
    assert o["llm_provider"] == "none" and o["llm_api_key"] == ""
    assert o["domain_layer_enabled"] is False
    assert o["journal_source"] == "local"


class TestIngest:
    def test_persists_like_weekly_with_nothing_paid(self, bt_env):
        outcomes = run_ingest("2025-050", "2025-050", source="fixture")

        assert [o.status for o in outcomes] == [PROCESSED]
        assert outcomes[0].search_calls == 0
        assert outcomes[0].opportunities > 0
        settings, _, kwargs = bt_env[0]
        assert settings.search_enabled is False
        assert settings.llm_enabled is False
        assert settings.domain_layer_enabled is False
        assert "backtest_runs" in str(kwargs["output_dir"])
        assert _count(Journal) == 1
        assert _count(TrademarkRecordRow) == 8
        assert _count(OpportunityRow) == outcomes[0].opportunities
        assert _count(Brand) > 0
        with session_scope() as session:
            sources = set(session.execute(select(Observation.source).distinct()).scalars())
            run_mode = session.execute(
                select(commands.PipelineRun.mode)  # type: ignore[attr-defined]
            ).scalar_one()
        assert "web_search" not in sources
        assert not sources & {"rdap", "dns", "homepage"}
        assert "companies_house" in sources  # free, and incorporation dates are PIT-safe
        assert run_mode == "backtest-ingest"

    def test_idempotent_unless_forced(self, bt_env):
        run_ingest("2025-050", "2025-050", source="fixture")
        observations = _count(Observation)

        again = run_ingest("2025-050", "2025-050", source="fixture")
        assert [o.status for o in again] == [SKIPPED]
        assert len(bt_env) == 1  # no second pipeline built, so nothing fetched
        assert _count(Observation) == observations
        assert _count(TrademarkRecordRow) == 8

        forced = run_ingest("2025-050", "2025-050", source="fixture", force=True)
        assert [o.status for o in forced] == [PROCESSED]
        assert _count(TrademarkRecordRow) == 8  # source rows are not duplicated
        assert _count(OpportunityRow) == forced[0].opportunities

    def test_missing_journals_are_reported_not_run(self, bt_env):
        outcomes = run_ingest("2025-049", "2025-050", source="fixture")
        assert [o.status for o in outcomes] == [UNAVAILABLE, PROCESSED]
        assert "no journal 2025-049" in format_ingest(outcomes)

    def test_cap_and_polite_delay_for_downloads(self, bt_env):
        sleeps: list[float] = []
        outcomes = run_ingest(
            "2025-048", "2025-050", source="ukipo_http", max_journals=2, sleep=sleeps.append
        )
        assert [o.status for o in outcomes][2] == DEFERRED
        assert len(bt_env) == 2
        # One pause between the two journals actually run, none before the first.
        assert sleeps == [15.0]
        text = format_ingest(outcomes)
        assert "deferred by the per-invocation cap" in text

    def test_domain_layer_only_on_request(self, bt_env):
        run_ingest("2025-050", "2025-050", source="fixture", with_domain_layer=True)
        assert bt_env[0][0].domain_layer_enabled is True

    def test_local_data_journal_end_to_end(self, bt_env, monkeypatch, tmp_path):
        """The real 2026-036 journal from data/journals, through the local source."""
        local = tmp_path / "journals"
        local.mkdir()
        shutil.copy(DATA_DIR / "journals" / "2026-036.xml.gz", local / "2026-036.xml.gz")
        monkeypatch.setenv("JOURNAL_LOCAL_DIR", str(local))
        get_settings.cache_clear()

        outcomes = run_ingest("2026-036", "2026-036", source="local")

        assert outcomes[0].status == PROCESSED, outcomes[0].detail
        assert outcomes[0].raw_records == 2946
        assert outcomes[0].search_calls == 0
        assert _count(Brand) > 0
        assert run_ingest("2026-036", "2026-036", source="local")[0].status == SKIPPED


def test_backtest_run_end_to_end_on_fixtures(bt_env, tmp_path, capsys):
    from src.pipeline import main

    assert main(["backtest", "ingest", "--from", "2025-050", "--to", "2025-050",
                 "--source", "fixture"]) == 0  # fmt: skip
    out_dir = tmp_path / "reports"
    assert main(["backtest", "run", "--as-of", "2026-10-09", "--out", str(out_dir),
                 "--note", "fixture run"]) == 0  # fmt: skip

    md = out_dir / "2026-10-09.md"
    js = out_dir / "2026-10-09.json"
    assert md.exists() and js.exists()
    data = json.loads(js.read_text())
    assert data["dataset"]["scored"] == data["dataset"]["brands"] > 0
    assert set(data["results"]["horizons"]) == {"3", "6"}
    assert "fixture run" in md.read_text()
    printed = capsys.readouterr().out
    assert "Labelled" in printed and "Report:" in printed
    assert _count(Outcome) == 2 * data["dataset"]["brands"]

    # label and report also work on their own.
    assert main(["backtest", "label", "--as-of", "2026-10-09"]) == 0
    assert main(["backtest", "report", "--out", str(out_dir)]) == 0


def test_ingest_cli_exit_codes(bt_env):
    from src.pipeline import main

    # Nothing available at all is a failure; skipping/processing is not.
    assert main(["backtest", "ingest", "--from", "2025-049", "--to", "2025-049",
                 "--source", "fixture"]) == 1  # fmt: skip
    assert main(["backtest", "ingest", "--from", "2025-050", "--to", "2025-050",
                 "--source", "fixture", "--max-journals", "1"]) == 0  # fmt: skip
