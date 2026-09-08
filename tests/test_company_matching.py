"""Company name normalisation and Companies House match confidence."""

from __future__ import annotations

from datetime import date

import pytest

from src.enrich.matching import CandidateCompany, best_match, score_candidate
from src.parse.normalise import (
    company_name_key,
    normalise_company_name,
    normalise_text,
    similarity,
)


class TestNormalisation:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("The Nibbly Snack Company Limited", "nibbly snack"),
            ("CRUMBLEDGE FOODS LTD.", "crumbledge foods"),
            ("Moorfoot Provisions Limited", "moorfoot provisions"),
            ("Hot Cloud Kitchen LLP", "hot cloud kitchen"),
            ("Café Rouge Ltd", "cafe rouge"),
            ("Smith & Sons Ltd", "smith and sons"),
        ],
    )
    def test_strips_legal_suffixes_and_normalises(self, raw, expected):
        assert normalise_company_name(raw) == expected

    def test_key_is_whitespace_free(self):
        assert company_name_key("Crumbledge Foods Ltd") == "crumbledgefoods"

    def test_handles_none(self):
        assert normalise_company_name(None) == ""
        assert normalise_text(None) == ""

    def test_similarity_is_symmetric_and_bounded(self):
        assert similarity("Nibbly Foods Ltd", "Nibbly Foods Limited") == 1.0
        assert similarity("Nibbly Foods", "Totally Different") < 0.3
        assert similarity(None, "x") == 0.0


def candidate(name: str, number: str = "12345678", **kwargs) -> CandidateCompany:
    return CandidateCompany(company_name=name, company_number=number, **kwargs)


class TestScoring:
    def test_exact_name_is_highest(self):
        confidence, method, _ = score_candidate(
            "Crumbledge Foods Ltd", candidate("Crumbledge Foods Ltd")
        )
        assert confidence >= 95
        assert method == "exact_name"

    def test_normalised_match_is_high(self):
        confidence, method, _ = score_candidate(
            "Crumbledge Foods Ltd", candidate("CRUMBLEDGE FOODS LIMITED")
        )
        assert confidence >= 90
        assert method == "normalised_name"

    def test_unrelated_names_score_low(self):
        confidence, _, _ = score_candidate("Crumbledge Foods Ltd", candidate("Barclays Bank PLC"))
        assert confidence < 40

    def test_generic_tokens_do_not_create_a_match(self):
        confidence, _, _ = score_candidate(
            "The London Group Ltd", candidate("The London Holdings Ltd")
        )
        assert confidence < 78

    def test_evidence_is_always_returned(self):
        _, _, evidence = score_candidate("Crumbledge Foods Ltd", candidate("Crumbledge Ltd"))
        assert evidence


class TestBestMatch:
    def test_picks_the_exact_candidate(self):
        match = best_match(
            "Crumbledge Foods Ltd",
            [candidate("Crumbledge Holdings Ltd", "1"), candidate("Crumbledge Foods Ltd", "2")],
            provider="test",
        )
        assert match.matched is True
        assert match.company_number == "2"
        assert match.match_confidence >= 95

    def test_returns_unmatched_rather_than_guessing(self):
        match = best_match(
            "Crumbledge Foods Ltd", [candidate("Barclays Bank PLC")], provider="test"
        )
        assert match.matched is False
        assert match.match_evidence

    def test_no_candidates_is_explicit(self):
        match = best_match("Crumbledge Foods Ltd", [], provider="test")
        assert match.matched is False
        assert match.match_method == "no_candidates"

    def test_no_applicant_name_is_explicit(self):
        match = best_match(None, [candidate("Anything Ltd")], provider="test")
        assert match.matched is False
        assert match.match_method == "no_applicant_name"

    def test_ambiguity_reduces_confidence(self):
        match = best_match(
            "Northern Bakery Company Ltd",
            [
                candidate("Northern Bakery Trading Ltd", "1"),
                candidate("Northern Bakery Retail Ltd", "2"),
            ],
            provider="test",
        )
        assert "Ambiguous" in " ".join(match.match_evidence) or match.matched is False

    def test_populates_company_detail(self):
        match = best_match(
            "Crumbledge Foods Ltd",
            [
                candidate(
                    "Crumbledge Foods Ltd",
                    "14000001",
                    incorporation_date=date(2025, 3, 1),
                    sic_codes=("10720",),
                    region="BRISTOL",
                )
            ],
            provider="test",
        )
        assert match.incorporation_date == date(2025, 3, 1)
        assert match.sic_codes == ["10720"]
        assert match.source_url.endswith("14000001")


class TestAgeCalculation:
    def test_age_at_filing_not_age_now(self):
        match = best_match(
            "Crumbledge Foods Ltd",
            [candidate("Crumbledge Foods Ltd", incorporation_date=date(2024, 9, 15))],
            provider="test",
        )
        assert match.age_years_at(date(2025, 9, 15)) == pytest.approx(1.0, abs=0.02)

    def test_missing_dates_return_none(self):
        match = best_match(
            "Crumbledge Foods Ltd", [candidate("Crumbledge Foods Ltd")], provider="test"
        )
        assert match.age_years_at(date(2025, 1, 1)) is None


class TestFixtureRegistry:
    def test_matches_a_known_company(self, company_registry):
        match = company_registry.match("Crumbledge Foods Ltd")
        assert match.matched is True
        assert match.company_number == "14000001"

    def test_unknown_company_is_unmatched(self, company_registry):
        match = company_registry.match("Definitely Not A Real Company Ltd")
        assert match.matched is False
