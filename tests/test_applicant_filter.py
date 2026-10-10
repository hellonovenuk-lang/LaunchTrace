"""Data minimisation at ingestion: only limited companies and LLPs are stored.

Network-free: the journal is the fixture XML with some applicants rewritten,
Companies House and web search are the fixture providers.
"""

from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

import pytest
from sqlalchemy import func, select

from src.classify.applicant_filter import ApplicantFilter
from src.classify.pipeline import ProductClassifier
from src.db.repository import ingestion_drop_stats, record_ingestion_drops
from src.db.tables import IngestionDropCount, OpportunityRow, TrademarkRecordRow
from src.ingest.fixture import FixtureJournalSource
from src.models import ApplicantLegalForm
from src.pipeline_core import Pipeline
from src.settings import load_config
from tests.conftest import FIXTURE_JOURNAL, make_record

L = ApplicantLegalForm


def filter_with(**overrides) -> ApplicantFilter:  # type: ignore[no-untyped-def]
    # load_config is cached: copy, never mutate the shared dict.
    config = {**load_config("ingestion_filter.json"), **overrides}
    return ApplicantFilter(config=config)


@pytest.fixture
def applicant_filter() -> ApplicantFilter:
    return ApplicantFilter()


class TestClassifier:
    @pytest.mark.parametrize(
        "name",
        [
            "Crumbledge Foods Ltd",
            "Crumbledge Foods Limited",
            "Acme PLC",
            "Acme Public Limited Company",
            "Acme Bakes Cyfyngedig",
            "Acme Community Interest Company",
            "Smith & Co Ltd",
            "Crumbs Ltd t/a Crumbs",
            "Oatly AB",
            "Acme GmbH",
        ],
    )
    def test_limited_companies(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.LIMITED_COMPANY

    @pytest.mark.parametrize(
        "name",
        [
            "crumbledge foods ltd",
            "CRUMBLEDGE FOODS LTD",
            "Crumbledge Foods Ltd.",
            "Crumbledge Foods L.T.D.",
            "Crumbledge Foods  Ltd ",
            "Crumbledge Foods,Ltd",
            "Acme p.l.c.",
            "Acme P.L.C",
            "Acme plc.",
        ],
    )
    def test_case_spacing_and_punctuation_variants(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.LIMITED_COMPANY

    @pytest.mark.parametrize(
        "name",
        [
            "Smith & Jones LLP",
            "Smith & Jones llp",
            "Smith & Jones L.L.P.",
            "Smith Jones Limited Liability Partnership",
        ],
    )
    def test_llps(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.LLP

    @pytest.mark.parametrize(
        "name",
        [
            "John Smith t/a Crumbs",
            "John Smith T/A Crumbs",
            "John Smith T / A Crumbs",
            "Jane Doe trading as Jam Jar",
            "Jane Doe Trading-As Jam Jar",
            "Jane Doe t.a. Jam Jar",
        ],
    )
    def test_trading_as_is_a_sole_trader(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.SOLE_TRADER

    @pytest.mark.parametrize(
        "name",
        [
            "Smith & Co",
            "Smith and Co.",
            "Smith & Sons",
            "Smith Bros",
            "Smith Partners",
            "Green Fields LP",
            "Green Fields Limited Partnership",
            "Jane Doe and John Doe t/a J&J Preserves",
        ],
    )
    def test_partnerships(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.PARTNERSHIP

    @pytest.mark.parametrize(
        "name",
        [
            "Jane Smith",
            "JANE SMITH",
            "Mr Kwan Hun Au",
            "Jumana Kapadia",
            "Smith, John",
            "Dr A Patel",
        ],
    )
    def test_bare_personal_names_are_individuals(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.INDIVIDUAL

    @pytest.mark.parametrize(
        "name",
        [
            None,
            "",
            "   ",
            "Crumbledge",
            "Maria's Kitchen",
            "Hive Foods",
            "123 Ventures",
            "Sa Foods",
        ],
    )
    def test_ambiguous_names_are_unknown(self, applicant_filter, name):
        assert applicant_filter.classify(name) == L.UNKNOWN


class TestKeepDecision:
    def test_defaults_keep_only_companies_and_llps(self, applicant_filter):
        assert {f for f in L if applicant_filter.keeps(f)} == {L.LIMITED_COMPANY, L.LLP}

    def test_unknown_can_be_kept_by_config(self):
        assert filter_with(drop_unknown=False).keeps(L.UNKNOWN)
        assert not filter_with(drop_unknown=False).keeps(L.INDIVIDUAL)

    def test_apply_counts_drops_by_reason(self, applicant_filter):
        records = [
            make_record(trademark_number="1", applicant_name="Crumbledge Foods Ltd"),
            make_record(trademark_number="2", applicant_name="Smith & Jones LLP"),
            make_record(trademark_number="3", applicant_name="Jane Smith"),
            make_record(trademark_number="4", applicant_name="Jane Smith"),
            make_record(trademark_number="5", applicant_name="John Smith t/a Crumbs"),
            make_record(trademark_number="6", applicant_name="Smith & Sons"),
            make_record(trademark_number="7", applicant_name="Maria's Kitchen"),
        ]
        kept, dropped = applicant_filter.apply(records)
        assert [r.trademark_number for r in kept] == ["1", "2"]
        assert dropped == {"individual": 2, "sole_trader": 1, "partnership": 1, "unknown": 1}

    def test_disabled_filter_keeps_everything(self):
        records = [make_record(applicant_name="Jane Smith"), make_record(applicant_name=None)]
        kept, dropped = filter_with(enabled=False).apply(records)
        assert kept == records
        assert not dropped


# -- ingestion -----------------------------------------------------------------

# Applicants in the fixture journal, rewritten to cover every legal form. The
# trade mark numbers and goods are unchanged, so each record still reaches the
# product filter exactly as before.
REWRITES = {
    "Crumbledge Foods Ltd": "Crumbledge Foods Ltd",  # limited company, kept
    "Moorfoot Provisions Limited": "Moorfoot Provisions LLP",  # llp, kept
    "Brinehouse Preserves Ltd": "Jane Brine t/a Brinehouse Preserves",  # sole trader
    "Veldt Roast Coffee Ltd": "Veldt & Sons",  # partnership
    "Northgate Table Ltd": "Jumana Kapadia",  # individual
    "Terra Pasture Farms Ltd": "Terra Pasture Farms",  # unknown
}


@pytest.fixture
def rewritten_journal(tmp_path: Path) -> Path:
    directory = tmp_path / "journals"
    directory.mkdir()
    xml = FIXTURE_JOURNAL.read_text(encoding="utf-8")
    for old, new in REWRITES.items():
        assert old in xml
        xml = xml.replace(f">{old}<", f">{escape(new)}<")
    (directory / FIXTURE_JOURNAL.name).write_text(xml, encoding="utf-8")
    return directory


def build_pipeline(  # type: ignore[no-untyped-def]
    settings, tmp_path, company_registry, web_enricher, directory, applicant_filter=None
) -> Pipeline:
    return Pipeline(
        settings=settings,
        source=FixtureJournalSource(settings, directory=directory),
        registry=company_registry,
        classifier=ProductClassifier(settings, llm_provider=None),
        web=web_enricher,
        output_dir=tmp_path / "runs",
        applicant_filter=applicant_filter,
    )


DROPPED_NAMES = {
    "Jane Brine t/a Brinehouse Preserves",
    "Veldt & Sons",
    "Jumana Kapadia",
    "Terra Pasture Farms",
}


class TestIngestion:
    def test_only_companies_and_llps_survive_ingestion(
        self, settings, tmp_path, company_registry, web_enricher, rewritten_journal
    ):
        pipeline = build_pipeline(
            settings, tmp_path, company_registry, web_enricher, rewritten_journal
        )
        result = pipeline.run(write_outputs=False)

        kept = {r.applicant_name for r in pipeline.last_records}
        assert "Crumbledge Foods Ltd" in kept
        assert "Moorfoot Provisions LLP" in kept
        assert not kept & DROPPED_NAMES
        # The dropped applicants never reach enrichment or scoring either.
        assert not {o.applicant_name for o in result.opportunities} & DROPPED_NAMES
        assert not {r.applicant_name for r in result.rejected} & DROPPED_NAMES
        # The volume guard still sees the whole journal.
        assert result.counts.raw_records == len(pipeline.last_records) + 4
        assert pipeline.last_applicant_drops == {
            "sole_trader": 1,
            "partnership": 1,
            "individual": 1,
            "unknown": 1,
        }

    def test_dropped_applicants_are_not_persisted(
        self, settings, tmp_path, company_registry, web_enricher, rewritten_journal, monkeypatch
    ):
        result = self._run_through_cli_persistence(
            settings, tmp_path, company_registry, web_enricher, rewritten_journal, monkeypatch
        )
        from src.db import session_scope

        with session_scope() as session:
            stored = {n for (n,) in session.execute(select(TrademarkRecordRow.applicant_name)) if n}
            opportunity_names = {
                n for (n,) in session.execute(select(OpportunityRow.applicant_name)) if n
            }
            drops = {
                r.reason: r.count for r in session.execute(select(IngestionDropCount)).scalars()
            }
        assert {"Crumbledge Foods Ltd", "Moorfoot Provisions LLP"} <= stored
        assert not stored & DROPPED_NAMES
        assert not opportunity_names & DROPPED_NAMES
        assert drops == {"sole_trader": 1, "partnership": 1, "individual": 1, "unknown": 1}
        assert result.journal.journal_number == "2025-050"

    def test_disabled_filter_restores_the_old_behaviour(
        self, settings, tmp_path, company_registry, web_enricher, rewritten_journal, monkeypatch
    ):
        self._run_through_cli_persistence(
            settings,
            tmp_path,
            company_registry,
            web_enricher,
            rewritten_journal,
            monkeypatch,
            applicant_filter=filter_with(enabled=False),
        )
        from src.db import session_scope

        with session_scope() as session:
            stored = {n for (n,) in session.execute(select(TrademarkRecordRow.applicant_name)) if n}
            total = session.execute(
                select(func.count()).select_from(TrademarkRecordRow)
            ).scalar_one()
            drop_rows = session.execute(
                select(func.count()).select_from(IngestionDropCount)
            ).scalar_one()
        assert stored >= DROPPED_NAMES
        assert total == 8  # every record in the fixture journal
        assert drop_rows == 0

    def test_unchanged_fixture_journal_loses_nothing(self, pipeline):
        # The shipped fixture journal is all limited companies, so the smoke test
        # and every existing expectation built on it are unaffected.
        result = pipeline.run(write_outputs=False)
        assert pipeline.last_applicant_drops == {}
        assert len(pipeline.last_records) == result.counts.raw_records

    @staticmethod
    def _run_through_cli_persistence(  # type: ignore[no-untyped-def]
        settings,
        tmp_path,
        company_registry,
        web_enricher,
        directory,
        monkeypatch,
        applicant_filter=None,
    ):
        """Drive the real persistence path in ``commands._run_one``."""
        import argparse

        from src import commands
        from src.settings import get_settings

        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        get_settings.cache_clear()
        pipeline = build_pipeline(
            settings, tmp_path, company_registry, web_enricher, directory, applicant_filter
        )
        monkeypatch.setattr(commands, "Pipeline", lambda **_: pipeline)
        monkeypatch.setattr(commands, "get_source", lambda **_: pipeline.source)
        try:
            return commands._run_one(argparse.Namespace(command="weekly"))
        finally:
            get_settings.cache_clear()


# -- dropped-count log ---------------------------------------------------------


class TestDroppedCountLog:
    def _result(self, journal: str):  # type: ignore[no-untyped-def]
        from datetime import date

        from src.models import JournalRef, PipelineResult

        return PipelineResult(
            run_id=f"run_{journal}",
            journal=JournalRef(
                journal_number=journal,
                publication_date=date(2025, 1, 1),
                source_name="fixture_journal_xml",
            ),
        )

    def test_counts_increment_by_reason_and_accumulate_across_journals(self, db_session):
        record_ingestion_drops(
            db_session, self._result("2025-001"), {"individual": 3, "unknown": 1}
        )
        record_ingestion_drops(
            db_session, self._result("2025-002"), {"individual": 2, "sole_trader": 4}
        )
        totals: dict[str, int] = {}
        for row in ingestion_drop_stats(db_session):
            totals[row.reason] = totals.get(row.reason, 0) + row.count
        assert totals == {"individual": 5, "unknown": 1, "sole_trader": 4}

    def test_reprocessing_a_journal_replaces_rather_than_doubles(self, db_session):
        result = self._result("2025-001")
        record_ingestion_drops(db_session, result, {"individual": 3, "partnership": 1})
        record_ingestion_drops(db_session, result, {"individual": 3})
        rows = ingestion_drop_stats(db_session)
        assert [(r.reason, r.count) for r in rows] == [("individual", 3)]

    def test_range_filters(self, db_session):
        for journal in ("2025-001", "2025-002", "2025-003"):
            record_ingestion_drops(db_session, self._result(journal), {"individual": 1})
        rows = ingestion_drop_stats(db_session, journal_from="2025-002", journal_to="2025-002")
        assert [r.journal_number for r in rows] == ["2025-002"]

    def test_the_log_holds_no_names(self):
        columns = set(IngestionDropCount.__table__.columns.keys())
        assert not {c for c in columns if "name" in c and c != "source_name"}

    def test_cli_prints_counts_by_reason(self, capsys, monkeypatch, tmp_path):
        from src.db import session_scope
        from src.pipeline import main
        from src.settings import get_settings

        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'drops.sqlite'}")
        get_settings.cache_clear()
        try:
            assert main(["dropped-stats"]) == 0
            assert "No applicants have been excluded" in capsys.readouterr().out

            from src.db import init_db

            init_db()
            with session_scope() as session:
                record_ingestion_drops(
                    session, self._result("2025-001"), {"individual": 3, "unknown": 2}
                )
                record_ingestion_drops(session, self._result("2025-002"), {"sole_trader": 1})
            assert main(["dropped-stats", "--from-journal", "2025-001"]) == 0
            out = capsys.readouterr().out
            assert "individual" in out and "sole_trader" in out and "unknown" in out
            total_line = next(line for line in out.splitlines() if line.startswith("TOTAL"))
            assert total_line.split()[-1] == "6"
            assert main(["dropped-stats", "--to-journal", "2025-001"]) == 0
            assert "2025-002" not in capsys.readouterr().out
        finally:
            get_settings.cache_clear()
