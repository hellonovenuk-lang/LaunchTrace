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


class TestPartialNamesAreNotIdentities:
    """A shared word is a coincidence; a company number is a claim about a real firm.

    These are the shapes that actually went wrong on live journal data. Each one
    attached a stranger's incorporation date to a record, and the score read
    that date as the brand's age.
    """

    @pytest.mark.parametrize(
        "applicant,registered",
        [
            ("Melissa Bent", "MELISSA 27 LIMITED"),
            ("Cameron Hunter", "CAMERON & CAMERON LIMITED"),
            ("Anthony Philip Stratton", "ANTHONY & ANTHONY LIMITED"),
            ("The Secretary of State for Defence", "SECRETARY LTD"),
            ("Fresh Essential Limited", "FRESH & CO GROUP LIMITED"),
            ("Nomad Caviar Limited", "NOMAD 3 LTD"),
            ("Lion's Gate Hot Sauce Ltd", "LION & CO., LTD."),
            ("Wonderful Pistachios & Almonds LLC", "WONDERFUL 1 LTD"),
        ],
    )
    def test_a_single_shared_word_is_not_a_match(self, applicant, registered):
        match = best_match(applicant, [candidate(registered)], provider="test")
        assert match.matched is False
        assert match.company_number is None
        assert match.incorporation_date is None

    def test_the_rejection_says_which_words_were_missing(self):
        match = best_match(
            "Fresh Essential Limited", [candidate("FRESH & CO GROUP LIMITED")], provider="test"
        )
        assert any("shares only part" in e for e in match.match_evidence)

    @pytest.mark.parametrize(
        "applicant,registered",
        [
            ("Hawkstone Farms LTD", "HAWKSTONE FARMS LTD"),
            ("Sushi Factory & Beyond Limited", "THE SUSHI FACTORY & BEYOND LTD"),
            ("Heavenly Foods and Beverages Ltd", "HEAVENLY FOODS & BEVERAGES LTD"),
            ("Crumbledge Foods Ltd", "CRUMBLEDGE FOODS (UK) LIMITED"),
            ("Moorfoot Granola Ltd", "MOORFOOT GRANOLA HOLDINGS LIMITED"),
        ],
    )
    def test_genuine_matches_are_untouched(self, applicant, registered):
        match = best_match(applicant, [candidate(registered)], provider="test")
        assert match.matched is True

    def test_an_extra_distinctive_word_means_a_different_company(self):
        """'ACME LTD' and 'ACME FOODS LTD' are two companies, not one."""
        match = best_match("Acme Ltd", [candidate("ACME FOODS LTD")], provider="test")
        assert match.matched is False
