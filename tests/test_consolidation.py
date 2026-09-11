"""One company, one opportunity — without throwing trade mark detail away."""

from __future__ import annotations

from datetime import date

from src.deliver.consolidation import CONSOLIDATION_REASON, consolidate
from src.models import Opportunity, Score, ScoreBand
from tests.conftest import make_company


def opp(tm: str, brand: str, score: int, number: str = "14000001", **overrides) -> Opportunity:
    base = {
        "dedupe_key": tm,
        "trademark_number": tm,
        "brand_name": brand,
        "applicant_name": "Crumbledge Foods Ltd",
        "company": make_company(company_number=number),
        "score": Score(value=score, band=ScoreBand.HIGH if score >= 80 else ScoreBand.MEDIUM),
        "product_category": "cereal_bars",
        "nice_classes": [30],
        "filing_date": date(2026, 8, 1),
        "goods_summary": "Cereal bars.",
        "source_url": f"https://example.invalid/{tm}",
    }
    base.update(overrides)
    return Opportunity(**base)  # type: ignore[arg-type]


class TestConsolidation:
    def test_three_marks_from_one_company_become_one_opportunity(self):
        outcome = consolidate([opp("A", "ALPHA", 84), opp("B", "BETA", 77), opp("C", "GAMMA", 70)])
        assert len(outcome.opportunities) == 1
        assert outcome.companies_consolidated == 1
        assert outcome.marks_consolidated == 2

    def test_the_strongest_mark_represents_the_company(self):
        outcome = consolidate([opp("A", "ALPHA", 70), opp("B", "BETA", 88)])
        assert outcome.opportunities[0].brand_name == "BETA"

    def test_no_trade_mark_detail_is_lost(self):
        outcome = consolidate([opp("A", "ALPHA", 88), opp("B", "BETA", 70)])
        primary = outcome.opportunities[0]
        assert [m.trademark_number for m in primary.related_marks] == ["B"]
        assert primary.related_marks[0].brand_name == "BETA"
        assert primary.related_marks[0].source_url
        assert primary.related_marks[0].goods_summary
        assert primary.company_mark_count == 2

    def test_folded_marks_are_marked_suppressed_rather_than_dropped(self):
        records = [opp("A", "ALPHA", 88), opp("B", "BETA", 70)]
        consolidate(records)
        folded = [o for o in records if o.suppressed]
        assert [o.trademark_number for o in folded] == ["B"]
        assert folded[0].suppression_reason == CONSOLIDATION_REASON

    def test_classes_from_every_mark_are_carried_on_the_company(self):
        outcome = consolidate([opp("A", "ALPHA", 88), opp("B", "BETA", 70, nice_classes=[29, 32])])
        assert outcome.opportunities[0].nice_classes == [29, 30, 32]

    def test_different_companies_are_never_merged(self):
        outcome = consolidate([opp("A", "ALPHA", 88), opp("B", "BETA", 84, number="14000002")])
        assert len(outcome.opportunities) == 2
        assert outcome.companies_consolidated == 0

    def test_unmatched_applicants_group_on_their_registered_name(self):
        unmatched = make_company(matched=False, company_number=None)
        outcome = consolidate(
            [
                opp("A", "ALPHA", 70, company=unmatched),
                opp("B", "BETA", 66, company=unmatched),
            ]
        )
        assert len(outcome.opportunities) == 1

    def test_similar_but_different_applicant_names_stay_apart(self):
        unmatched = make_company(matched=False, company_number=None)
        outcome = consolidate(
            [
                opp("A", "ALPHA", 70, company=unmatched, applicant_name="Northfield Foods Ltd"),
                opp("B", "BETA", 66, company=unmatched, applicant_name="Northfield Bakery Ltd"),
            ]
        )
        assert len(outcome.opportunities) == 2

    def test_a_single_mark_company_is_left_exactly_as_it_was(self):
        outcome = consolidate([opp("A", "ALPHA", 88)])
        assert outcome.opportunities[0].related_marks == []
        assert outcome.marks_consolidated == 0

    def test_output_is_ordered_by_score(self):
        outcome = consolidate([opp("A", "ALPHA", 70), opp("B", "BETA", 88, number="14000002")])
        assert [o.score.value for o in outcome.opportunities] == [88, 70]
