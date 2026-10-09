"""Point-in-time scoring: only PIT-safe facts inside the cutoff can move a backtest score."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from src.backtest.pit import PitPolicy, PitScorer, applicant_history, pit_facts, record_from_row
from src.score.launchtrace_score import LaunchTraceScorer
from tests.backtest_helpers import (
    make_brand,
    make_source_record,
    matched_company,
    observe,
)

LATER = datetime(2026, 5, 1, tzinfo=UTC)


@pytest.fixture
def scorer(settings) -> PitScorer:  # type: ignore[no-untyped-def,override]
    return PitScorer(settings=settings, policy=PitPolicy.load())


def _score(scorer: PitScorer, session, brand, record):  # type: ignore[no-untyped-def]
    result = scorer.score(session, brand.id, record)
    assert result is not None
    return result


class TestPolicy:
    def test_loads_config(self):
        policy = PitPolicy.load()
        assert policy.window_days == 0
        assert "incorporation_date" in policy.allowed_signals
        assert "company_status" not in policy.allowed_signals
        assert "early_stage_website" in policy.not_evaluable
        assert policy.cutoff(date(2025, 9, 15)) == date(2025, 9, 15)

    def test_window_moves_the_cutoff(self):
        policy = PitPolicy.load({"pit_window_days": 45, "allowed_signals": []})
        assert policy.cutoff(date(2025, 9, 15)) == date(2025, 10, 30)

    def test_every_allowed_signal_is_registered_point_in_time_safe(self):
        from src.brands import signal_registry

        registry = signal_registry()
        for name in PitPolicy.load().allowed_signals:
            assert registry[name]["point_in_time_safe"] is True, name

    def test_not_evaluable_keys_are_real_indicators(self):
        from src.settings import load_config

        scoring = load_config("scoring.json")
        keys = {
            i["key"]
            for g in ("positive_indicators", "negative_indicators", "domain_indicators")
            for i in scoring[g]
        }
        assert set(PitPolicy.load().not_evaluable) <= keys


class TestPitFacts:
    def test_pit_safe_incorporation_inside_cutoff_is_used(self, db_session):
        brand = make_brand(db_session)
        matched_company(db_session, brand, incorporated=date(2025, 3, 1))
        facts = pit_facts(db_session, brand.id, date(2025, 9, 15), PitPolicy.load())
        assert facts.matched
        assert facts.incorporation_date == date(2025, 3, 1)
        assert facts.match_confidence == 98

    def test_incorporation_after_the_cutoff_is_refused(self, db_session):
        brand = make_brand(db_session)
        matched_company(db_session, brand, incorporated=date(2025, 10, 1))
        facts = pit_facts(db_session, brand.id, date(2025, 9, 15), PitPolicy.load())
        assert not facts.matched
        assert any("after cutoff" in r["reason"] for r in facts.refused)

    def test_no_identity_evidence_means_no_match(self, db_session):
        brand = make_brand(db_session)
        observe(
            db_session,
            brand,
            "incorporation_date",
            {"company_number": "1", "incorporation_date": "2025-01-01"},
            source_date=date(2025, 1, 1),
        )
        facts = pit_facts(db_session, brand.id, date(2025, 9, 15), PitPolicy.load())
        assert not facts.matched

    def test_identity_link_can_be_switched_off(self, db_session):
        brand = make_brand(db_session)
        matched_company(db_session, brand)
        policy = PitPolicy.load(
            {"allowed_signals": ["incorporation_date"], "identity_link_signal": None}
        )
        assert not pit_facts(db_session, brand.id, date(2025, 9, 15), policy).matched

    def test_non_pit_observations_are_refused_and_said_so(self, db_session):
        brand = make_brand(db_session)
        observe(db_session, brand, "company_status", "active")
        facts = pit_facts(db_session, brand.id, date(2026, 9, 15), PitPolicy.load())
        assert {"signal": "company_status", "reason": "not point-in-time safe"} in facts.refused


class TestPitScore:
    def test_a_pit_safe_fact_changes_the_score(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        before = _score(scorer, db_session, brand, record)
        assert "no_company_match" in before.fired

        matched_company(db_session, brand, incorporated=date(2025, 3, 1))
        after = _score(scorer, db_session, brand, record)

        assert "company_incorporated_within_12m" in after.fired
        assert "strong_company_match" in after.fired
        assert "no_company_match" not in after.fired
        assert after.value != before.value

    def test_non_pit_observations_cannot_affect_the_score(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        matched_company(db_session, brand)
        baseline = _score(scorer, db_session, brand, record)

        # Everything that describes the present, or is our own derivation.
        observe(db_session, brand, "company_status", "dissolved")
        observe(db_session, brand, "sic_codes", ["56101"])  # a restaurant: service-only
        observe(db_session, brand, "accounts_category", "DORMANT")
        observe(db_session, brand, "website", "https://crumbledge.test")
        observe(db_session, brand, "website_maturity", "established")
        observe(db_session, brand, "retail_presence", "multiple_retail")
        observe(db_session, brand, "marketplace_presence", True)
        observe(db_session, brand, "major_retailer_presence", True)
        observe(db_session, brand, "launch_evidence", ["now available"])
        observe(db_session, brand, "launchtrace_score", {"score": 99, "band": "HIGH"})
        observe(db_session, brand, "launch_stage", "established")
        observe(
            db_session,
            brand,
            "site_platform",
            {"domain": "crumbledge.test", "platform": "shopify", "shop_platform": True},
        )
        observe(db_session, brand, "dns_has_mx", {"domain": "crumbledge.test", "value": True})
        observe(
            db_session, brand, "holding_page", {"domain": "x", "holding": True, "parked": False}
        )

        after = _score(scorer, db_session, brand, record)
        assert after.value == baseline.value
        assert after.fired == baseline.fired
        for key in PitPolicy.load().not_evaluable:
            assert key not in after.fired

    def test_a_pit_safe_signal_after_the_cutoff_cannot_affect_the_score(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        baseline = _score(scorer, db_session, brand, record)

        # Incorporated a month AFTER filing: true later, not knowable at filing.
        matched_company(db_session, brand, incorporated=date(2025, 10, 15))
        observe(
            db_session,
            brand,
            "domain_created",
            {"domain": "crumbledge.test", "created": "2025-11-01"},
            source_date=date(2025, 11, 1),
        )
        after = _score(scorer, db_session, brand, record)
        assert after.value == baseline.value
        assert after.fired == baseline.fired

    def test_a_wider_window_admits_facts_known_by_publication(self, db_session, settings):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        matched_company(db_session, brand, incorporated=date(2025, 10, 15))
        strict = PitScorer(settings=settings, policy=PitPolicy.load())
        wide = PitScorer(
            settings=settings,
            policy=PitPolicy.load(
                {
                    "pit_window_days": 60,
                    "allowed_signals": ["incorporation_date"],
                    "identity_link_signal": "company_match",
                }
            ),
        )
        assert "no_company_match" in _score(strict, db_session, brand, record).fired
        wide_score = _score(wide, db_session, brand, record)
        # Incorporated after filing: treated as brand new, as the weekly run does.
        assert "company_incorporated_within_12m" in wide_score.fired

    def test_a_pit_safe_row_flagged_unsafe_is_ignored(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        observe(
            db_session,
            brand,
            "incorporation_date",
            {"company_number": "14000001", "incorporation_date": "2025-03-01"},
            source_date=date(2025, 3, 1),
            pit=False,
        )
        observe(
            db_session,
            brand,
            "company_match",
            {"company_number": "14000001", "match_confidence": 98},
        )
        assert "no_company_match" in _score(scorer, db_session, brand, record).fired

    def test_domain_created_before_filing_fires_the_pit_domain_indicator(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        observe(
            db_session,
            brand,
            "domain_created",
            {"domain": "crumbledge.test", "created": "2025-06-01"},
            source_date=date(2025, 6, 1),
        )
        result = _score(scorer, db_session, brand, record)
        assert "domain_registered_recently" in result.fired
        # Weight 0: it is reported, but it does not move the score.
        observe(db_session, brand, "dns_has_mx", {"domain": "crumbledge.test", "value": True})
        assert "has_mx_records" not in _score(scorer, db_session, brand, record).fired

    def test_web_inputs_are_neutralised_as_search_not_run(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        matched_company(db_session, brand)
        result = _score(scorer, db_session, brand, record)
        assert result.opportunity.web.attempted is False
        # Capped at the top of MEDIUM, exactly as a live run without search is.
        assert result.value <= 79

    def test_the_real_scorer_is_used(self, db_session, scorer, monkeypatch):
        calls: list[object] = []
        real = LaunchTraceScorer.score

        def spy(self, ctx):  # type: ignore[no-untyped-def]
            calls.append(ctx)
            return real(self, ctx)

        monkeypatch.setattr(LaunchTraceScorer, "score", spy)
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        result = _score(scorer, db_session, brand, record)
        assert len(calls) == 1
        ctx = calls[0]
        assert ctx.web.attempted is False  # type: ignore[attr-defined]
        assert ctx.company.sic_codes == []  # type: ignore[attr-defined]
        assert ctx.company.accounts_category is None  # type: ignore[attr-defined]
        assert result.value == real(LaunchTraceScorer(), ctx).value  # type: ignore[arg-type]

    def test_record_without_filing_date_is_not_scored(self, db_session, scorer):
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand, filing_date=None)
        assert scorer.score(db_session, brand.id, record) is None


class TestApplicantHistory:
    def test_first_mark_and_count_come_from_stored_journals(self, db_session):
        a = make_source_record(db_session, trademark_number="UK1", journal_number="2025-050")
        make_source_record(db_session, trademark_number="UK2", journal_number="2025-050")
        assert applicant_history(db_session, record_from_row(a)) == (True, 2)
        b = make_source_record(db_session, trademark_number="UK3", journal_number="2025-051")
        assert applicant_history(db_session, record_from_row(b)) == (False, 1)

    def test_earlier_means_published_by_the_pit_cutoff(self, db_session):
        # D-708: a journal published after the filing (but before this record's
        # own journal) is the future at the cutoff.
        from datetime import date

        make_source_record(
            db_session,
            trademark_number="UK1",
            journal_number="2025-045",
            publication_date=date(2025, 11, 7),
        )
        filed_before = make_source_record(
            db_session,
            trademark_number="UK2",
            journal_number="2025-050",
            filing_date=date(2025, 9, 15),
        )
        record = record_from_row(filed_before)
        assert applicant_history(db_session, record, date(2025, 9, 15)) == (True, 1)
        assert applicant_history(db_session, record, date(2025, 11, 7)) == (False, 1)
        # without a cutoff: the weekly definition, every earlier journal
        assert applicant_history(db_session, record) == (False, 1)

    def test_a_record_without_a_publication_date_is_not_assumed_early(self, db_session):
        from datetime import date

        make_source_record(
            db_session, trademark_number="UK1", journal_number="2025-045", publication_date=None
        )
        row = make_source_record(db_session, trademark_number="UK2", journal_number="2025-050")
        assert applicant_history(db_session, record_from_row(row), date(2026, 1, 1)) == (True, 1)

    def test_the_pit_scorer_uses_the_cutoff(self, db_session, scorer, monkeypatch):
        import src.backtest.pit as pit

        seen: list[object] = []
        real = pit.applicant_history

        def spy(session, record, cutoff=None):  # type: ignore[no-untyped-def]
            seen.append(cutoff)
            return real(session, record, cutoff)

        monkeypatch.setattr(pit, "applicant_history", spy)
        brand = make_brand(db_session)
        record = make_source_record(db_session, brand)
        _score(scorer, db_session, brand, record)
        assert seen == [scorer.policy.cutoff(record.filing_date)]

    def test_no_applicant_name(self, db_session):
        row = make_source_record(db_session, trademark_number="UK9", applicant_name=None)
        assert applicant_history(db_session, record_from_row(row)) == (True, 1)
