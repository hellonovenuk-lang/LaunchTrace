"""Commercial CSV export.

Only fields a sales team can act on.  Internal debug state (match methods,
rejection reasons, LLM traces) stays out of the customer file -- it belongs in
the QA report.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from src.models import Opportunity

CSV_COLUMNS: list[str] = [
    "brand_name",
    "trademark_number",
    "filing_date",
    "publication_date",
    "product_category",
    "goods_summary",
    "company_name",
    "company_number",
    "company_incorporation_date",
    "company_region",
    "website",
    "contact_page",
    "launch_stage",
    "retail_presence",
    "packaging_relevance",
    "label_relevance",
    "carton_relevance",
    "contract_manufacturing_relevance",
    "copacking_relevance",
    "distribution_relevance",
    "brokerage_relevance",
    "fulfilment_relevance",
    "marketing_relevance",
    "launchtrace_score",
    "score_band",
    "score_reasons",
    "source_url",
    "evidence_urls",
]

_HUMAN_STAGE = {
    "pre_launch": "Pre-launch",
    "early_launch": "Early launch",
    "scaling": "Scaling",
    "established": "Established",
    "unknown": "Unknown",
}
_HUMAN_RETAIL = {
    "none_found": "None found",
    "direct_only": "Direct / own site only",
    "marketplace": "Marketplace listing found",
    "independent_retail": "Independent retail",
    "multiple_retail": "Multiple retailers",
    "unknown": "Unknown",
}


def opportunity_to_row(opp: Opportunity) -> dict[str, str]:
    intent = opp.buying_intent
    return {
        "brand_name": opp.brand_name or "",
        "trademark_number": opp.trademark_number,
        "filing_date": opp.filing_date.isoformat() if opp.filing_date else "",
        "publication_date": opp.publication_date.isoformat() if opp.publication_date else "",
        "product_category": opp.product_category_label or opp.product_category or "",
        "goods_summary": opp.goods_summary or "",
        "company_name": opp.company.company_name or "",
        "company_number": opp.company.company_number or "",
        "company_incorporation_date": (
            opp.company.incorporation_date.isoformat() if opp.company.incorporation_date else ""
        ),
        "company_region": opp.company.region or opp.company.post_town or "",
        "website": opp.web.website or "",
        "contact_page": opp.web.contact_page or "",
        "launch_stage": _HUMAN_STAGE.get(opp.launch_stage.value, opp.launch_stage.value),
        "retail_presence": _HUMAN_RETAIL.get(opp.retail_presence.value, opp.retail_presence.value),
        "packaging_relevance": intent.flexible_packaging.value,
        "label_relevance": intent.labels.value,
        "carton_relevance": intent.cartons.value,
        "contract_manufacturing_relevance": intent.contract_manufacturing.value,
        "copacking_relevance": intent.copacking.value,
        "distribution_relevance": intent.distribution.value,
        "brokerage_relevance": intent.brokerage.value,
        "fulfilment_relevance": intent.fulfilment.value,
        "marketing_relevance": intent.marketing.value,
        "launchtrace_score": str(opp.score.value),
        "score_band": opp.score.band.value,
        "score_reasons": " | ".join(opp.score.reason_texts),
        "source_url": opp.source_url or "",
        "evidence_urls": " ".join(opp.evidence_urls[:5]),
    }


def write_opportunities_csv(opportunities: Iterable[Opportunity], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for opp in opportunities:
            writer.writerow(opportunity_to_row(opp))
    return path
