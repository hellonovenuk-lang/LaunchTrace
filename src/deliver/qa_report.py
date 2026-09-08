"""Internal QA report.

Produced on every run, before anything is sent.  It is the thing an operator
reads on a Friday morning to decide whether the week's feed is safe to release.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

from src.models import Opportunity, PipelineResult
from src.settings import load_config


def _volume_flags(result: PipelineResult, history: list[dict[str, Any]] | None) -> list[str]:
    guard = load_config("validation_bands.json")["volume_guardrails"]
    flags: list[str] = []
    raw = result.counts.raw_records
    by_source = guard.get("min_expected_records_per_journal_by_source", {})
    minimum = int(by_source.get(result.journal.source_name, guard["min_expected_records_per_journal"]))
    if raw < minimum:
        flags.append(
            f"Record volume is unusually LOW: {raw} parsed, expected at least {minimum}."
        )
    if raw > guard["max_expected_records_per_journal"]:
        flags.append(
            f"Record volume is unusually HIGH: {raw} parsed, expected at most "
            f"{guard['max_expected_records_per_journal']}."
        )
    if history:
        previous = [h["raw_records"] for h in history if h.get("raw_records")]
        if previous:
            avg = sum(previous) / len(previous)
            if avg and (raw / avg > guard["max_week_on_week_change_ratio"] or avg / max(raw, 1) > guard["max_week_on_week_change_ratio"]):
                flags.append(
                    f"Record volume changed sharply: {raw} this week against a {avg:.0f} average "
                    f"over the previous {len(previous)} week(s)."
                )
    return flags


def _major_brand_detections(result: PipelineResult) -> list[str]:
    return [
        f"{r.trademark_number} — {r.applicant_name} ({r.detail})"
        for r in result.rejected
        if r.reason == "major_brand_owner"
    ][:20]


def _duplicate_flags(opportunities: list[Opportunity]) -> list[str]:
    seen = Counter(o.dedupe_key for o in opportunities)
    return [f"Duplicate dedupe key emitted {n} times: {k}" for k, n in seen.items() if n > 1]


def _suspicious_opportunities(opportunities: list[Opportunity]) -> list[str]:
    """Cross-checks the pipeline's own output against the rules it should have applied."""
    allowed_days = int(
        load_config("exclusions.json")["company_age_limits"].get(
            "max_months_incorporated_after_filing", 6
        )
        * 30
    )
    notes: list[str] = []
    for o in opportunities:
        if o.score.band.value == "HIGH" and not o.company.matched:
            notes.append(f"{o.trademark_number} scored HIGH with no company match (should be capped)")
        if o.score.band.value == "HIGH" and o.company.match_confidence < 70:
            notes.append(
                f"{o.trademark_number} scored HIGH on a {o.company.match_confidence}% company match"
            )
        if o.company.incorporation_date and o.filing_date:
            days_after = (o.company.incorporation_date - o.filing_date).days
            if days_after > allowed_days:
                notes.append(
                    f"{o.trademark_number}: matched company was incorporated {days_after} days after "
                    "the filing date — likely a wrong match that the pipeline should have discarded"
                )
        if not o.brand_name:
            notes.append(f"{o.trademark_number} has no brand name")
    return notes[:30]


def build_qa_report(
    result: PipelineResult,
    history: list[dict[str, Any]] | None = None,
    send_mode: str = "review",
) -> dict[str, Any]:
    counts = result.counts
    deliverable = result.deliverable
    scores = [o.score.value for o in deliverable]

    concerns: list[str] = []
    concerns += _volume_flags(result, history)
    concerns += _duplicate_flags(deliverable)
    concerns += _suspicious_opportunities(deliverable)

    if counts.food_class_candidates and counts.company_matched == 0:
        concerns.append(
            "No applicants were matched to Companies House at all — check the registry provider."
        )
    if counts.packaged_food_candidates:
        match_rate = counts.company_matched / counts.packaged_food_candidates
        if match_rate < 0.25:
            concerns.append(
                f"Company match rate is low: {counts.company_matched}/"
                f"{counts.packaged_food_candidates} ({match_rate:.0%})."
            )
    if counts.llm_failures:
        concerns.append(f"{counts.llm_failures} LLM responses failed schema validation.")
    if counts.enrichment_failures:
        concerns.append(f"{counts.enrichment_failures} records failed enrichment and were downranked.")

    return {
        "run_id": result.run_id,
        "generated_on": date.today().isoformat(),
        "journal_number": result.journal.journal_number,
        "publication_date": result.journal.publication_date.isoformat(),
        "source": result.journal.source_name,
        "status": result.status.value,
        "send_mode": send_mode,
        "blocked_reason": result.blocked_reason,
        "funnel": counts.model_dump(),
        "score_summary": {
            "delivered": len(deliverable),
            "high": counts.high,
            "medium": counts.medium,
            "suppressed": counts.suppressed,
            "min_score": min(scores) if scores else None,
            "max_score": max(scores) if scores else None,
            "mean_score": round(sum(scores) / len(scores), 1) if scores else None,
        },
        "top_rejection_reasons": sorted(
            counts.rejection_reasons.items(), key=lambda kv: kv[1], reverse=True
        )[:12],
        "major_brand_detections": _major_brand_detections(result),
        "data_quality_concerns": concerns,
        "warnings": result.warnings,
        "errors": result.errors,
        "requires_approval": send_mode == "review",
    }


def write_qa_report(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    return path


def format_qa_report(report: dict[str, Any]) -> str:
    """Human-readable version for the terminal and for the alert email."""
    f = report["funnel"]
    lines = [
        f"LaunchTrace QA — journal {report['journal_number']} ({report['publication_date']})",
        f"Run {report['run_id']} · status {report['status']} · send mode {report['send_mode']}",
        "",
        "Funnel",
        f"  raw records parsed          {f['raw_records']}",
        f"  food-class candidates       {f['food_class_candidates']}",
        f"  packaged-food candidates    {f['packaged_food_candidates']}",
        f"  UK corporate applicants     {f['uk_corporate_applicants']}",
        f"  matched to Companies House  {f['company_matched']}",
        f"  emerging-brand candidates   {f['emerging_candidates']}",
        f"  web enriched                {f['web_enriched']}",
        f"  HIGH                        {f['high']}",
        f"  MEDIUM                      {f['medium']}",
        f"  suppressed                  {f['suppressed']}",
        f"  duplicates dropped          {f['duplicates_dropped']}",
        "",
        "Top rejection reasons",
    ]
    for reason, n in report["top_rejection_reasons"]:
        lines.append(f"  {n:>6}  {reason}")
    if report["data_quality_concerns"]:
        lines += ["", "Data-quality concerns"]
        lines += [f"  ! {c}" for c in report["data_quality_concerns"]]
    else:
        lines += ["", "Data-quality concerns: none flagged"]
    if report.get("blocked_reason"):
        lines += ["", f"BLOCKED: {report['blocked_reason']}"]
    return "\n".join(lines)
