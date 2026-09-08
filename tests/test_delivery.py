"""CSV export, email rendering, review mode and delivery idempotency."""

from __future__ import annotations

import csv
from datetime import UTC

import pytest

from src.db.tables import Customer, CustomerPreference, PipelineRun
from src.deliver.csv_export import CSV_COLUMNS, write_opportunities_csv
from src.deliver.email_render import (
    build_subject,
    render_alert_email,
    render_sample_email,
    render_weekly_email,
    render_welcome_email,
)
from src.deliver.qa_report import build_qa_report, format_qa_report
from src.deliver.resend_client import EmailSender
from src.delivery_service import deliver_weekly
from src.models import RunStatus


@pytest.fixture
def result(pipeline):  # type: ignore[no-untyped-def]
    return pipeline.run()


class TestCsv:
    def test_columns_match_the_published_schema(self, result, tmp_path):
        path = write_opportunities_csv(result.deliverable, tmp_path / "out.csv")
        with path.open(encoding="utf-8-sig") as fh:
            assert next(csv.reader(fh)) == CSV_COLUMNS

    def test_one_row_per_opportunity(self, result, tmp_path):
        path = write_opportunities_csv(result.deliverable, tmp_path / "out.csv")
        with path.open(encoding="utf-8-sig") as fh:
            assert len(list(csv.DictReader(fh))) == len(result.deliverable)

    def test_rows_carry_commercially_useful_values(self, result, tmp_path):
        path = write_opportunities_csv(result.deliverable, tmp_path / "out.csv")
        with path.open(encoding="utf-8-sig") as fh:
            row = next(csv.DictReader(fh))
        assert row["brand_name"]
        assert row["trademark_number"].startswith("UK")
        assert row["launchtrace_score"].isdigit()
        assert row["score_band"] in {"HIGH", "MEDIUM"}
        assert row["score_reasons"]
        assert row["packaging_relevance"] in {"HIGH", "MEDIUM", "LOW", "NONE"}

    def test_no_internal_debug_fields_leak_into_the_customer_file(self, result, tmp_path):
        path = write_opportunities_csv(result.deliverable, tmp_path / "out.csv")
        with path.open(encoding="utf-8-sig") as fh:
            header = next(csv.reader(fh))
        for internal in (
            "match_method",
            "match_confidence",
            "rejection_reasons",
            "dedupe_key",
            "raw",
        ):
            assert internal not in header

    def test_stages_are_human_readable(self, result, tmp_path):
        path = write_opportunities_csv(result.deliverable, tmp_path / "out.csv")
        with path.open(encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                assert "_" not in row["launch_stage"]

    def test_empty_export_still_writes_a_header(self, tmp_path):
        path = write_opportunities_csv([], tmp_path / "empty.csv")
        with path.open(encoding="utf-8-sig") as fh:
            assert next(csv.reader(fh)) == CSV_COLUMNS


class TestEmailRendering:
    def test_subject_follows_the_agreed_format(self):
        assert build_subject(18) == "18 emerging UK food brands detected this week"
        assert build_subject(1) == "1 emerging UK food brand detected this week"
        assert build_subject(0) == "0 emerging UK food brands detected this week"

    def test_weekly_email_contains_the_headline_facts(self, result, settings):
        rendered = render_weekly_email(result, settings=settings)
        assert str(len(result.deliverable)) in rendered.html
        assert result.journal.journal_number in rendered.html
        for opp in sorted(result.deliverable, key=lambda o: o.score.value, reverse=True)[:5]:
            assert opp.brand_name in rendered.html

    def test_weekly_email_carries_source_attribution(self, result, settings):
        rendered = render_weekly_email(result, settings=settings)
        assert "Intellectual Property Office" in rendered.html
        assert "Companies House" in rendered.html
        assert "Open Government Licence" in rendered.html

    def test_weekly_email_does_not_claim_purchasing_intent(self, result, settings):
        rendered = render_weekly_email(result, settings=settings)
        lowered = rendered.html.lower()
        assert "not a prediction that a company" in lowered
        assert "needs packaging now" not in lowered

    def test_weekly_email_offers_a_way_out(self, result, settings):
        rendered = render_weekly_email(result, settings=settings)
        assert "Manage or cancel" in rendered.html

    def test_plain_text_alternative_is_produced(self, result, settings):
        rendered = render_weekly_email(result, settings=settings)
        assert rendered.text
        assert "<" not in rendered.text.split("Source:")[0].replace("<br>", "")

    def test_empty_week_renders_without_pretending(self, result, settings):
        empty = result.model_copy(deep=True)
        empty.opportunities = []
        rendered = render_weekly_email(empty, settings=settings)
        assert "No brands cleared" in rendered.html
        assert "Nothing has been removed from" in rendered.html

    def test_welcome_email_states_the_price(self, settings):
        rendered = render_welcome_email("PackCo Ltd", "Sam", settings=settings)
        assert "79.00" in rendered.html
        assert "PackCo Ltd" in rendered.html

    def test_alert_email_states_nothing_was_sent(self):
        rendered = render_alert_email("Journal missing", "run_1", "2025-050", "blocked", "detail")
        assert "No customer email was sent" in rendered.html

    def test_sample_email_links_to_checkout(self, settings):
        rendered = render_sample_email(
            "PackCo Ltd", "Sam", 12, "Flexible packaging", settings=settings
        )
        assert "billing/checkout" in rendered.html


class TestQaReport:
    def test_report_records_the_whole_funnel(self, result):
        report = build_qa_report(result)
        for key in (
            "raw_records",
            "food_class_candidates",
            "packaged_food_candidates",
            "company_matched",
            "emerging_candidates",
            "high",
            "medium",
            "suppressed",
        ):
            assert key in report["funnel"]

    def test_report_lists_major_brand_detections(self, result):
        report = build_qa_report(result)
        assert any("Nestle" in d for d in report["major_brand_detections"])

    def test_report_flags_review_mode(self, result):
        report = build_qa_report(result, send_mode="review")
        assert report["requires_approval"] is True

    def test_report_formats_for_a_terminal(self, result):
        text = format_qa_report(build_qa_report(result))
        assert "Funnel" in text
        assert "Top rejection reasons" in text

    def test_low_volume_is_flagged(self, result):
        shrunk = result.model_copy(deep=True)
        shrunk.counts.raw_records = 1
        shrunk.journal = shrunk.journal.model_copy(update={"source_name": "ukipo_journal_xml"})
        report = build_qa_report(shrunk)
        assert any("unusually LOW" in c for c in report["data_quality_concerns"])


class TestReviewMode:
    def _customer(self, session):  # type: ignore[no-untyped-def]
        customer = Customer(company="PackCo Ltd", subscription_status="active")
        session.add(customer)
        session.flush()
        session.add(
            CustomerPreference(customer_id=customer.id, recipient_email="sales@packco.test")
        )
        session.flush()
        return customer

    def test_review_mode_blocks_delivery(self, db_session, result, settings, tmp_path):
        self._customer(db_session)
        summary = deliver_weekly(
            db_session, result, settings=settings, sender=EmailSender(settings, tmp_path / "outbox")
        )
        assert summary.sent == 0
        assert "not been approved" in (summary.blocked_reason or "")

    def test_approval_unblocks_delivery(self, db_session, result, settings, tmp_path):
        from datetime import datetime

        self._customer(db_session)
        db_session.add(
            PipelineRun(
                run_id=result.run_id,
                journal_number=result.journal.journal_number,
                status="completed",
                approved_at=datetime.now(UTC),
            )
        )
        db_session.flush()
        summary = deliver_weekly(
            db_session, result, settings=settings, sender=EmailSender(settings, tmp_path / "outbox")
        )
        assert summary.rendered_not_sent == 1

    def test_force_overrides_review_mode(self, db_session, result, settings, tmp_path):
        self._customer(db_session)
        summary = deliver_weekly(
            db_session,
            result,
            settings=settings,
            force=True,
            sender=EmailSender(settings, tmp_path / "outbox"),
        )
        assert summary.rendered_not_sent == 1

    def test_blocked_run_is_never_delivered_even_when_forced(
        self, db_session, result, settings, tmp_path
    ):
        self._customer(db_session)
        blocked = result.model_copy(deep=True)
        blocked.status = RunStatus.BLOCKED
        blocked.blocked_reason = "journal_retrieval_failed"
        summary = deliver_weekly(
            db_session,
            blocked,
            settings=settings,
            force=True,
            sender=EmailSender(settings, tmp_path / "outbox"),
        )
        assert summary.attempted == 0
        assert "blocked" in (summary.blocked_reason or "").lower()

    def test_delivery_is_idempotent(self, db_session, result, settings, tmp_path):
        self._customer(db_session)
        outbox = tmp_path / "outbox"
        first = deliver_weekly(
            db_session, result, settings=settings, force=True, sender=EmailSender(settings, outbox)
        )
        second = deliver_weekly(
            db_session, result, settings=settings, force=True, sender=EmailSender(settings, outbox)
        )
        assert first.rendered_not_sent == 1
        assert second.skipped_already_sent == 1
        assert second.rendered_not_sent == 0


class TestFileModeSending:
    def test_without_a_key_email_is_written_not_sent(self, settings, tmp_path):
        sender = EmailSender(settings, tmp_path / "outbox")
        rendered = render_alert_email("t", "run", "2025-050", "blocked", "d")
        outcome = sender.send(["ops@launchtrace.test"], rendered)
        assert outcome.status == "rendered_not_sent"
        assert (tmp_path / "outbox").exists()
        assert outcome.ok

    def test_no_recipients_is_a_failure_not_a_silent_success(self, settings, tmp_path):
        sender = EmailSender(settings, tmp_path / "outbox")
        rendered = render_alert_email("t", "run", "2025-050", "blocked", "d")
        assert sender.send([], rendered).status == "failed"
