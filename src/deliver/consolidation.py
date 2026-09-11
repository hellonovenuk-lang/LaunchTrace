"""One company, one opportunity.

A supplier sells to a company, not to a trade mark.  When a business protects
three names in the same week the register shows three rows, and a feed built
straight from those rows shows a packaging buyer the same prospect three times
-- which reads as padding, and makes the weekly count larger than the number of
conversations it is actually worth having.

So qualifying marks belonging to one company are collapsed into a single
customer-facing opportunity.  Nothing is discarded: every trade mark number,
brand name, product category and source URL survives underneath the primary
record as supporting context, and the marks that were folded in are marked
suppressed with a reason rather than dropped, so the funnel still adds up.

Companies are matched by Companies House number where there is one, and by
normalised applicant name where there is not.  Two different companies that
merely look alike are never merged.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.models import Opportunity, RelatedMark

CONSOLIDATION_REASON = "consolidated_into_company_opportunity"


@dataclass
class ConsolidationOutcome:
    opportunities: list[Opportunity]
    companies_consolidated: int = 0
    marks_consolidated: int = 0


def _primary_rank(opp: Opportunity) -> tuple:
    """Which mark best represents the company to a supplier.

    The strongest signal first, then the one whose product we actually know,
    then the earliest filing, so the choice is stable across re-runs.
    """
    return (
        opp.score.value,
        1 if opp.product_category else 0,
        1 if (opp.goods_summary or "").strip() else 0,
        -(opp.filing_date.toordinal() if opp.filing_date else 0),
        opp.trademark_number,
    )


def _as_related(opp: Opportunity) -> RelatedMark:
    return RelatedMark(
        trademark_number=opp.trademark_number,
        brand_name=opp.brand_name,
        product_category=opp.product_category,
        product_category_label=opp.product_category_label,
        nice_classes=list(opp.nice_classes),
        filing_date=opp.filing_date,
        goods_summary=opp.goods_summary,
        source_url=opp.source_url,
    )


def consolidate(opportunities: list[Opportunity]) -> ConsolidationOutcome:
    """Collapse same-company marks, preserving every one of them."""
    groups: dict[str, list[Opportunity]] = {}
    for opp in opportunities:
        groups.setdefault(opp.company_key, []).append(opp)

    kept: list[Opportunity] = []
    companies = 0
    marks = 0
    for members in groups.values():
        members.sort(key=_primary_rank, reverse=True)
        primary, rest = members[0], members[1:]
        if rest:
            companies += 1
            marks += len(rest)
            primary.related_marks = [_as_related(o) for o in rest]
            classes = set(primary.nice_classes)
            for other in rest:
                other.suppressed = True
                other.suppression_reason = CONSOLIDATION_REASON
                classes.update(other.nice_classes)
            primary.nice_classes = sorted(classes)
        kept.append(primary)

    kept.sort(key=lambda o: o.score.value, reverse=True)
    return ConsolidationOutcome(
        opportunities=kept, companies_consolidated=companies, marks_consolidated=marks
    )
