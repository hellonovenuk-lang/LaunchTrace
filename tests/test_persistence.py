"""Database persistence: idempotent journal processing and stored evidence."""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select

from src.db.repository import (
    active_suppressions,
    add_suppression,
    journal_already_processed,
    known_applicant_names,
    record_delivery,
    save_opportunities,
    save_run,
    save_trademark_records,
    upsert_journal,
)
from src.db.tables import (
    CompanyMatchRow,
    Delivery,
    Journal,
    OpportunityRow,
    PipelineRun,
    ScoreEvent,
    TrademarkRecordRow,
    WebEnrichmentRow,
)
from src.models import JournalArtifact, JournalRef
from src.parse.journal_xml import parse_journal_xml
from tests.conftest import FIXTURE_JOURNAL


def artifact() -> JournalArtifact:
    return JournalArtifact(
        ref=JournalRef(
            journal_number="2025-050",
            publication_date=date(2025, 12, 12),
            source_name="fixture_journal_xml",
            source_url="fixture://2025-050",
        ),
        local_path=str(FIXTURE_JOURNAL),
        sha256="a" * 64,
        byte_size=1234,
    )


class TestJournals:
    def test_upsert_records_integrity_metadata(self, db_session):
        row = upsert_journal(db_session, artifact(), record_count=8)
        assert row.sha256 == "a" * 64
        assert row.record_count == 8
        assert row.processing_status == "processed"

    def test_upsert_is_idempotent(self, db_session):
        upsert_journal(db_session, artifact(), 8)
        upsert_journal(db_session, artifact(), 8)
        assert db_session.execute(select(func.count()).select_from(Journal)).scalar_one() == 1

    def test_processed_journals_are_detectable(self, db_session):
        assert journal_already_processed(db_session, "fixture_journal_xml", "2025-050") is False
        upsert_journal(db_session, artifact(), 8)
        assert journal_already_processed(db_session, "fixture_journal_xml", "2025-050") is True

    def test_the_same_number_from_a_different_source_is_a_different_journal(self, db_session):
        upsert_journal(db_session, artifact(), 8)
        other = artifact()
        other.ref = other.ref.model_copy(update={"source_name": "ipo_open_data"})
        upsert_journal(db_session, other, 8)
        assert db_session.execute(select(func.count()).select_from(Journal)).scalar_one() == 2


class TestSourceRecords:
    def test_records_are_stored(self, db_session):
        records = list(parse_journal_xml(FIXTURE_JOURNAL, "2025-050"))
        assert save_trademark_records(db_session, records) == 8

    def test_reprocessing_does_not_duplicate(self, db_session):
        records = list(parse_journal_xml(FIXTURE_JOURNAL, "2025-050"))
        save_trademark_records(db_session, records)
        assert save_trademark_records(db_session, records) == 0
        total = db_session.execute(
            select(func.count()).select_from(TrademarkRecordRow)
        ).scalar_one()
        assert total == 8

    def test_known_applicants_are_reported(self, db_session):
        save_trademark_records(db_session, list(parse_journal_xml(FIXTURE_JOURNAL, "2025-050")))
        names = known_applicant_names(db_session)
        assert "crumbledge foods ltd" in names


class TestOpportunities:
    def test_saving_writes_derived_tables(self, db_session, pipeline):
        result = pipeline.run(write_outputs=False)
        saved = save_opportunities(db_session, result)
        assert saved == len(result.opportunities)
        for table in (OpportunityRow, CompanyMatchRow, WebEnrichmentRow, ScoreEvent):
            assert db_session.execute(select(func.count()).select_from(table)).scalar_one() > 0

    def test_rerunning_a_journal_updates_rather_than_duplicates(self, db_session, pipeline):
        result = pipeline.run(write_outputs=False)
        save_opportunities(db_session, result)
        first = db_session.execute(select(func.count()).select_from(OpportunityRow)).scalar_one()
        save_opportunities(db_session, pipeline.run(write_outputs=False))
        assert (
            db_session.execute(select(func.count()).select_from(OpportunityRow)).scalar_one()
            == first
        )

    def test_score_history_accumulates_across_runs(self, db_session, pipeline):
        save_opportunities(db_session, pipeline.run(write_outputs=False))
        first = db_session.execute(select(func.count()).select_from(ScoreEvent)).scalar_one()
        save_opportunities(db_session, pipeline.run(write_outputs=False))
        assert db_session.execute(select(func.count()).select_from(ScoreEvent)).scalar_one() > first

    def test_match_evidence_is_retained(self, db_session, pipeline):
        save_opportunities(db_session, pipeline.run(write_outputs=False))
        rows = list(db_session.execute(select(CompanyMatchRow)).scalars())
        assert any(r.match_evidence for r in rows)
        assert any(r.match_confidence > 0 for r in rows)


class TestSourceReuse:
    """The journal must be parsed once per run, not once per consumer."""

    def test_run_retains_what_it_parsed(self, pipeline):
        result = pipeline.run(write_outputs=False)
        assert result.status.value == "completed"
        assert pipeline.last_artifact is not None
        assert len(pipeline.last_records) == result.counts.raw_records

    def test_retained_records_can_be_persisted_without_reparsing(self, db_session, pipeline):
        pipeline.run(write_outputs=False)
        row = upsert_journal(db_session, pipeline.last_artifact, len(pipeline.last_records))
        assert save_trademark_records(db_session, pipeline.last_records, row.id) == 8

    def test_a_blocked_run_retains_nothing(
        self, settings, tmp_path, company_registry, web_enricher
    ):
        from src.classify.pipeline import ProductClassifier
        from src.ingest.fixture import FixtureJournalSource
        from src.pipeline_core import Pipeline

        empty = tmp_path / "empty-fixtures"
        empty.mkdir()
        pipeline = Pipeline(
            settings=settings,
            source=FixtureJournalSource(settings, directory=empty),
            registry=company_registry,
            classifier=ProductClassifier(settings, llm_provider=None),
            web=web_enricher,
            output_dir=tmp_path / "runs",
        )
        pipeline.run(write_outputs=False)
        assert pipeline.last_artifact is None
        assert pipeline.last_records == []


class TestRuns:
    def test_run_is_recorded_with_its_funnel(self, db_session, pipeline):
        result = pipeline.run(write_outputs=False)
        row = save_run(db_session, result)
        assert row.status == "completed"
        assert row.counts["raw_records"] == 8

    def test_blocked_run_records_the_reason_and_the_error(self, db_session, pipeline):
        result = pipeline.run(write_outputs=False)
        result.status = result.status.__class__("blocked")
        result.blocked_reason = "journal_retrieval_failed: 403"
        result.errors.append("403 from ipo.gov.uk")
        row = save_run(db_session, result)
        assert row.blocked_reason.startswith("journal_retrieval_failed")
        from src.db.tables import ErrorLog

        assert db_session.execute(select(func.count()).select_from(ErrorLog)).scalar_one() == 1

    def test_saving_the_same_run_twice_updates_it(self, db_session, pipeline):
        result = pipeline.run(write_outputs=False)
        save_run(db_session, result)
        save_run(db_session, result)
        assert db_session.execute(select(func.count()).select_from(PipelineRun)).scalar_one() == 1


class TestDeliveries:
    def test_first_delivery_is_created(self, db_session):
        _, created = record_delivery(db_session, "run_1", "2025-050", "a@b.test", "run_1:a@b.test")
        assert created is True

    def test_second_delivery_with_the_same_key_is_not(self, db_session):
        record_delivery(db_session, "run_1", "2025-050", "a@b.test", "run_1:a@b.test")
        _, created = record_delivery(db_session, "run_1", "2025-050", "a@b.test", "run_1:a@b.test")
        assert created is False
        assert db_session.execute(select(func.count()).select_from(Delivery)).scalar_one() == 1


class TestSuppression:
    def test_rule_is_stored_and_listed(self, db_session):
        add_suppression(db_session, "company", "Bad Company Ltd", "customer complained", "cli")
        assert "bad company ltd" in active_suppressions(db_session)["company"]

    def test_adding_the_same_rule_twice_updates_it(self, db_session):
        add_suppression(db_session, "email", "a@b.test", "first", "cli")
        add_suppression(db_session, "email", "a@b.test", "second", "cli")
        from src.db.tables import SuppressionRule

        rows = list(db_session.execute(select(SuppressionRule)).scalars())
        assert len(rows) == 1
        assert rows[0].reason == "second"
