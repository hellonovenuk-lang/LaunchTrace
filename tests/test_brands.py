"""Brands across weeks: dedupe identity, append-only observations, stage changes."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import func, select

from src.brands import (
    applicant_key_hash,
    brand_key_for,
    journal_sort_key,
    latest_observations,
    observations_as_of,
    record_observation,
    record_stage_change,
    signal_registry,
    sync_brands,
    upsert_brand_from_opportunity,
)
from src.db.repository import save_opportunities
from src.db.tables import Brand, Observation, OpportunityRow, ScoreEvent, StageChange
from src.models import (
    ApplicantType,
    CompanyMatch,
    JournalRef,
    LaunchStage,
    Opportunity,
    PipelineResult,
    RetailPresence,
    Score,
    ScoreBand,
    WebEnrichment,
)
from src.settings import load_config
from tests.conftest import make_company


def opp(**overrides) -> Opportunity:  # type: ignore[no-untyped-def]
    base = {
        "dedupe_key": "k-crumbledge",
        "trademark_number": "UK00003900001",
        "brand_name": "CRUMBLEDGE",
        "journal_number": "2025-050",
        "filing_date": date(2025, 9, 15),
        "publication_date": date(2025, 12, 12),
        "applicant_name": "Crumbledge Foods Ltd",
        "applicant_type": ApplicantType.CORPORATE,
        "nice_classes": [30],
        "product_category": "cereal_bars",
        "company": make_company(),
        "score": Score(value=78, band=ScoreBand.MEDIUM),
        "launch_stage": LaunchStage.PRE_LAUNCH,
    }
    base.update(overrides)
    return Opportunity(**base)  # type: ignore[arg-type]


def unmatched(**overrides) -> Opportunity:  # type: ignore[no-untyped-def]
    return opp(company=CompanyMatch(matched=False), **overrides)


def result(*opps: Opportunity, run_id: str = "run_1", journal: str = "2025-050") -> PipelineResult:
    from src.ingest.discovery import date_for_journal_number

    return PipelineResult(
        run_id=run_id,
        journal=JournalRef(
            journal_number=journal,
            publication_date=date_for_journal_number(journal),
            source_name="fixture_journal_xml",
        ),
        opportunities=list(opps),
    )


def brands(session) -> list[Brand]:  # type: ignore[no-untyped-def]
    return list(session.execute(select(Brand).order_by(Brand.id)).scalars())


class TestIdentity:
    def test_a_confident_company_match_keys_by_company_number(self):
        assert brand_key_for(opp()) == "ch:14000001"

    def test_an_unmatched_applicant_keys_by_mark_and_applicant(self):
        key = brand_key_for(unmatched())
        assert key.startswith("tm:") and len(key) == 3 + 24
        # Normalisation: case, punctuation and legal suffix do not split a brand.
        assert (
            brand_key_for(
                unmatched(brand_name="Crumbledge!", applicant_name="CRUMBLEDGE FOODS LIMITED")
            )
            == key
        )
        assert brand_key_for(unmatched(brand_name="OTHER")) != key

    def test_an_unconfident_match_is_not_used(self):
        weak = make_company(matched=False)
        assert brand_key_for(opp(company=weak)).startswith("tm:")

    def test_one_company_across_journals_is_one_brand(self, db_session):
        a = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        b = upsert_brand_from_opportunity(
            db_session,
            opp(
                journal_number="2025-051",
                brand_name="CRUMBLEDGE GRANOLA",
                trademark_number="UK00003900099",
                dedupe_key="k-2",
            ),
            "run_2",
        )
        assert a.id == b.id
        assert len(brands(db_session)) == 1
        assert b.brand_uid.startswith("b_") and len(b.brand_uid) == 18

    def test_a_tm_brand_that_gains_a_company_number_keeps_its_identity(self, db_session):
        first = upsert_brand_from_opportunity(db_session, unmatched(), "run_1")
        brand_id, uid, key = first.id, first.brand_uid, first.brand_key
        assert key.startswith("tm:") and first.company_number is None

        upgraded = upsert_brand_from_opportunity(db_session, opp(journal_number="2025-051"), "r2")
        assert (upgraded.id, upgraded.brand_uid, upgraded.brand_key) == (brand_id, uid, key)
        assert upgraded.company_number == "14000001"

        # A different mark from the same company now finds it by company number.
        other = upsert_brand_from_opportunity(
            db_session,
            opp(journal_number="2025-052", brand_name="OTHER MARK", dedupe_key="k-3"),
            "r3",
        )
        assert other.id == brand_id
        assert len(brands(db_session)) == 1

    def test_the_uid_is_deterministic(self, db_session):
        brand = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        import hashlib

        assert brand.brand_uid == "b_" + hashlib.sha256(b"ch:14000001").hexdigest()[:16]


class TestFirstSeen:
    def test_first_seen_is_kept_on_a_rerun(self, db_session):
        first = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        seen_at = first.first_seen_at
        again = upsert_brand_from_opportunity(db_session, opp(), "run_2")
        assert again.first_seen_journal == "2025-050"
        assert again.first_seen_at == seen_at
        assert again.last_seen_journal == "2025-050"

    def test_a_later_journal_moves_last_seen_not_first_seen(self, db_session):
        upsert_brand_from_opportunity(db_session, opp(), "run_1")
        later = upsert_brand_from_opportunity(
            db_session,
            opp(journal_number="2026-001", score=Score(value=90, band=ScoreBand.HIGH)),
            "run_2",
        )
        assert later.first_seen_journal == "2025-050"
        assert later.last_seen_journal == "2026-001"
        assert (later.current_score, later.current_band) == (90, "HIGH")

    def test_a_backfilled_earlier_journal_moves_first_seen_earlier(self, db_session):
        upsert_brand_from_opportunity(
            db_session, opp(journal_number="2025-051", filing_date=date(2025, 10, 1)), "run_1"
        )
        backfilled = upsert_brand_from_opportunity(
            db_session,
            opp(
                journal_number="2025-049",
                filing_date=date(2025, 9, 1),
                score=Score(value=10, band=ScoreBand.SUPPRESS),
            ),
            "run_2",
        )
        assert backfilled.first_seen_journal == "2025-049"
        assert backfilled.first_filing_date == date(2025, 9, 1)
        # ...but the current state still reflects the newest journal.
        assert backfilled.last_seen_journal == "2025-051"
        assert backfilled.current_score == 78

    def test_journal_ordering_is_chronological(self):
        assert journal_sort_key("2025-052") < journal_sort_key("2026-001")
        assert journal_sort_key("2026-9") < journal_sort_key("2026-010")


class TestStageChanges:
    def test_a_new_stage_in_a_later_week_is_recorded(self, db_session):
        upsert_brand_from_opportunity(db_session, opp(), "run_1")
        brand = upsert_brand_from_opportunity(
            db_session,
            opp(journal_number="2025-051", launch_stage=LaunchStage.EARLY_LAUNCH),
            "run_2",
        )
        changes = list(db_session.execute(select(StageChange)).scalars())
        assert [(c.from_stage, c.to_stage, c.run_id) for c in changes] == [
            ("pre_launch", "early_launch", "run_2")
        ]
        assert changes[0].brand_id == brand.id
        assert changes[0].evidence["journal_number"] == "2025-051"
        assert brand.current_stage == "early_launch"

    def test_two_marks_in_one_week_are_not_a_transition(self, db_session):
        upsert_brand_from_opportunity(db_session, opp(), "run_1")
        upsert_brand_from_opportunity(
            db_session,
            opp(dedupe_key="k-2", brand_name="B", launch_stage=LaunchStage.SCALING),
            "run_1",
        )
        assert db_session.execute(select(func.count()).select_from(StageChange)).scalar_one() == 0

    def test_record_stage_change_directly(self, db_session):
        brand = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        row = record_stage_change(db_session, brand.id, "pre_launch", "launched", {"why": "x"})
        assert row.id and row.evidence == {"why": "x"} and row.run_id is None


class TestObservations:
    def _observations(self, session, signal: str) -> list[Observation]:  # type: ignore[no-untyped-def]
        return list(
            session.execute(
                select(Observation).where(Observation.signal == signal).order_by(Observation.id)
            ).scalars()
        )

    def test_sync_records_facts_with_dates_and_pit_flags(self, db_session):
        web = WebEnrichment(
            attempted=True,
            provider="fixture",
            website="https://crumbledge.test",
            retail_presence=RetailPresence.DIRECT_ONLY,
            enriched_at=datetime.now(UTC),
        )
        sync_brands(db_session, result(opp(web=web)))

        filing = self._observations(db_session, "filing_date")[0]
        assert (filing.source, filing.source_date, filing.point_in_time_safe) == (
            "trademark",
            date(2025, 9, 15),
            True,
        )
        assert filing.value == {"trademark_number": "UK00003900001", "filing_date": "2025-09-15"}
        assert filing.run_id == "run_1" and filing.journal_number == "2025-050"

        published = self._observations(db_session, "publication_date")[0]
        assert published.source_date == date(2025, 12, 12) and published.point_in_time_safe

        incorporated = self._observations(db_session, "incorporation_date")[0]
        assert incorporated.source == "companies_house"
        assert (incorporated.source_date, incorporated.point_in_time_safe) == (
            date(2025, 3, 1),
            True,
        )

        for signal in ("company_status", "sic_codes", "accounts_category", "company_match"):
            row = self._observations(db_session, signal)[0]
            assert row.point_in_time_safe is False and row.source_date is None, signal

        website = self._observations(db_session, "website")[0]
        assert website.source == "web_search" and website.value == "https://crumbledge.test"
        assert website.point_in_time_safe is False

        score = self._observations(db_session, "launchtrace_score")[0]
        assert score.source == "score" and score.value["score"] == 78
        assert score.point_in_time_safe is False

    def test_no_web_observations_without_a_search(self, db_session):
        sync_brands(db_session, result(opp()))
        assert self._observations(db_session, "website") == []

    def test_a_rerun_appends_rather_than_updates(self, db_session):
        sync_brands(db_session, result(opp()))
        first = db_session.execute(select(func.count()).select_from(Observation)).scalar_one()
        sync_brands(db_session, result(opp(), run_id="run_2"))
        assert db_session.execute(select(func.count()).select_from(Observation)).scalar_one() == (
            2 * first
        )
        runs = {o.run_id for o in self._observations(db_session, "filing_date")}
        assert runs == {"run_1", "run_2"}

    def test_company_facts_are_not_repeated_per_mark_within_a_run(self, db_session):
        two_marks = result(
            opp(),
            opp(dedupe_key="k-2", trademark_number="UK2", brand_name="CRUMBLEDGE OATS"),
        )
        assert sync_brands(db_session, two_marks) == 1
        assert len(self._observations(db_session, "incorporation_date")) == 1
        assert len(self._observations(db_session, "filing_date")) == 2  # one per mark

    def test_unregistered_signals_are_refused(self, db_session):
        brand = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        with pytest.raises(ValueError, match="not registered"):
            record_observation(db_session, brand.id, "web_search", "made_up_signal", 1)

    def test_pit_default_comes_from_the_registry(self, db_session):
        brand = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        safe = record_observation(
            db_session, brand.id, "trademark", "filing_date", {}, source_date=date(2025, 1, 1)
        )
        unsafe = record_observation(
            db_session,
            brand.id,
            "companies_house",
            "sic_codes",
            ["10720"],
            source_date=date.today(),
        )
        undated = record_observation(db_session, brand.id, "trademark", "filing_date", {})
        assert safe.point_in_time_safe is True
        assert unsafe.point_in_time_safe is False
        assert undated.point_in_time_safe is False  # cannot be placed in time

    def test_latest_and_as_of_queries(self, db_session):
        brand = upsert_brand_from_opportunity(db_session, opp(), "run_1")
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        record_observation(db_session, brand.id, "web_search", "website", None, observed_at=t0)
        record_observation(
            db_session,
            brand.id,
            "web_search",
            "website",
            "https://x.test",
            observed_at=t0 + timedelta(days=30),
        )
        record_observation(
            db_session,
            brand.id,
            "trademark",
            "filing_date",
            {"filing_date": "2025-09-15"},
            observed_at=t0,
            source_date=date(2025, 9, 15),
        )

        latest = latest_observations(db_session, brand.id)
        assert latest["website"].value == "https://x.test"
        earlier = latest_observations(db_session, brand.id, as_of=t0 + timedelta(days=1))
        assert earlier["website"].value is None

        pit = observations_as_of(db_session, brand.id, date(2025, 12, 31))
        assert [o.signal for o in pit] == ["filing_date"]
        assert observations_as_of(db_session, brand.id, date(2025, 9, 1)) == []
        everything = observations_as_of(
            db_session, brand.id, date(2026, 1, 15), pit_safe_only=False
        )
        assert sorted(o.signal for o in everything) == ["filing_date", "website"]

    def test_there_is_no_update_api(self):
        import src.brands as brands_module

        assert not [n for n in dir(brands_module) if n.startswith(("update_obs", "delete_obs"))]


class TestPrivacy:
    def test_an_individual_applicants_name_is_never_stored_on_a_brand(self, db_session):
        name = "Jane Marie Doe"
        person = unmatched(
            applicant_name=name,
            applicant_type=ApplicantType.NATURAL_PERSON,
            brand_name="JANES JAMS",
        )
        sync_brands(db_session, result(person))
        rows = brands(db_session)
        assert len(rows) == 1
        row = rows[0]
        for column in Brand.__table__.columns:
            value = str(getattr(row, column.name) or "").lower()
            assert "jane marie doe" not in value, column.name
            assert "doe" not in value.split(), column.name
        assert row.applicant_key_hash == applicant_key_hash(name)
        assert len(row.applicant_key_hash) == 64
        assert row.applicant_type == "natural_person"


class TestSyncFromAPipelineRun:
    def test_opportunities_and_score_events_are_linked(self, db_session, pipeline):
        run = pipeline.run(write_outputs=False)
        save_opportunities(db_session, run)
        touched = sync_brands(db_session, run)

        assert touched == len(brands(db_session)) > 0
        rows = list(db_session.execute(select(OpportunityRow)).scalars())
        assert rows and all(r.brand_id is not None for r in rows)
        events = list(
            db_session.execute(select(ScoreEvent).where(ScoreEvent.run_id == run.run_id)).scalars()
        )
        assert events and all(e.brand_id is not None for e in events)
        by_key = {r.dedupe_key: r.brand_id for r in rows}
        assert all(e.brand_id == by_key[e.dedupe_key] for e in events)

    def test_suppressed_opportunities_get_brands_too(self, db_session):
        suppressed = unmatched(
            dedupe_key="k-s",
            brand_name="LOWSCORE",
            score=Score(value=12, band=ScoreBand.SUPPRESS),
            suppressed=True,
            suppression_reason="score_below_band",
        )
        run = result(opp(), suppressed)
        save_opportunities(db_session, run)
        assert sync_brands(db_session, run) == 2
        assert {b.brand_key for b in brands(db_session)} == {
            brand_key_for(o) for o in run.opportunities
        }
        row = db_session.execute(
            select(OpportunityRow).where(OpportunityRow.dedupe_key == "k-s")
        ).scalar_one()
        assert row.brand_id is not None


class TestSignalsRegistry:
    def test_groups_by_source(self):
        cfg = load_config("signals.json")
        for group in ("trademark", "companies_house", "web_search", "score", "domain", "rescan"):
            assert isinstance(cfg[group], dict), group
        assert "_comment" in cfg["domain"] and "_comment" in cfg["rescan"]

    def test_every_signal_is_fully_described_and_unique(self):
        cfg = load_config("signals.json")
        seen: set[str] = set()
        for group, signals in cfg.items():
            if group.startswith(("_", "$")) or not isinstance(signals, dict):
                continue
            for name, spec in signals.items():
                if name.startswith("_"):
                    continue
                assert name not in seen, f"{name} registered twice"
                seen.add(name)
                assert set(spec) >= {"source", "point_in_time_safe", "description"}, name
                assert isinstance(spec["point_in_time_safe"], bool), name
        assert seen == set(signal_registry())

    def test_only_dated_historical_facts_are_pit_safe(self):
        safe = {n for n, s in signal_registry().items() if s["point_in_time_safe"]}
        assert safe == {"filing_date", "publication_date", "nice_classes", "incorporation_date"}
