"""End-to-end pipeline behaviour, deduplication and fail-closed safeguards."""

from __future__ import annotations

import pytest

from src.errors import JournalRetrievalError, VolumeAnomalyError
from src.ingest.base import JournalSource
from src.models import JournalArtifact, JournalRef, RunStatus, ScoreBand
from src.pipeline_core import Pipeline


class TestHappyPath:
    def test_run_completes_and_produces_opportunities(self, pipeline):
        result = pipeline.run()
        assert result.status == RunStatus.COMPLETED
        assert result.counts.raw_records == 8
        assert result.deliverable

    def test_funnel_counts_are_internally_consistent(self, pipeline):
        result = pipeline.run()
        counts = result.counts
        assert counts.packaged_food_candidates <= counts.food_class_candidates
        assert counts.emerging_candidates <= counts.packaged_food_candidates
        assert counts.high + counts.medium + counts.suppressed == counts.scored

    def test_major_brand_filing_never_reaches_a_customer(self, pipeline):
        result = pipeline.run()
        brands = {o.applicant_name for o in result.deliverable}
        assert not any("Nestle" in (b or "") for b in brands)

    def test_restaurant_filing_is_rejected_with_a_specific_reason(self, pipeline):
        result = pipeline.run()
        reasons = {r.reason for r in result.rejected}
        assert "service_only_hospitality" in reasons

    def test_raw_agricultural_filing_is_rejected(self, pipeline):
        result = pipeline.run()
        assert "raw_agricultural_only" in {r.reason for r in result.rejected}

    def test_outputs_are_written(self, pipeline):
        from pathlib import Path

        result = pipeline.run()
        assert Path(result.csv_path).exists()
        assert Path(result.email_html_path).exists()
        assert Path(result.qa_report_path).exists()

    def test_every_opportunity_carries_a_source_url(self, pipeline):
        for opp in pipeline.run().deliverable:
            assert opp.source_url

    def test_buying_intent_is_populated(self, pipeline):
        for opp in pipeline.run().deliverable:
            assert set(opp.buying_intent.as_dict().values()) <= {"HIGH", "MEDIUM", "LOW", "NONE"}


class TestIdempotency:
    def test_two_runs_produce_the_same_opportunities(self, pipeline):
        first = {o.dedupe_key for o in pipeline.run().deliverable}
        second = {o.dedupe_key for o in pipeline.run().deliverable}
        assert first == second

    def test_duplicate_records_are_dropped(
        self, settings, tmp_path, company_registry, web_enricher
    ):
        from src.classify.pipeline import ProductClassifier
        from src.ingest.fixture import FixtureJournalSource
        from src.parse.journal_xml import parse_journal_xml
        from tests.conftest import FIXTURE_JOURNAL

        records = list(parse_journal_xml(FIXTURE_JOURNAL, "2025-050"))

        class DuplicatingSource(FixtureJournalSource):
            pass

        pipeline = Pipeline(
            settings=settings,
            source=DuplicatingSource(settings),
            registry=company_registry,
            classifier=ProductClassifier(settings, llm_provider=None),
            web=web_enricher,
            output_dir=tmp_path / "runs",
        )
        result = pipeline.run(write_outputs=False)
        # Feeding the same records twice must not double the opportunities.
        pipeline._process(result, records + records, set())
        assert result.counts.duplicates_dropped > 0

    def test_dedupe_key_is_stable(self):
        from tests.conftest import make_record

        assert make_record().dedupe_key == make_record().dedupe_key

    def test_dedupe_key_changes_with_the_applicant(self):
        from tests.conftest import make_record

        assert make_record().dedupe_key != make_record(applicant_name="Other Ltd").dedupe_key


class FailingSource(JournalSource):
    name = "failing"
    parser = "journal_xml"

    def latest_ref(self) -> JournalRef:
        return self.ref_for()

    def ref_for(self, journal_number=None, publication_date=None) -> JournalRef:  # type: ignore[no-untyped-def]
        from datetime import date

        return JournalRef(
            journal_number="2025-050", publication_date=date(2025, 12, 12), source_name="failing"
        )

    def available_refs(self, limit: int = 12) -> list[JournalRef]:
        return [self.ref_for()]

    def fetch(self, ref: JournalRef) -> JournalArtifact:
        raise JournalRetrievalError("ipo.gov.uk returned 403")


class TruncatedSource(FailingSource):
    name = "truncated"

    def fetch(self, ref: JournalRef) -> JournalArtifact:
        from tests.conftest import FIXTURE_JOURNAL

        return JournalArtifact(ref=ref, local_path=str(FIXTURE_JOURNAL))


class TestFailClosed:
    def test_retrieval_failure_blocks_the_run(
        self, settings, tmp_path, company_registry, web_enricher
    ):
        from src.classify.pipeline import ProductClassifier

        pipeline = Pipeline(
            settings=settings,
            source=FailingSource(settings),
            registry=company_registry,
            classifier=ProductClassifier(settings, llm_provider=None),
            web=web_enricher,
            output_dir=tmp_path / "runs",
        )
        result = pipeline.run()
        assert result.status == RunStatus.BLOCKED
        assert "journal_retrieval_failed" in (result.blocked_reason or "")
        assert not result.deliverable

    def test_volume_anomaly_blocks_the_run(
        self, settings, tmp_path, company_registry, web_enricher
    ):
        from src.classify.pipeline import ProductClassifier

        pipeline = Pipeline(
            settings=settings,
            source=TruncatedSource(settings),  # source name has no fixture allowance
            registry=company_registry,
            classifier=ProductClassifier(settings, llm_provider=None),
            web=web_enricher,
            output_dir=tmp_path / "runs",
        )
        result = pipeline.run()
        assert result.status == RunStatus.BLOCKED
        assert "volume_anomaly" in (result.blocked_reason or "")

    def test_volume_guard_raises_on_a_short_journal(self, pipeline):
        from src.models import PipelineResult

        result = PipelineResult(run_id="t", journal=pipeline.source.latest_ref())
        result.counts.raw_records = 2
        result.journal = result.journal.model_copy(update={"source_name": "ukipo_journal_xml"})
        with pytest.raises(VolumeAnomalyError):
            pipeline._check_volume(result, history=None)

    def test_sharp_volume_change_warns_but_does_not_block(self, pipeline):
        from src.models import PipelineResult

        result = PipelineResult(run_id="t", journal=pipeline.source.latest_ref())
        result.counts.raw_records = 8
        pipeline._check_volume(result, history=[{"raw_records": 8000}])
        assert result.warnings


class TestPostDatedCompanyMatches:
    def test_a_company_incorporated_long_after_filing_is_discarded(self, pipeline):
        from datetime import date

        from tests.conftest import make_company, make_record

        record = make_record(filing_date=date(2018, 1, 1))
        match = make_company(incorporation_date=date(2024, 6, 1))
        cleaned = pipeline._reject_post_dated_match(record, match, max_after_months=6)
        assert cleaned.matched is False
        assert cleaned.company_number is None
        assert cleaned.incorporation_date is None
        assert any("incorporated" in e for e in cleaned.match_evidence)

    def test_incorporation_shortly_after_filing_is_kept(self, pipeline):
        from datetime import date

        from tests.conftest import make_company, make_record

        record = make_record(filing_date=date(2025, 1, 1))
        match = make_company(incorporation_date=date(2025, 3, 1))
        cleaned = pipeline._reject_post_dated_match(record, match, max_after_months=6)
        assert cleaned.matched is True


class TestSuppression:
    def test_low_scoring_records_are_marked_suppressed(self, pipeline):
        result = pipeline.run()
        for opp in result.opportunities:
            if opp.score.band == ScoreBand.SUPPRESS:
                assert opp.suppressed is True
                assert opp.suppression_reason

    def test_suppressed_records_are_excluded_from_delivery(self, pipeline):
        result = pipeline.run()
        assert all(not o.suppressed for o in result.deliverable)
