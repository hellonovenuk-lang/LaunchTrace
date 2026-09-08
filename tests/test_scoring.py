"""LaunchTrace Score calculation, caps and explanations."""

from __future__ import annotations

from datetime import date

import pytest

from src.models import LaunchStage, RetailPresence, ScoreBand
from src.score.launchtrace_score import LaunchTraceScorer, ScoringContext
from tests.conftest import make_assessment, make_company, make_record, make_web


def context(**overrides):  # type: ignore[no-untyped-def]
    base = {
        "record": make_record(),
        "product": make_assessment(),
        "company": make_company(),
        "web": make_web(
            attempted=True,
            provider="fixture",
            website="https://crumbledge.co.uk",
            website_maturity="early_stage",
            major_retailer_presence=False,
            launch_evidence=["coming soon"],
            retail_presence=RetailPresence.NONE_FOUND,
        ),
        "launch_stage": LaunchStage.PRE_LAUNCH,
        "company_age_years": 0.5,
    }
    base.update(overrides)
    return ScoringContext(**base)  # type: ignore[arg-type]


class TestBands:
    def test_ideal_record_scores_high(self, scorer):
        score = scorer.score(context())
        assert score.band == ScoreBand.HIGH
        assert score.value >= 80

    def test_established_brand_scores_low(self, scorer):
        score = scorer.score(
            context(
                company_age_years=14.0,
                web=make_web(
                    attempted=True,
                    website_maturity="established",
                    major_retailer_presence=True,
                    retail_presence=RetailPresence.MULTIPLE_RETAIL,
                ),
                launch_stage=LaunchStage.ESTABLISHED,
            )
        )
        assert score.band == ScoreBand.SUPPRESS

    def test_major_brand_owner_is_pushed_out_of_delivery(self, scorer):
        score = scorer.score(context(is_major_brand_owner=True))
        assert score.band == ScoreBand.SUPPRESS

    def test_score_stays_within_bounds(self, scorer):
        for ctx in (context(), context(is_major_brand_owner=True, company_age_years=30.0)):
            score = scorer.score(ctx)
            assert 0 <= score.value <= 100


class TestCaps:
    def test_no_company_match_caps_below_high(self, scorer):
        score = scorer.score(context(company=make_company(matched=False, match_confidence=0)))
        assert score.value <= 79
        assert score.band != ScoreBand.HIGH

    def test_cap_reason_names_the_missing_evidence(self, scorer):
        """A record that would otherwise score HIGH is capped, and says why."""
        score = scorer.score(
            context(company=make_company(matched=False, match_confidence=0), company_age_years=0.2)
        )
        if score.capped:
            assert "Companies House" in (score.cap_reason or "")

    def test_weak_company_match_caps_below_high(self, scorer):
        score = scorer.score(context(company=make_company(match_confidence=60)))
        assert score.value <= 79

    def test_no_product_category_caps_below_high(self, scorer):
        score = scorer.score(context(product=make_assessment(product_category=None)))
        assert score.value <= 79

    def test_no_web_evidence_caps_below_high(self, scorer):
        score = scorer.score(context(web=make_web(attempted=False)))
        assert score.value <= 79
        assert score.capped is True


class TestEvidenceNormalisation:
    def test_missing_web_evidence_does_not_double_penalise(self, scorer):
        """A record we never researched should not score as one that failed research."""
        without_web = scorer.score(context(web=make_web(attempted=False)))
        failed_research = scorer.score(
            context(
                web=make_web(
                    attempted=True,
                    website=None,
                    website_maturity="established",
                    major_retailer_presence=True,
                    retail_presence=RetailPresence.MULTIPLE_RETAIL,
                )
            )
        )
        assert without_web.value > failed_research.value

    def test_scaling_is_bounded(self, scorer):
        score = scorer.score(context(web=make_web(attempted=False)))
        assert score.value <= 100


class TestReasons:
    def test_every_score_carries_reasons(self, scorer):
        score = scorer.score(context())
        assert score.reasons
        assert all(r.text for r in score.reasons)

    def test_reasons_are_ordered_by_weight(self, scorer):
        score = scorer.score(context())
        weights = [r.weight for r in score.reasons]
        assert weights == sorted(weights, reverse=True)

    def test_company_age_is_rendered_in_months(self, scorer):
        score = scorer.score(context(company_age_years=0.5))
        assert any("6 months" in r.text for r in score.reasons)

    def test_no_reason_contains_an_unfilled_placeholder(self, scorer):
        for ctx in (
            context(),
            context(company=make_company(matched=False)),
            context(company_age_years=None),
        ):
            score = scorer.score(ctx)
            for reason in score.reasons + score.negative_reasons:
                assert "{" not in reason.text and "}" not in reason.text

    def test_customer_facing_reason_count_is_limited(self, scorer):
        score = scorer.score(context())
        assert len(score.top_reasons) <= 6
        assert len(score.reason_texts) <= 6

    def test_full_reasoning_is_retained_for_audit(self, scorer):
        score = scorer.score(context())
        assert len(score.reasons) >= len(score.top_reasons)

    def test_negative_reasons_are_kept_separately(self, scorer):
        score = scorer.score(context(is_natural_person=True))
        assert any(r.key == "natural_person_applicant" for r in score.negative_reasons)
        assert not any(r.key == "natural_person_applicant" for r in score.reasons)


class TestIndicators:
    @pytest.mark.parametrize(
        "age,expected_key",
        [
            (0.5, "company_incorporated_within_12m"),
            (1.8, "company_incorporated_within_24m"),
            (3.5, "company_incorporated_within_48m"),
        ],
    )
    def test_age_bands(self, scorer, age, expected_key):
        score = scorer.score(context(company_age_years=age))
        assert any(r.key == expected_key for r in score.reasons)
        assert len(score.top_reasons) <= 6

    def test_portfolio_filing_is_penalised(self, scorer):
        single = scorer.score(context(applicant_journal_mark_count=1))
        portfolio = scorer.score(context(applicant_journal_mark_count=6))
        assert portfolio.value < single.value

    def test_broad_class_profile_is_penalised(self, scorer):
        narrow = scorer.score(context())
        broad = scorer.score(
            context(record=make_record(nice_classes=[29, 30, 31, 32, 33, 35, 39, 41, 43]))
        )
        assert broad.value < narrow.value

    def test_missing_goods_text_is_penalised(self, scorer):
        with_text = scorer.score(context())
        without = scorer.score(
            context(record=make_record(goods_text=None, goods_text_available=False))
        )
        assert without.value < with_text.value

    def test_service_business_sic_is_penalised(self, scorer):
        normal = scorer.score(context())
        service = scorer.score(context(service_business_sic_only="56101"))
        assert service.value < normal.value

    def test_marketplace_presence_is_penalised(self, scorer):
        clean = scorer.score(context())
        marketplace = scorer.score(
            context(
                web=make_web(
                    attempted=True,
                    website="https://x.test",
                    website_maturity="early_stage",
                    major_retailer_presence=False,
                    marketplace_presence=True,
                    retail_presence=RetailPresence.MARKETPLACE,
                )
            )
        )
        assert marketplace.value < clean.value


class TestConfigurability:
    def test_weights_come_from_config(self):
        config = {
            "base_score": 0,
            "min_score": 0,
            "max_score": 100,
            "bands": [
                {"key": "HIGH", "min": 50, "max": 100, "label": "High"},
                {"key": "MEDIUM", "min": 25, "max": 49, "label": "Medium"},
                {"key": "SUPPRESS", "min": 0, "max": 24, "label": "Low"},
            ],
            "positive_indicators": [
                {"key": "word_mark", "weight": 60, "reason_template": "Word mark"}
            ],
            "negative_indicators": [],
            "confidence_requirements": {},
            "high_band_cap_without_company_match": 100,
            "max_reasons_shown": 6,
        }
        scorer = LaunchTraceScorer(config=config)
        score = scorer.score(context())
        assert score.value == 60
        assert score.band == ScoreBand.HIGH

    def test_filing_date_drives_age_so_history_and_live_runs_agree(self, scorer):
        """Age is measured at filing, so a 2018 journal scores as it did in 2018."""
        old_filing = make_record(filing_date=date(2018, 1, 1))
        company = make_company(incorporation_date=date(2017, 7, 1))
        assert company.age_years_at(old_filing.filing_date) == pytest.approx(0.5, abs=0.02)
