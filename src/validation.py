"""Historical validation.

Answers the question the business actually turns on: *does UK trade mark
activity reliably surface genuinely emerging food brands, in enough volume, for
a weekly supplier feed to be worth paying for?*

It processes several complete journal weeks, records the whole funnel, and
writes the evidence to ``reports/validation/``.  It deliberately does not
declare the business validated -- it produces the numbers and states which band
they fall in under the currently configured assumptions.
"""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from src.deliver.csv_export import CSV_COLUMNS, opportunity_to_row
from src.ingest.base import get_source
from src.logging_setup import get_logger
from src.models import Opportunity, PipelineResult, RunStatus
from src.pipeline_core import Pipeline
from src.settings import REPORTS_DIR, get_settings, load_config

log = get_logger(__name__)

VALIDATION_DIR = REPORTS_DIR / "validation"


def _band_for(average: float, bands: list[dict[str, Any]]) -> dict[str, Any]:
    for band in bands:
        low = float(band["min"])
        high = float(band["max"]) if band["max"] is not None else float("inf")
        if low <= average <= high:
            return band
    return bands[-1]


def _evidence_state(settings) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """What enrichment was actually available for this validation run."""
    from src.classify.llm import get_llm_provider
    from src.enrich.companies_house import get_company_registry
    from src.enrich.web import get_search_provider

    registry = get_company_registry(settings)
    return {
        "company_registry": registry.name,
        "company_registry_live": registry.name != "none",
        "llm_classifier": get_llm_provider(settings).name,
        "web_search": get_search_provider(settings).name,
        "web_search_available": get_search_provider(settings).available,
        "missing_credentials": settings.missing_credentials(),
    }


def run_validation(weeks: int = 4, source_name: str = "open_data") -> int:
    settings = get_settings()
    bands_cfg = load_config("validation_bands.json")
    VALIDATION_DIR.mkdir(parents=True, exist_ok=True)

    source = get_source(source_name, settings)
    try:
        refs = list(reversed(source.available_refs(weeks)))[-weeks:]
    except Exception as exc:
        print(f"Could not list journals from source {source_name}: {exc}")
        return 1
    if not refs:
        print(
            f"No journals available from source {source_name!r}.\n"
            "For the official IPO Open Data route run: python -m src.pipeline fetch-open-data --weeks 4"
        )
        return 1

    pipeline = Pipeline(settings=settings, source=source, output_dir=VALIDATION_DIR / "runs")
    results: list[PipelineResult] = []
    history: list[dict[str, Any]] = []
    known_applicants: set[str] = set()

    for index, ref in enumerate(refs, start=1):
        log.info("validation.week.start", journal=ref.journal_number)
        result = pipeline.run(
            journal_number=ref.journal_number,
            known_applicants=set(known_applicants),
            history=history,
        )
        results.append(result)
        history.append(result.counts.model_dump())
        # Applicants seen in earlier weeks are no longer 'first trade mark'.
        for opp in result.opportunities:
            if opp.applicant_name:
                known_applicants.add(opp.applicant_name.strip().lower())
        _write_week_csv(result, index)

    summary = _build_summary(results, refs, bands_cfg, settings)
    _write_top_opportunities(results)
    _write_rejections(results)

    (VALIDATION_DIR / "4_week_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8"
    )
    (VALIDATION_DIR / "4_week_summary.md").write_text(
        _render_markdown(summary), encoding="utf-8"
    )
    print(_render_markdown(summary))
    print(f"\nWritten to {VALIDATION_DIR}")
    return 0 if all(r.status == RunStatus.COMPLETED for r in results) else 1


def _write_week_csv(result: PipelineResult, index: int) -> Path:
    path = VALIDATION_DIR / f"week_{index}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    deliverable = sorted(result.deliverable, key=lambda o: o.score.value, reverse=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for opp in deliverable:
            writer.writerow(opportunity_to_row(opp))
    return path


def _write_top_opportunities(results: list[PipelineResult]) -> Path:
    everything: list[Opportunity] = []
    for r in results:
        everything.extend(r.deliverable)
    everything.sort(key=lambda o: o.score.value, reverse=True)
    path = VALIDATION_DIR / "top_opportunities.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=[*CSV_COLUMNS, "journal_number"], extrasaction="ignore")
        writer.writeheader()
        for opp in everything:
            row = opportunity_to_row(opp)
            row["journal_number"] = opp.journal_number
            writer.writerow(row)
    return path


def _write_rejections(results: list[PipelineResult]) -> Path:
    """Every record the funnel dropped, so the filtering can be audited."""
    path = VALIDATION_DIR / "rejections.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["journal_number", "trademark_number", "mark_text", "applicant_name", "stage", "reason", "detail"]
        )
        for result in results:
            for rejected in result.rejected:
                writer.writerow(
                    [
                        result.journal.journal_number,
                        rejected.trademark_number,
                        rejected.mark_text or "",
                        rejected.applicant_name or "",
                        rejected.stage,
                        rejected.reason,
                        rejected.detail or "",
                    ]
                )
    return path


def _score_sensitivity(results: list[PipelineResult]) -> list[dict[str, Any]]:
    """How the weekly count moves as the delivery threshold moves.

    The funnel can look thin either because the signal is thin or because the
    threshold is set high. This separates the two.
    """
    scored = [o for r in results for o in r.opportunities]
    weeks = max(len(results), 1)
    rows: list[dict[str, Any]] = []
    for threshold in (75, 70, 65, 60, 55, 50, 45, 40):
        qualifying = [o for o in scored if o.score.value >= threshold]
        rows.append(
            {
                "threshold": threshold,
                "total": len(qualifying),
                "per_week": round(len(qualifying) / weeks, 2),
            }
        )
    return rows


def _score_histogram(results: list[PipelineResult]) -> dict[str, int]:
    buckets = {"80+": 0, "70-79": 0, "60-69": 0, "50-59": 0, "40-49": 0, "below 40": 0}
    for r in results:
        for o in r.opportunities:
            v = o.score.value
            if v >= 80:
                buckets["80+"] += 1
            elif v >= 70:
                buckets["70-79"] += 1
            elif v >= 60:
                buckets["60-69"] += 1
            elif v >= 50:
                buckets["50-59"] += 1
            elif v >= 40:
                buckets["40-49"] += 1
            else:
                buckets["below 40"] += 1
    return buckets


def _build_summary(
    results: list[PipelineResult], refs, bands_cfg: dict, settings
) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    weeks: list[dict[str, Any]] = []
    for index, result in enumerate(results, start=1):
        counts = result.counts
        good = counts.high + counts.medium
        weeks.append(
            {
                "week": index,
                "journal_number": result.journal.journal_number,
                "publication_date": result.journal.publication_date.isoformat(),
                "source": result.journal.source_name,
                "status": result.status.value,
                "total_trademark_applications_parsed": counts.raw_records,
                "food_class_candidates": counts.food_class_candidates,
                "packaged_food_candidates": counts.packaged_food_candidates,
                "uk_corporate_applicants": counts.uk_corporate_applicants,
                "companies_house_matched": counts.company_matched,
                "emerging_brand_candidates": counts.emerging_candidates,
                "web_enriched": counts.web_enriched,
                "high": counts.high,
                "medium": counts.medium,
                "good_opportunities": good,
                "suppressed": counts.suppressed,
                "duplicates_dropped": counts.duplicates_dropped,
                "enrichment_failures": counts.enrichment_failures,
                "llm_failures": counts.llm_failures,
                "top_rejection_reasons": sorted(
                    counts.rejection_reasons.items(), key=lambda kv: kv[1], reverse=True
                )[:8],
                "csv": f"week_{index}.csv",
            }
        )

    completed = [w for w in weeks if w["status"] == "completed"]
    total_good = sum(w["good_opportunities"] for w in completed)
    average = round(total_good / len(completed), 2) if completed else 0.0
    band = _band_for(average, bands_cfg["bands"])
    evidence = _evidence_state(settings)

    caveats: list[str] = []
    if not evidence["web_search_available"]:
        caveats.append(
            "Web enrichment did not run (no search provider configured). Without it the pipeline "
            "cannot verify whether a brand is already established, so every record is capped below "
            "the HIGH band. The HIGH counts here are therefore structurally zero, not a finding "
            "about the signal. Connect SEARCH_PROVIDER and SEARCH_API_KEY and re-run to get the "
            "real HIGH/MEDIUM split."
        )
    if evidence["llm_classifier"] == "none":
        caveats.append(
            "Product classification ran on deterministic rules only (no LLM key configured). "
            "That is more conservative than the full classifier and will under-detect some "
            "packaged-food filings."
        )
    sources = {w["source"] for w in weeks}
    if "ipo_open_data" in sources:
        caveats.append(
            "These weeks came from the IPO Open Data release rather than the weekly journal XML. "
            "The Open Data release does not publish goods and services text, so product "
            "categorisation relied on Nice class, Companies House SIC codes and the brand name. "
            "The weekly journal XML does carry goods text, so live weekly runs have strictly more "
            "evidence than this validation did."
        )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "weeks_processed": len(weeks),
        "weeks_completed": len(completed),
        "average_good_opportunities_per_week": average,
        "total_good_opportunities": total_good,
        "total_high": sum(w["high"] for w in completed),
        "total_medium": sum(w["medium"] for w in completed),
        "verdict_band": band["key"],
        "verdict": band["verdict"],
        "verdict_is_provisional": True,
        "bands": bands_cfg["bands"],
        "evidence": evidence,
        "caveats": caveats,
        "score_sensitivity": _score_sensitivity(results),
        "score_histogram": _score_histogram(results),
        "scored_records_total": sum(w["emerging_brand_candidates"] for w in completed),
        "weeks": weeks,
    }


def _render_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    a = lines.append
    a("# LaunchTrace Food — four-week historical validation")
    a("")
    a(f"Generated {summary['generated_at']}.")
    a("")
    a("## The question this answers")
    a("")
    a(
        "Does UK trade mark activity reliably surface genuinely emerging food brands, early enough "
        "and in enough volume, that suppliers would value receiving them as weekly sales "
        "opportunities?"
    )
    a("")
    a("## Headline")
    a("")
    a(f"- Journal weeks processed: **{summary['weeks_processed']}** ({summary['weeks_completed']} completed)")
    a(f"- Good opportunities (HIGH + MEDIUM) in total: **{summary['total_good_opportunities']}**")
    a(f"- Average per week: **{summary['average_good_opportunities_per_week']}**")
    a(f"- HIGH: **{summary['total_high']}** · MEDIUM: **{summary['total_medium']}**")
    a(f"- Band under the currently configured assumptions: **{summary['verdict_band']}**")
    a("")
    a(f"> {summary['verdict']}")
    a("")
    a(
        "This is the evidence, not a verdict. The bands in `config/validation_bands.json` are "
        "business assumptions we have not yet tested against a paying supplier, and the run below "
        "was made with the enrichment that was actually available."
    )
    a("")
    a("## Enrichment available for this run")
    a("")
    ev = summary["evidence"]
    a("| Stage | Provider |")
    a("| --- | --- |")
    a(f"| Company verification | {ev['company_registry']} |")
    a(f"| Product classification | {ev['llm_classifier']} |")
    a(f"| Web enrichment | {ev['web_search']} |")
    a("")
    if ev["missing_credentials"]:
        a("Credentials not present for this run:")
        a("")
        for key, what in ev["missing_credentials"].items():
            a(f"- `{key}` — {what}")
        a("")
    if summary["caveats"]:
        a("## What limits this result")
        a("")
        for c in summary["caveats"]:
            a(f"- {c}")
        a("")
    a("## Week by week")
    a("")
    a("| Week | Journal | Published | Parsed | Food class | Packaged food | UK corporate | CH matched | Emerging | HIGH | MEDIUM | Suppressed |")
    a("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for w in summary["weeks"]:
        a(
            f"| {w['week']} | {w['journal_number']} | {w['publication_date']} | "
            f"{w['total_trademark_applications_parsed']} | {w['food_class_candidates']} | "
            f"{w['packaged_food_candidates']} | {w['uk_corporate_applicants']} | "
            f"{w['companies_house_matched']} | {w['emerging_brand_candidates']} | "
            f"{w['high']} | {w['medium']} | {w['suppressed']} |"
        )
    a("")
    a("## Is the signal thin, or is the threshold high?")
    a("")
    a(
        "The pipeline scored every emerging-brand candidate; the delivery threshold then decides how "
        "many reach a customer. This table separates those two things. If volume needs to increase, "
        "the honest lever is the threshold in `config/scoring.json`, and the cost of lowering it is "
        "weaker leads."
    )
    a("")
    a("Score distribution across all scored candidates:")
    a("")
    a("| Score band | Records |")
    a("| --- | ---: |")
    for label, count in summary["score_histogram"].items():
        a(f"| {label} | {count} |")
    a("")
    a("Opportunities per week at different delivery thresholds:")
    a("")
    a("| Minimum score | Total over the period | Per week |")
    a("| ---: | ---: | ---: |")
    for row in summary["score_sensitivity"]:
        marker = "  ← current MEDIUM threshold" if row["threshold"] == 60 else ""
        a(f"| {row['threshold']} | {row['total']} | {row['per_week']}{marker} |")
    a("")
    a("## Why records were rejected")
    a("")
    for w in summary["weeks"]:
        a(f"**Week {w['week']} — {w['journal_number']}**")
        a("")
        for reason, count in w["top_rejection_reasons"]:
            a(f"- `{reason}`: {count}")
        a("")
    a("## Files")
    a("")
    a("- `week_1.csv` … `week_N.csv` — the deliverable opportunities for each journal week")
    a("- `top_opportunities.csv` — the strongest opportunities across all weeks, sorted by score")
    a("- `rejections.csv` — every record the funnel dropped, with the stage and reason")
    a("- `4_week_summary.json` — the machine-readable version of this report")
    a("- `runs/<journal>/qa_report.json` — the per-week QA report")
    a("")
    a("## How to read this")
    a("")
    a(
        "The funnel is deliberately narrow. Most of the weekly journal is not food, most food-class "
        "filings are not packaged consumer products, and most packaged-food filings come from "
        "companies that are already established. The number that matters commercially is the last "
        "column pair: how many brands a supplier's sales team could actually act on in a week."
    )
    a("")
    a(
        "Nothing here should be read as proof that suppliers will pay. It shows whether the raw "
        "material exists. The next test is qualitative: send `top_opportunities.csv` to real "
        "suppliers and ask whether these are companies they would want to reach."
    )
    return "\n".join(lines) + "\n"
