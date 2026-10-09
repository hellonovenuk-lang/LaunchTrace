"""The weekly command: idempotency, the search cost guard, cache housekeeping,
and the 'first trade mark for this applicant' signal across runs."""

from __future__ import annotations

import os
import shutil
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

import src.commands as commands
from src.classify.pipeline import ProductClassifier
from src.db import session_scope
from src.db.repository import known_applicant_names
from src.db.tables import PipelineRun, TrademarkRecordRow
from src.enrich.budget import MONTHLY_BUDGET, PER_RUN_CAP, SearchBudget, SearchGuard
from src.enrich.companies_house import FixtureCompanyRegistry
from src.enrich.providers import FixtureSearchProvider
from src.enrich.web import WebEnricher
from src.ingest.discovery import journal_number_for_date
from src.ingest.fixture import FixtureJournalSource
from src.models import RunStatus
from src.pipeline_core import Pipeline
from src.settings import Settings, get_settings
from tests.conftest import FIXTURE_JOURNAL


class CountingSearchProvider(FixtureSearchProvider):
    """The fixture search provider, counting every call made to it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def search(self, query: str, limit: int = 8):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().search(query, limit=limit)


def _args(**overrides) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    base = {
        "command": "weekly",
        "source": "fixture",
        "journal": None,
        "date": None,
        "max_records": None,
        "no_db": False,
        "send": False,
        "force": False,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def weekly_env(monkeypatch, tmp_path):  # type: ignore[no-untyped-def]
    """An isolated database, fixture sources and a counting search provider."""
    url = f"sqlite:///{tmp_path / 'weekly.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("JOURNAL_SOURCE", "fixture")
    monkeypatch.setenv("CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("SEARCH_PROVIDER", "none")
    monkeypatch.setenv("LLM_PROVIDER", "none")
    monkeypatch.setenv("COMPANIES_HOUSE_API_KEY", "")
    monkeypatch.setenv("RESEND_API_KEY", "")
    monkeypatch.delenv("SEARCH_MAX_CALLS_PER_RUN", raising=False)
    get_settings.cache_clear()

    provider = CountingSearchProvider()
    fetches: list[str] = []
    real_fetch = FixtureJournalSource.fetch

    def counting_fetch(self, ref):  # type: ignore[no-untyped-def]
        fetches.append(ref.journal_number)
        return real_fetch(self, ref)

    monkeypatch.setattr(FixtureJournalSource, "fetch", counting_fetch)

    def factory(settings, source):  # type: ignore[no-untyped-def]
        return Pipeline(
            settings=settings,
            source=source,
            registry=FixtureCompanyRegistry(),
            classifier=ProductClassifier(settings, llm_provider=None),
            web=WebEnricher(provider=provider, settings=settings),
            output_dir=tmp_path / "runs",
        )

    monkeypatch.setattr(commands, "Pipeline", factory)
    yield SimpleNamespace(provider=provider, fetches=fetches, tmp=tmp_path, url=url)
    get_settings.cache_clear()


def _run_count() -> int:
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(PipelineRun)).scalar_one()


# ---------------------------------------------------------------------------
# idempotency
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_second_weekly_run_is_a_no_op(self, weekly_env, capsys):
        assert commands.cmd_weekly(_args()) == 0
        first_calls = weekly_env.provider.calls
        assert first_calls > 0
        assert weekly_env.fetches == ["2025-050"]
        assert _run_count() == 1
        capsys.readouterr()

        assert commands.cmd_weekly(_args()) == 0

        out = capsys.readouterr().out
        assert "already been processed" in out
        assert "2025-050" in out
        assert weekly_env.provider.calls == first_calls  # zero search calls
        assert weekly_env.fetches == ["2025-050"]  # no re-download
        assert _run_count() == 1  # no new run row

    def test_force_reprocesses(self, weekly_env):
        assert commands.cmd_weekly(_args()) == 0
        first_calls = weekly_env.provider.calls

        assert commands.cmd_weekly(_args(force=True)) == 0

        assert weekly_env.provider.calls > first_calls
        assert weekly_env.fetches == ["2025-050", "2025-050"]
        assert _run_count() == 2

    def test_an_explicit_journal_is_checked_too(self, weekly_env, capsys):
        assert commands.cmd_weekly(_args(journal="2025-050")) == 0
        capsys.readouterr()
        assert commands.cmd_weekly(_args(journal="2025-050")) == 0
        assert "already been processed" in capsys.readouterr().out
        assert weekly_env.fetches == ["2025-050"]

    def test_an_unresolvable_journal_still_fails_closed(self, weekly_env, monkeypatch):
        monkeypatch.setattr(commands, "send_failure_alert", lambda *a, **k: None)
        assert commands.cmd_weekly(_args(journal="2025-099")) == 2
        with session_scope() as session:
            run = session.execute(select(PipelineRun)).scalar_one()
        assert run.status == "blocked"
        assert run.blocked_reason.startswith("journal_retrieval_failed")

    def test_a_blocked_run_is_retried_by_the_next_attempt(self, weekly_env, monkeypatch):
        """Only a processed journal is skipped: a failed attempt must not block the retry."""
        monkeypatch.setattr(commands, "send_failure_alert", lambda *a, **k: None)
        real_fetch = FixtureJournalSource.fetch

        def failing_fetch(self, ref):  # type: ignore[no-untyped-def]
            from src.errors import JournalRetrievalError

            raise JournalRetrievalError("not published yet")

        monkeypatch.setattr(FixtureJournalSource, "fetch", failing_fetch)
        assert commands.cmd_weekly(_args()) == 2
        monkeypatch.setattr(FixtureJournalSource, "fetch", real_fetch)
        assert commands.cmd_weekly(_args()) == 0
        assert _run_count() == 2

    def test_search_calls_are_persisted_with_the_run(self, weekly_env):
        commands.cmd_weekly(_args())
        with session_scope() as session:
            run = session.execute(select(PipelineRun)).scalar_one()
        assert run.counts["search_calls"] == weekly_env.provider.calls > 0

    def test_backfill_honours_force(self, weekly_env, monkeypatch):
        monkeypatch.setattr(
            commands, "previous_journal_dates", lambda weeks: [datetime(2025, 12, 12).date()]
        )
        assert commands.cmd_backfill(_args(command="backfill", weeks=1)) == 0
        assert commands.cmd_backfill(_args(command="backfill", weeks=1)) == 0
        assert weekly_env.fetches == ["2025-050"]
        assert commands.cmd_backfill(_args(command="backfill", weeks=1, force=True)) == 0
        assert weekly_env.fetches == ["2025-050", "2025-050"]

    def test_force_is_a_documented_flag(self):
        from src.pipeline import build_parser

        assert build_parser().parse_args(["weekly", "--force"]).force is True
        assert build_parser().parse_args(["weekly"]).force is False
        assert build_parser().parse_args(["backfill", "--force"]).force is True


# ---------------------------------------------------------------------------
# search cost guard
# ---------------------------------------------------------------------------


def _pipeline(settings: Settings, tmp_path: Path, provider) -> Pipeline:  # type: ignore[no-untyped-def]
    return Pipeline(
        settings=settings,
        source=FixtureJournalSource(settings),
        registry=FixtureCompanyRegistry(),
        classifier=ProductClassifier(settings, llm_provider=None),
        web=WebEnricher(provider=provider, settings=settings),
        output_dir=tmp_path / "runs",
    )


class TestSearchBudget:
    def test_a_record_is_searched_completely_or_not_at_all(self):
        budget = SearchBudget(3)
        assert budget.reserve(2)
        budget.count(2)
        assert not budget.reserve(2)  # one call left, the record needs two
        assert budget.exhausted
        assert not budget.reserve(1)  # once refused, always refused
        assert budget.records_skipped == 2
        assert budget.calls == 2

    def test_unlimited_budget_still_counts(self):
        budget = SearchBudget(None)
        for _ in range(500):
            assert budget.reserve(2)
            budget.count(2)
        assert budget.calls == 1000
        assert budget.warning() is None

    def test_monthly_budget_limits_the_allowance(self):
        guard = SearchGuard(max_calls_per_run=200, monthly_budget=900, window_days=30)
        assert guard.allowance(0) == (200, PER_RUN_CAP)
        assert guard.allowance(800) == (100, MONTHLY_BUDGET)
        assert guard.allowance(2000) == (0, MONTHLY_BUDGET)

    def test_config_defaults(self, settings):
        guard = SearchGuard.load(settings)
        assert (guard.max_calls_per_run, guard.monthly_budget, guard.window_days) == (200, 900, 30)

    def test_environment_overrides_the_per_run_cap(self, settings):
        assert SearchGuard.load(settings.model_copy(update={"search_max_calls_per_run": 7})) == (
            SearchGuard(7, 900, 30)
        )

    def test_a_blank_environment_value_means_unset(self, monkeypatch):
        monkeypatch.setenv("SEARCH_MAX_CALLS_PER_RUN", "")
        assert Settings().search_max_calls_per_run is None  # type: ignore[call-arg]
        monkeypatch.setenv("SEARCH_MAX_CALLS_PER_RUN", "12")
        assert Settings().search_max_calls_per_run == 12  # type: ignore[call-arg]


class TestCostGuardInThePipeline:
    def test_the_cap_is_respected_and_the_run_completes(self, settings, tmp_path):
        unlimited_provider = CountingSearchProvider()
        unlimited = _pipeline(settings, tmp_path, unlimited_provider).run(
            write_outputs=False, search_budget=SearchBudget(None)
        )
        assert unlimited_provider.calls >= 3

        cap = unlimited_provider.calls - 1
        provider = CountingSearchProvider()
        capped = _pipeline(settings, tmp_path, provider).run(
            write_outputs=False, search_budget=SearchBudget(cap)
        )

        assert capped.status == RunStatus.COMPLETED
        assert provider.calls <= cap
        assert provider.calls >= cap - 1  # stopped only when the next record would not fit
        assert capped.counts.search_calls == provider.calls
        assert any("Search budget reached" in w for w in capped.warnings)
        assert capped.counts.enrichment_failures == 0

        # Records enriched before the money ran out score exactly as before;
        # the rest are scored as if no search provider were configured.
        before = {o.dedupe_key: o for o in unlimited.opportunities}
        skipped = 0
        for opp in capped.opportunities:
            if opp.web.attempted:
                assert opp.score.value == before[opp.dedupe_key].score.value
            else:
                skipped += 1
                assert opp.web.error == "search_budget_exhausted"
        assert skipped >= 1

    def test_the_cap_holds_exactly_when_every_record_needs_one_call(self, settings, tmp_path):
        provider = CountingSearchProvider()
        pipeline = _pipeline(settings, tmp_path, provider)
        # Without a company name each record takes exactly one call.
        pipeline.registry = FixtureCompanyRegistry(records=[])
        result = pipeline.run(write_outputs=False, search_budget=SearchBudget(2))
        assert provider.calls == 2 == result.counts.search_calls
        assert result.status == RunStatus.COMPLETED

    def test_an_exhausted_monthly_budget_stops_all_searches(self, settings, tmp_path):
        provider = CountingSearchProvider()
        result = _pipeline(settings, tmp_path, provider).run(
            write_outputs=False, search_budget=SearchBudget(0, limited_by=MONTHLY_BUDGET)
        )
        assert provider.calls == 0
        assert result.status == RunStatus.COMPLETED
        assert any("monthly search budget" in w for w in result.warnings)
        assert all(not o.web.attempted for o in result.opportunities)

    def test_without_a_budget_the_per_run_cap_from_config_applies(self, settings, tmp_path):
        provider = CountingSearchProvider()
        pipeline = _pipeline(
            settings.model_copy(update={"search_max_calls_per_run": 1}), tmp_path, provider
        )
        result = pipeline.run(write_outputs=False)
        assert provider.calls <= 1
        assert result.counts.search_calls == provider.calls

    def test_monthly_usage_comes_from_persisted_runs(self, weekly_env):
        from src.db import init_db

        init_db()
        now = datetime.now(UTC)
        with session_scope() as session:
            session.add(
                PipelineRun(
                    run_id="r_old",
                    started_at=now - timedelta(days=45),
                    counts={"search_calls": 5000},
                )
            )
            session.add(
                PipelineRun(
                    run_id="r_1", started_at=now - timedelta(days=10), counts={"search_calls": 500}
                )
            )
            session.add(
                PipelineRun(
                    run_id="r_2", started_at=now - timedelta(days=2), counts={"search_calls": 398}
                )
            )
            session.add(PipelineRun(run_id="r_3", started_at=now, counts={}))
        with session_scope() as session:
            budget = commands.search_budget_for_run(session, get_settings())
        assert (budget.max_calls, budget.limited_by) == (2, MONTHLY_BUDGET)

        result = commands._run_one(_args(), write_db=True)
        assert result.status == RunStatus.COMPLETED
        assert weekly_env.provider.calls <= 2
        assert any("monthly search budget" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# cache housekeeping
# ---------------------------------------------------------------------------


def _age(path: Path, days: int) -> None:
    then = time.time() - days * 86400
    os.utime(path, (then, then))


class TestCachePrune:
    def test_only_old_journal_downloads_are_removed(self, settings):
        cache = Path(settings.cache_dir)
        journals, opendata = cache / "journals", cache / "opendata"
        journals.mkdir(parents=True)
        opendata.mkdir(parents=True)
        old, new = journals / "old.xml", journals / "new.xml"
        old_zip = opendata / "opendatadomestic.zip"
        ch_cache = cache / "companies_house_api.sqlite"
        for p in (old, new, old_zip, ch_cache):
            p.write_text("x")
        _age(old, 45)
        _age(old_zip, 31)
        _age(ch_cache, 400)

        assert commands.prune_caches(settings) == 2

        assert not old.exists() and not old_zip.exists()
        assert new.exists()
        assert ch_cache.exists()  # not a journal download cache

    def test_prune_never_raises(self, settings, monkeypatch):
        from src.ingest import cache as cache_module

        (Path(settings.cache_dir) / "journals").mkdir(parents=True)

        def boom(self, *a, **k):  # type: ignore[no-untyped-def]
            raise PermissionError("read-only")

        monkeypatch.setattr(cache_module.FileCache, "prune", boom)
        assert commands.prune_caches(settings) == 0

    def test_weekly_prunes_after_success_and_after_failure(self, weekly_env, monkeypatch):
        calls: list[str] = []
        monkeypatch.setattr(commands, "prune_caches", lambda settings: calls.append("pruned"))
        monkeypatch.setattr(commands, "send_failure_alert", lambda *a, **k: None)
        assert commands.cmd_weekly(_args()) == 0
        assert commands.cmd_weekly(_args(journal="2025-099")) == 2

        def explode(args):  # type: ignore[no-untyped-def]
            raise RuntimeError("unexpected")

        monkeypatch.setattr(commands, "_weekly", explode)
        with pytest.raises(RuntimeError):
            commands.cmd_weekly(_args())
        assert calls == ["pruned", "pruned", "pruned"]

    def test_the_rule_is_config_driven(self):
        from src.settings import load_config

        assert load_config("operations.json")["cache_max_age_days"] == 30


# ---------------------------------------------------------------------------
# first trade mark for this applicant
# ---------------------------------------------------------------------------


def _reason_keys(result, brand: str) -> set[str]:  # type: ignore[no-untyped-def]
    opp = next(o for o in result.opportunities if o.brand_name == brand)
    return {r.key for r in opp.score.reasons}


class TestFirstTrademarkSignal:
    def test_a_rerun_keeps_the_first_trademark_signal_and_the_score(self, weekly_env):
        first = commands._run_one(_args(), write_db=True)
        again = commands._run_one(_args(force=True), write_db=True)

        assert "first_trademark_for_applicant" in _reason_keys(first, "BRINEHOUSE")
        assert "first_trademark_for_applicant" in _reason_keys(again, "BRINEHOUSE")
        scores = {o.dedupe_key: o.score.value for o in first.opportunities}
        assert {o.dedupe_key: o.score.value for o in again.opportunities} == scores
        assert first.counts.high == again.counts.high

    def test_a_later_journal_sees_earlier_applicants_as_known(
        self, weekly_env, monkeypatch, tmp_path
    ):
        fixtures = tmp_path / "journals"
        fixtures.mkdir()
        shutil.copy(FIXTURE_JOURNAL, fixtures / "2025-050.xml")
        shutil.copy(FIXTURE_JOURNAL, fixtures / "2025-051.xml")
        monkeypatch.setattr(
            commands,
            "get_source",
            lambda settings=None, name=None: FixtureJournalSource(settings, directory=fixtures),
        )

        earlier = commands._run_one(_args(), journal_number="2025-050", write_db=True)
        later = commands._run_one(_args(), journal_number="2025-051", write_db=True)

        assert "first_trademark_for_applicant" in _reason_keys(earlier, "BRINEHOUSE")
        assert "first_trademark_for_applicant" not in _reason_keys(later, "BRINEHOUSE")

        # ...and processing the earlier week again later does not see the later one.
        again = commands._run_one(_args(), journal_number="2025-050", write_db=True)
        assert "first_trademark_for_applicant" in _reason_keys(again, "BRINEHOUSE")


class TestJournalNumberOrdering:
    """known_applicant_names compares journal numbers as strings in SQL."""

    def test_zero_padded_numbers_sort_chronologically(self, db_session):
        numbers = ["2017-052", "2018-001", "2018-009", "2018-010", "2018-052", "2019-001"]
        for i, number in enumerate(numbers):
            db_session.add(
                TrademarkRecordRow(
                    journal_number=number,
                    dedupe_key=f"k{i}",
                    trademark_number=f"UK{i}",
                    applicant_name=f"Applicant {number}",
                )
            )
        db_session.flush()
        known = known_applicant_names(db_session, before_journal="2018-010")
        assert known == {"applicant 2017-052", "applicant 2018-001", "applicant 2018-009"}

    def test_open_data_journal_numbers_are_zero_padded_and_ordered(self):
        start = datetime(2017, 12, 1).date()
        days = [start + timedelta(days=7 * i) for i in range(70)]
        numbers = [journal_number_for_date(d) for d in days]
        assert all(len(n) == 8 and n[4] == "-" for n in numbers)
        assert numbers == sorted(numbers)

    def test_local_files_are_normalised_to_three_digits(self, tmp_path, settings):
        from src.ingest.local_file import LocalJournalSource

        (tmp_path / "2026-07.xml").write_text("<x/>")
        source = LocalJournalSource(
            settings.model_copy(update={"journal_local_dir": str(tmp_path)})
        )
        assert "2026-007" in source._files()


class TestBrandsFromWeekly:
    def test_weekly_links_every_opportunity_to_a_brand(self, weekly_env):
        from src.db.tables import Brand, Observation, OpportunityRow, ScoreEvent

        assert commands.cmd_weekly(_args()) == 0
        with session_scope() as session:
            opportunities = list(session.execute(select(OpportunityRow)).scalars())
            events = list(session.execute(select(ScoreEvent)).scalars())
            brand_count = session.execute(select(func.count()).select_from(Brand)).scalar_one()
            observed = session.execute(select(func.count()).select_from(Observation)).scalar_one()
        assert opportunities and all(o.brand_id for o in opportunities)
        assert events and all(e.brand_id for e in events)
        assert brand_count > 0 and observed > 0
