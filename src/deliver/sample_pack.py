"""The customer-facing sample package.

Distinct from the internal validation reports on purpose. A validation report
exists to let an operator judge whether the signal is real; a sample exists to
let a supplier judge whether the feed is worth £79 a month. They need different
things, and mixing them makes both worse.

What a sample is allowed to contain:

* opportunities that cleared the deliverable band on their own merit;
* the score, and the reasons behind it, so the reader can disagree;
* inferred supplier relevance, phrased as relevance and never as purchase intent;
* a link to the public source record.

What it must never contain: suppressed records, unmatched companies, internal
match methods, rejection reasons, or LLM traces.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from src.errors import RenderFailureError
from src.logging_setup import get_logger
from src.sales.leads import Lead
from src.sales.models import today
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)

TEMPLATE_DIR = Path(__file__).parent / "templates"

SAMPLE_CSV_COLUMNS = [
    "brand_name",
    "product_category",
    "goods_summary",
    "company_name",
    "company_number",
    "company_incorporation_date",
    "company_region",
    "website",
    "launch_stage",
    "retail_presence",
    "launchtrace_score",
    "score_band",
    "why_selected",
    "flexible_packaging_relevance",
    "label_relevance",
    "carton_relevance",
    "contract_manufacturing_relevance",
    "copacking_relevance",
    "distribution_relevance",
    "brokerage_relevance",
    "fulfilment_relevance",
    "marketing_relevance",
    "trademark_number",
    "filing_date",
    "publication_date",
    "source_url",
]

_STAGE_LABELS = {
    "pre_launch": "Pre-launch — brand protected, no retail presence found",
    "early_launch": "Early launch — selling, limited distribution",
    "scaling": "Scaling — established distribution",
    "established": "Established",
    "unknown": "Not established from available evidence",
}


@dataclass
class SamplePack:
    """Everything the sample produced, and where each part was written."""

    csv_path: Path
    html_path: Path
    leads: list[Lead] = field(default_factory=list)
    high_count: int = 0
    medium_count: int = 0
    journals: list[str] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)
    supplier_label: str = ""

    @property
    def count(self) -> int:
        return len(self.leads)


def qualify_for_sample(
    leads: list[Lead], minimum_score: int = 60
) -> tuple[list[Lead], dict[str, int]]:
    """Filter to what a customer may see, and count what was held back.

    The exclusions are the product's credibility. An unmatched company or a
    suppressed record in a sample is worse than a shorter sample.
    """
    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    kept: list[Lead] = []
    for lead in leads:
        if lead.suppressed:
            drop("suppressed record")
        elif lead.band not in {"HIGH", "MEDIUM"}:
            drop("below the deliverable band")
        elif lead.score < minimum_score:
            drop("below the minimum score")
        elif not lead.company_number:
            drop("no verified Companies House match")
        else:
            kept.append(lead)
    kept.sort(key=lambda item: item.score, reverse=True)
    return kept, excluded


def _intent_tags(lead: Lead, limit: int = 4) -> list[str]:
    labels = {
        c["key"]: c["label"] for c in load_config("buying_intent.json")["supplier_categories"]
    }
    order = {"HIGH": 3, "MEDIUM": 2}
    pairs = [
        (labels.get(key, key.replace("_", " ")), band)
        for key, band in lead.intents.items()
        if band in order
    ]
    pairs.sort(key=lambda p: order.get(p[1], 0), reverse=True)
    return [f"{name}: {band}" for name, band in pairs[:limit]]


def _write_sample_csv(leads: list[Lead], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=SAMPLE_CSV_COLUMNS)
        writer.writeheader()
        for lead in leads:
            writer.writerow(
                {
                    "brand_name": lead.brand_name,
                    "product_category": lead.display_category,
                    "goods_summary": lead.goods_summary,
                    "company_name": lead.display_company,
                    "company_number": lead.company_number,
                    "company_incorporation_date": (
                        lead.company_incorporation_date.isoformat()
                        if lead.company_incorporation_date
                        else ""
                    ),
                    "company_region": lead.company_region,
                    "website": lead.website,
                    "launch_stage": _STAGE_LABELS.get(lead.launch_stage, lead.launch_stage),
                    "retail_presence": lead.retail_presence,
                    "launchtrace_score": lead.score,
                    "score_band": lead.band,
                    "why_selected": " | ".join(lead.reasons[:6]),
                    "flexible_packaging_relevance": lead.intent("flexible_packaging"),
                    "label_relevance": lead.intent("labels"),
                    "carton_relevance": lead.intent("cartons"),
                    "contract_manufacturing_relevance": lead.intent("contract_manufacturing"),
                    "copacking_relevance": lead.intent("copacking"),
                    "distribution_relevance": lead.intent("distribution"),
                    "brokerage_relevance": lead.intent("brokerage"),
                    "fulfilment_relevance": lead.intent("fulfilment"),
                    "marketing_relevance": lead.intent("marketing"),
                    "trademark_number": lead.trademark_number,
                    "filing_date": lead.filing_date.isoformat() if lead.filing_date else "",
                    "publication_date": (
                        lead.publication_date.isoformat() if lead.publication_date else ""
                    ),
                    "source_url": lead.source_url,
                }
            )
    return path


def render_sample_report(
    leads: list[Lead],
    watermark: bool = True,
    supplier_label: str = "",
    prepared_on: date | None = None,
    settings: Settings | None = None,
) -> str:
    """The standalone HTML report a supplier can open, print or forward."""
    settings = settings or get_settings()
    prepared = prepared_on or today()
    journals = sorted({lead.journal_number for lead in leads if lead.journal_number})

    items = []
    for lead in leads:
        items.append(
            {
                "brand": lead.brand_name,
                "trademark_number": lead.trademark_number,
                "category": lead.display_category,
                "goods": lead.goods_summary,
                "company": lead.display_company,
                "company_number": lead.company_number,
                "incorporated": (
                    lead.company_incorporation_date.strftime("%B %Y")
                    if lead.company_incorporation_date
                    else ""
                ),
                "region": lead.company_region.title() if lead.company_region else "",
                "website": lead.website,
                "stage": _STAGE_LABELS.get(lead.launch_stage, "Not established"),
                "score": lead.score,
                "band": lead.band,
                "reasons": lead.reasons[:6],
                "intents": _intent_tags(lead),
                "source_url": lead.source_url,
            }
        )

    intro = (
        f"{len(leads)} UK food brand{'s' if len(leads) != 1 else ''} identified from newly "
        "published trade mark activity, each verified against Companies House and scored on "
        "how early-stage the brand appears. Every entry shows the reasoning behind its score."
    )
    if not leads:
        intro = (
            "No brands cleared the qualification thresholds for this sample. Nothing has been "
            "removed — the feed had nothing worth a sales team's time."
        )

    env = Environment(
        loader=FileSystemLoader(TEMPLATE_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    try:
        return env.get_template("sample_report.html.j2").render(
            title="LaunchTrace Food — sample feed" if watermark else "LaunchTrace Food feed",
            intro=intro,
            leads=items,
            watermark=watermark,
            high_count=sum(1 for item in leads if item.band == "HIGH"),
            medium_count=sum(1 for item in leads if item.band == "MEDIUM"),
            journals=journals or ["—"],
            prepared_on=prepared.strftime("%-d %B %Y"),
            supplier_label=supplier_label,
            site_url=settings.site_url,
            unsubscribe_url=f"{settings.site_url.rstrip('/')}/unsubscribe",
        )
    except Exception as exc:  # a sample that will not render must not be sent
        raise RenderFailureError(f"Sample report failed to render: {exc}") from exc


def build_sample_pack(
    leads: list[Lead],
    out_dir: Path,
    watermark: bool = True,
    supplier_label: str = "",
    minimum_score: int = 60,
    settings: Settings | None = None,
) -> SamplePack:
    """Produce the whole customer-facing package in one directory."""
    qualified, excluded = qualify_for_sample(leads, minimum_score=minimum_score)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = _write_sample_csv(qualified, out_dir / "launchtrace_food_sample.csv")
    html = render_sample_report(
        qualified, watermark=watermark, supplier_label=supplier_label, settings=settings
    )
    html_path = out_dir / "launchtrace_food_sample.html"
    html_path.write_text(html, encoding="utf-8")

    pack = SamplePack(
        csv_path=csv_path,
        html_path=html_path,
        leads=qualified,
        high_count=sum(1 for lead in qualified if lead.band == "HIGH"),
        medium_count=sum(1 for lead in qualified if lead.band == "MEDIUM"),
        journals=sorted({lead.journal_number for lead in qualified if lead.journal_number}),
        excluded=excluded,
        supplier_label=supplier_label,
    )
    log.info("sample.built", count=pack.count, out_dir=str(out_dir))
    return pack


__all__ = [
    "SAMPLE_CSV_COLUMNS",
    "SamplePack",
    "build_sample_pack",
    "qualify_for_sample",
    "render_sample_report",
]
