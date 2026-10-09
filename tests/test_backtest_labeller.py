"""Outcome labeller: launched at 3 vs 6 months, unknown when not elapsed or no evidence."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from sqlalchemy import select

from src.backtest.labeller import (
    LAUNCHED,
    NOT_LAUNCHED,
    UNKNOWN,
    add_months,
    format_labels,
    label_brands,
    label_one,
    matches,
)
from src.db.tables import Observation, Outcome
from src.settings import load_config
from tests.backtest_helpers import make_brand, make_source_record, observe

FILED = date(2025, 9, 15)
CFG = load_config("backtest.json")["outcomes"]


def obs(signal: str, value, when: date, oid: int = 1) -> Observation:  # type: ignore[no-untyped-def]
    return Observation(
        id=oid,
        brand_id=1,
        source="test",
        signal=signal,
        value=value,
        observed_at=datetime(when.year, when.month, when.day, 12, tzinfo=UTC),
        point_in_time_safe=False,
    )


def shop(when: date) -> Observation:
    return obs(
        "site_platform", {"domain": "x.test", "platform": "shopify", "shop_platform": True}, when
    )


def no_store(when: date) -> Observation:
    return obs("web_presence_stage", {"domain": "x.test", "stage": "holding_page"}, when)


def test_add_months_clips_to_month_end():
    assert add_months(date(2025, 1, 31), 1) == date(2025, 2, 28)
    assert add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert add_months(date(2025, 11, 15), 3) == date(2026, 2, 15)
    assert add_months(date(2025, 9, 15), 6) == date(2026, 3, 15)


def test_matches_field_equals_and_in():
    row = shop(FILED)
    assert matches({"signal": "site_platform", "field": "shop_platform", "equals": True}, row)
    assert not matches({"signal": "site_platform", "field": "shop_platform", "equals": False}, row)
    assert not matches({"signal": "other", "equals": True}, row)
    scalar = obs("retail_presence", "marketplace", FILED)
    assert matches({"signal": "retail_presence", "in": ["marketplace"]}, scalar)
    assert not matches({"signal": "retail_presence"}, scalar)
    assert not matches(
        {"signal": "marketplace_presence", "equals": None}, obs("marketplace_presence", None, FILED)
    )


class TestLabelOne:
    def test_launched_by_six_months_but_not_three(self):
        rows = [shop(date(2026, 1, 20))]  # ~4 months after filing
        three = label_one(FILED, 3, rows, date(2026, 10, 1), CFG)
        six = label_one(FILED, 6, rows, date(2026, 10, 1), CFG)
        assert three.label == UNKNOWN and three.reason == "no evidence"
        assert six.label == LAUNCHED
        assert six.criteria_met["shop_live"] == "positive"
        assert six.evidence[0]["signal"] == "site_platform"

    def test_launched_at_three_months(self):
        rows = [shop(date(2025, 11, 1))]
        assert label_one(FILED, 3, rows, date(2026, 10, 1), CFG).label == LAUNCHED
        assert label_one(FILED, 6, rows, date(2026, 10, 1), CFG).label == LAUNCHED

    def test_unknown_while_the_horizon_has_not_elapsed(self):
        rows = [shop(date(2025, 10, 1))]
        result = label_one(FILED, 6, rows, date(2026, 1, 1), CFG)
        assert result.label == UNKNOWN
        assert "horizon not elapsed" in result.reason
        assert result.horizon_end == date(2026, 3, 15)

    def test_unknown_with_no_evidence_at_all(self):
        result = label_one(FILED, 3, [], date(2026, 10, 1), CFG)
        assert result.label == UNKNOWN
        assert result.criteria_met["shop_live"] == "no_evidence"
        assert result.criteria_met["ch_accounts_after_filing"] == "unavailable"

    def test_not_launched_needs_a_check_made_at_the_horizon_end(self):
        at_end = [no_store(date(2025, 12, 20))]  # horizon end 2025-12-15, within 30 days
        assert label_one(FILED, 3, at_end, date(2026, 10, 1), CFG).label == NOT_LAUNCHED

        too_early = [no_store(date(2025, 10, 1))]  # checked before the horizon end
        assert label_one(FILED, 3, too_early, date(2026, 10, 1), CFG).label == UNKNOWN

        too_late = [no_store(date(2026, 6, 1))]  # long after: says nothing about the horizon
        assert label_one(FILED, 3, too_late, date(2026, 10, 1), CFG).label == UNKNOWN

    def test_positive_beats_negative(self):
        rows = [shop(date(2025, 11, 1)), no_store(date(2025, 12, 20))]
        assert label_one(FILED, 3, rows, date(2026, 10, 1), CFG).label == LAUNCHED

    def test_positive_observed_after_the_horizon_does_not_count(self):
        rows = [shop(date(2026, 4, 1))]
        assert label_one(FILED, 6, rows, date(2026, 10, 1), CFG).label == UNKNOWN

    def test_observations_after_as_of_are_ignored(self):
        rows = [no_store(date(2025, 12, 20))]
        assert label_one(FILED, 3, rows, date(2025, 12, 16), CFG).label == UNKNOWN

    def test_marketplace_listing_from_stored_search_counts(self):
        rows = [obs("marketplace_presence", True, date(2025, 11, 1))]
        result = label_one(FILED, 3, rows, date(2026, 10, 1), CFG)
        assert result.label == LAUNCHED
        assert result.criteria_met["retail_or_marketplace_listing"] == "positive"

    def test_no_filing_date_is_unknown(self):
        result = label_one(None, 3, [shop(FILED)], date(2026, 10, 1), CFG)
        assert result.label == UNKNOWN and result.reason == "no filing date"


class TestLabelBrands:
    def test_writes_one_row_per_brand_and_horizon_and_replaces_on_rerun(self, db_session):
        brand = make_brand(db_session)
        observe(
            db_session,
            brand,
            "web_presence_stage",
            {"domain": "x.test", "stage": "live_store"},
            observed_at=datetime(2025, 11, 1, tzinfo=UTC),
        )
        summary = label_brands(db_session, as_of=date(2026, 10, 1))
        rows = db_session.execute(select(Outcome).order_by(Outcome.horizon_months)).scalars().all()
        assert [(r.horizon_months, r.label) for r in rows] == [(3, LAUNCHED), (6, LAUNCHED)]
        assert rows[0].labeller_version == "1"
        assert rows[0].as_of_date == date(2026, 10, 1)
        assert rows[0].evidence[0]["signal"] == "web_presence_stage"
        assert summary.counts[3][LAUNCHED] == 1
        assert "launched 1" in format_labels(summary)

        label_brands(db_session, as_of=date(2025, 10, 1))
        rows = db_session.execute(select(Outcome)).scalars().all()
        assert len(rows) == 2
        assert {r.label for r in rows} == {UNKNOWN}

    def test_anchor_falls_back_to_the_earliest_opportunity(self, db_session):
        brand = make_brand(db_session, first_filing_date=None)
        make_source_record(db_session, brand)
        summary = label_brands(db_session, as_of=date(2025, 10, 1))
        assert summary.reasons[3]["horizon not elapsed"] == 1

    def test_never_searches(self, db_session, monkeypatch):
        from src.enrich.web import WebEnricher

        def boom(*args, **kwargs):  # type: ignore[no-untyped-def]
            raise AssertionError("the labeller must never search")

        monkeypatch.setattr(WebEnricher, "enrich", boom)
        make_brand(db_session)
        label_brands(db_session, as_of=date(2026, 10, 1))

    def test_unique_per_brand_horizon_version(self, db_session):
        from sqlalchemy.exc import IntegrityError

        brand = make_brand(db_session)
        for _ in range(2):
            db_session.add(
                Outcome(
                    brand_id=brand.id,
                    horizon_months=3,
                    label=UNKNOWN,
                    criteria_met={},
                    evidence=[],
                    as_of_date=date(2026, 1, 1),
                    labeller_version="1",
                )
            )
        with pytest.raises(IntegrityError):
            db_session.flush()
        db_session.rollback()

    def test_deleting_a_brand_deletes_its_outcomes(self, db_session):
        from src.db.tables import Brand

        brand = make_brand(db_session)
        label_brands(db_session, as_of=date(2026, 10, 1))
        db_session.delete(db_session.get(Brand, brand.id))
        db_session.flush()
        assert db_session.execute(select(Outcome)).scalars().all() == []
