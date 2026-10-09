"""Data retention: old personal data goes, new data and suppression lists stay."""

from __future__ import annotations

import copy
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from src.db.engine import get_engine
from src.db.tables import (
    Base,
    Brand,
    CompanyMatchRow,
    Customer,
    CustomerPreference,
    Delivery,
    ErrorLog,
    Journal,
    LeadFeedback,
    OpportunityRow,
    PipelineRun,
    ProspectStateRow,
    ProspectSuppression,
    SampleRequest,
    SuppressionRule,
    TrademarkRecordRow,
    WebEnrichmentRow,
    WebhookEvent,
)
from src.retention import (
    HANDLERS,
    PROTECTED_TABLES,
    cutoff_for,
    format_report,
    run_retention,
)
from src.settings import load_config

NOW = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=365 * 3)  # past every default period
NEW = NOW - timedelta(days=30)  # inside every default period
OLD_DATE = OLD.date()
NEW_DATE = NEW.date()


@pytest.fixture
def factory(settings):  # type: ignore[no-untyped-def]
    engine = get_engine(settings.database_url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def _tm(n: int, name: str, published: date) -> TrademarkRecordRow:
    return TrademarkRecordRow(
        journal_number="2023-001",
        dedupe_key=f"tm{n}",
        trademark_number=f"UK{n:08d}",
        applicant_name=name,
        publication_date=published,
    )


def _opp(n: int, name: str, kind: str, published: date) -> OpportunityRow:
    return OpportunityRow(
        dedupe_key=f"o{n}",
        run_id="r",
        journal_number=f"2023-{n:03d}",
        trademark_number=f"UK{n:08d}",
        applicant_name=name,
        applicant_type=kind,
        publication_date=published,
        launchtrace_score=70,
        score_band="MEDIUM",
    )


@pytest.fixture
def populated(factory):  # type: ignore[no-untyped-def]
    with factory() as s:
        s.add_all(
            [
                _tm(1, "Zebedee Quillfeather", OLD_DATE),
                _tm(2, "Old Foods Ltd", OLD_DATE),
                _tm(3, "Recent Person", NEW_DATE),
                _opp(1, "Zebedee Quillfeather", "natural_person", OLD_DATE),
                _opp(2, "Old Foods Ltd", "corporate", OLD_DATE),
                _opp(3, "Recent Person", "natural_person", NEW_DATE),
                _opp(4, "Untyped Person", "unknown", OLD_DATE),
                CompanyMatchRow(
                    dedupe_key="c1",
                    applicant_name="Zebedee Quillfeather",
                    created_at=OLD,
                    match_evidence=["Shared distinctive words: quillfeather"],
                    error="429 for url ...?q=Zebedee+Quillfeather",
                ),
                CompanyMatchRow(dedupe_key="c2", applicant_name="Old Foods Ltd", created_at=OLD),
                CompanyMatchRow(dedupe_key="c3", applicant_name="Recent Person", created_at=NEW),
                WebEnrichmentRow(dedupe_key="w1", enriched_at=OLD),
                WebEnrichmentRow(dedupe_key="w2", enriched_at=NEW),
                WebEnrichmentRow(dedupe_key="w3", enriched_at=None),
                PipelineRun(run_id="old-run", started_at=OLD),
                PipelineRun(run_id="new-run", started_at=NEW),
                ErrorLog(stage="x", message="old", created_at=OLD),
                ErrorLog(stage="x", message="new", created_at=NEW),
                SampleRequest(work_email="old@a.test", company="A", created_at=OLD),
                SampleRequest(
                    work_email="sent-recently@b.test", company="B", created_at=OLD, sent_at=NEW
                ),
                SampleRequest(work_email="new@c.test", company="C", created_at=NEW),
                LeadFeedback(state="USEFUL", note="old note", created_at=OLD),
                LeadFeedback(state="USEFUL", note="new note", created_at=NEW),
                WebhookEvent(event_id="e1", event_type="t", processed_at=OLD),
                WebhookEvent(event_id="e2", event_type="t", processed_at=NEW),
                ProspectStateRow(
                    prospect_id="P001",
                    generic_contact_email="sales@old.test",
                    named_contact="Jane Old",
                    decision_maker_role="Buyer",
                    notes="spoke in 2023",
                    date_added=OLD_DATE,
                    email_1_sent_date=OLD_DATE,
                    opted_out=True,
                ),
                ProspectStateRow(
                    prospect_id="P002",
                    named_contact="Jim Recent",
                    date_added=OLD_DATE,
                    email_1_sent_date=NEW_DATE,
                ),
                ProspectStateRow(
                    prospect_id="P003",
                    named_contact="Customer Contact",
                    date_added=OLD_DATE,
                ),
                ProspectSuppression(kind="email", value="sales@old.test", created_at=OLD),
                SuppressionRule(rule_type="company", value="Zebedee Quillfeather", created_at=OLD),
                Journal(
                    journal_number="2023-001",
                    publication_date=OLD_DATE,
                    retrieved_at=OLD,
                ),
            ]
        )
        live = Customer(
            company="Live Ltd", subscription_status="active", prospect_id="P003", created_at=OLD
        )
        gone = Customer(company="Gone Ltd", subscription_status="cancelled", created_at=OLD)
        s.add_all([live, gone])
        s.flush()
        s.add(CustomerPreference(customer_id=live.id, recipient_email="buyer@live.test"))
        s.add_all(
            [
                Delivery(
                    run_id="r",
                    journal_number="j",
                    customer_id=live.id,
                    recipient_email="buyer@live.test",
                    idempotency_key="d-live",
                    created_at=OLD,
                ),
                Delivery(
                    run_id="r",
                    journal_number="j",
                    customer_id=gone.id,
                    recipient_email="x@gone.test",
                    idempotency_key="d-gone",
                    created_at=OLD,
                ),
            ]
        )
        s.commit()
    return factory


def _snapshot(factory) -> dict[str, list]:  # type: ignore[no-untyped-def]
    with factory() as s:
        out = {}
        for model in Base.__subclasses__():
            rows = s.execute(select(model)).scalars().all()
            out[model.__tablename__] = sorted(
                repr(sorted((c.name, getattr(r, c.key, None)) for c in model.__table__.columns))
                for r in rows
            )
        return out


def _counts(reports) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {r.key: r.affected for r in reports}


EXPECTED = {
    "trademark_records_individual_names": 1,
    "opportunities_individual_names": 2,
    "company_matches_individual_names": 1,
    "web_enrichment": 1,
    "pipeline_runs": 1,
    "errors": 1,
    "deliveries": 1,
    "sample_requests": 1,
    "prospect_contacts": 1,
    "lead_feedback_notes": 1,
    "webhook_events": 1,
}


class TestRetention:
    def test_dry_run_counts_and_changes_nothing(self, populated):
        before = _snapshot(populated)
        reports = run_retention(populated, apply=False, now=NOW)
        assert _counts(reports) == EXPECTED
        assert not any(r.applied for r in reports)
        assert _snapshot(populated) == before

    def test_apply_changes_exactly_what_the_dry_run_reported(self, populated):
        reports = run_retention(populated, apply=True, now=NOW)
        assert _counts(reports) == EXPECTED
        with populated() as s:
            names = dict(
                s.execute(select(TrademarkRecordRow.dedupe_key, TrademarkRecordRow.applicant_name))
                .tuples()
                .all()
            )
            assert names == {"tm1": None, "tm2": "Old Foods Ltd", "tm3": "Recent Person"}
            names = dict(
                s.execute(select(OpportunityRow.dedupe_key, OpportunityRow.applicant_name))
                .tuples()
                .all()
            )
            assert names == {
                "o1": None,
                "o2": "Old Foods Ltd",
                "o3": "Recent Person",
                "o4": None,
            }
            # scores untouched
            assert set(s.execute(select(OpportunityRow.launchtrace_score)).scalars()) == {70}
            names = dict(
                s.execute(select(CompanyMatchRow.dedupe_key, CompanyMatchRow.applicant_name))
                .tuples()
                .all()
            )
            assert names == {"c1": None, "c2": "Old Foods Ltd", "c3": "Recent Person"}
            c1 = s.execute(
                select(CompanyMatchRow).where(CompanyMatchRow.dedupe_key == "c1")
            ).scalar_one()
            assert c1.match_evidence == [] and c1.error is None
            assert set(s.execute(select(WebEnrichmentRow.dedupe_key)).scalars()) == {"w2", "w3"}
            assert set(s.execute(select(PipelineRun.run_id)).scalars()) == {"new-run"}
            assert set(s.execute(select(ErrorLog.message)).scalars()) == {"new"}
            assert set(s.execute(select(SampleRequest.work_email)).scalars()) == {
                "sent-recently@b.test",
                "new@c.test",
            }
            assert set(s.execute(select(LeadFeedback.note)).scalars()) == {None, "new note"}
            assert s.execute(select(func.count(LeadFeedback.id))).scalar_one() == 2
            assert set(s.execute(select(WebhookEvent.event_id)).scalars()) == {"e2"}
            assert set(s.execute(select(Delivery.idempotency_key)).scalars()) == {"d-live"}

            p1 = s.execute(
                select(ProspectStateRow).where(ProspectStateRow.prospect_id == "P001")
            ).scalar_one()
            assert (p1.generic_contact_email, p1.named_contact, p1.decision_maker_role) == (
                "",
                "",
                "",
            )
            assert p1.notes == ""
            assert p1.opted_out is True, "the row and its opt-out flag stay"
            others = dict(
                s.execute(select(ProspectStateRow.prospect_id, ProspectStateRow.named_contact))
                .tuples()
                .all()
            )
            assert others["P002"] == "Jim Recent", "recent interaction"
            assert others["P003"] == "Customer Contact", "linked to a live customer"

    def test_protected_tables_are_never_touched(self, populated):
        before = _snapshot(populated)
        run_retention(populated, apply=True, now=NOW)
        after = _snapshot(populated)
        for table in PROTECTED_TABLES:
            assert after[table] == before[table], table
        assert after["suppression_rules"] and after["prospect_suppressions"]
        assert after["journals"] and after["customers"]

    def test_second_apply_is_a_no_op(self, populated):
        run_retention(populated, apply=True, now=NOW)
        before = _snapshot(populated)
        reports = run_retention(populated, apply=True, now=NOW)
        assert sum(r.affected for r in reports) == 0
        assert _snapshot(populated) == before

    def test_a_disabled_class_is_skipped(self, populated):
        config = copy.deepcopy(load_config("retention.json"))
        config["classes"]["web_enrichment"]["enabled"] = False
        reports = run_retention(populated, apply=True, now=NOW, config=config)
        skipped = {r.key: r for r in reports}["web_enrichment"]
        assert skipped.skipped_reason and skipped.affected == 0
        with populated() as s:
            assert s.execute(select(func.count(WebEnrichmentRow.id))).scalar_one() == 3

    def test_unknown_class_in_config_is_refused(self, populated):
        with pytest.raises(ValueError):
            run_retention(populated, now=NOW, config={"classes": {"suppression_rules": {}}})

    def test_report_renders(self, populated):
        text = format_report(run_retention(populated, now=NOW), applied=False)
        assert "dry run" in text and "--apply" in text


class TestConfig:
    def test_every_class_has_a_handler_and_a_placeholder_period(self):
        config = load_config("retention.json")
        assert set(config["classes"]) == set(HANDLERS)
        for key, cfg in config["classes"].items():
            assert cfg["status"] == "placeholder pending owner decision", key
            assert ("months" in cfg) != ("days" in cfg), key
            cutoff_for(cfg, NOW)

    def test_no_handler_targets_a_protected_table(self):
        assert not {table for table, _ in HANDLERS.values()} & PROTECTED_TABLES

    def test_cutoff_in_months_and_days(self):
        assert cutoff_for({"months": 1}, datetime(2026, 3, 31, tzinfo=UTC)) == datetime(
            2026, 2, 28, tzinfo=UTC
        )
        assert cutoff_for({"months": 24}, NOW) == datetime(2024, 10, 9, 12, 0, tzinfo=UTC)
        assert cutoff_for({"days": 10}, NOW) == NOW - timedelta(days=10)
        with pytest.raises(ValueError):
            cutoff_for({"months": 1, "days": 1}, NOW)
        with pytest.raises(ValueError):
            cutoff_for({"months": 0}, NOW)


class TestBrandsHoldNoPersonalData:
    def test_brand_table_has_no_name_column_for_the_applicant(self):
        columns = {c.name for c in Brand.__table__.columns}
        assert "applicant_name" not in columns
        assert "applicant_key_hash" in columns


class TestCli:
    def test_cli_defaults_to_dry_run(self, populated, settings, monkeypatch, capsys):
        from src.pipeline import main
        from src.settings import get_settings

        monkeypatch.setenv("DATABASE_URL", settings.database_url)
        get_settings.cache_clear()
        try:
            before = _snapshot(populated)
            assert main(["retention", "--json"]) == 0
            out = capsys.readouterr().out
            report = json.loads(out[out.index("[\n") :])
            assert {r["key"] for r in report} == set(HANDLERS)
            assert not any(r["applied"] for r in report)
            assert _snapshot(populated) == before
            assert main(["retention", "--apply"]) == 0
            assert "applied" in capsys.readouterr().out
        finally:
            get_settings.cache_clear()

    def test_retention_is_not_part_of_a_pipeline_run(self):
        """The stability snapshot runs `_run_one`; retention must never be in it."""
        import inspect

        import src.commands as commands
        import src.pipeline_core as core

        assert "retention" not in inspect.getsource(core)
        assert "run_retention" not in inspect.getsource(commands)
