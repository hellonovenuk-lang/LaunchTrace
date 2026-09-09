"""Rendering a prospect-specific preview.

Three outputs from the same selection:

* a Markdown briefing the operator reads before deciding to send anything;
* an email-ready lead block that drops straight into Email 1;
* a CSV, for when the operator would rather look at it in a spreadsheet.

Every fact rendered here comes from a stored opportunity. Nothing is
embellished, and a lead with no evidence simply shows less.
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.sales.matching import MatchedLead, PreviewResult

PREVIEW_CSV_COLUMNS = [
    "rank",
    "brand_name",
    "product_category",
    "company_name",
    "company_number",
    "company_region",
    "company_incorporation_date",
    "launch_stage",
    "launchtrace_score",
    "score_band",
    "why_early_stage",
    "primary_buying_intent",
    "why_relevant_to_this_supplier",
    "fit_score",
    "source_url",
    "trademark_number",
]


def _months_between(match: MatchedLead) -> str:
    lead = match.lead
    if not lead.company_incorporation_date or not lead.filing_date:
        return ""
    months = (
        (lead.filing_date.year - lead.company_incorporation_date.year) * 12
        + lead.filing_date.month
        - lead.company_incorporation_date.month
    )
    if months < 0:
        return ""
    if months == 0:
        return "incorporated the same month it filed"
    if months < 24:
        return f"incorporated {months} month{'s' if months != 1 else ''} before filing"
    return f"incorporated {months // 12} years before filing"


def _top_intents(match: MatchedLead, limit: int = 3) -> list[str]:
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1, "NONE": 0}
    pairs = [
        (key.replace("_", " "), band)
        for key, band in match.lead.intents.items()
        if band in {"HIGH", "MEDIUM"}
    ]
    pairs.sort(key=lambda p: order.get(p[1], 0), reverse=True)
    return [f"{name}: {band}" for name, band in pairs[:limit]]


def render_markdown(result: PreviewResult) -> str:
    """The operator's briefing. Written to be read before sending, not sent."""
    prospect = result.prospect
    lines: list[str] = [
        f"# Preview for {prospect.company_name} ({prospect.prospect_id})",
        "",
        f"- **Supplier profile:** {result.profile_label}",
        f"- **Priority:** {prospect.priority.value} (ICP score {prospect.icp_score})",
        f"- **Status:** {prospect.status.value}",
        f"- **Lead source:** {result.source}",
        f"- **Considered:** {result.considered} opportunities · **selected:** {result.count}",
        "",
    ]

    if result.stale:
        lines += [
            f"> **This data is {result.source_age_days} days old.** The most recent opportunity "
            f"here was published on "
            f"{result.newest_publication.isoformat() if result.newest_publication else 'an unknown date'}. "
            "It is fine for checking that the matching works, but do not describe these to a "
            "supplier as brands from this week. Run a live week first.",
            "",
        ]

    if not prospect.contactable:
        lines += [
            "> **DO NOT CONTACT.** This prospect is "
            f"{prospect.status.value.lower().replace('_', ' ')}"
            + (f" — {prospect.suppression_reason}" if prospect.suppression_reason else "")
            + ".",
            "",
        ]

    if not result.matches:
        lines += [
            "## No suitable opportunities",
            "",
            "Nothing in the current feed genuinely suits this supplier. Do not send a "
            "first email with weak examples — wait for a run that produces something "
            "relevant, or reconsider whether this prospect is a fit at all.",
            "",
        ]
    else:
        lines += ["## Leads", ""]
        for index, match in enumerate(result.matches, start=1):
            lead = match.lead
            lines.append(f"### {index}. {lead.brand_name or lead.trademark_number}")
            lines.append("")
            lines.append(f"- **Product:** {lead.display_category or 'Not categorised'}")
            if lead.goods_summary:
                lines.append(f"- **Goods:** {lead.goods_summary[:200]}")
            company = f"- **Applicant / company:** {lead.display_company}"
            if lead.company_number:
                company += f" (company number {lead.company_number})"
            if lead.company_region:
                company += f", {lead.company_region}"
            lines.append(company)
            age = _months_between(match)
            lines.append(
                "- **Why it looks early-stage:** " + match.why_early + (f" — {age}" if age else "")
            )
            intents = _top_intents(match)
            if intents:
                lines.append(f"- **Buying-intent signal:** {'; '.join(intents)}")
            lines.append(
                f"- **LaunchTrace Score:** {lead.score} ({lead.band}) · fit for this supplier "
                f"{match.fit_score}"
            )
            if lead.source_url:
                lines.append(f"- **Source / evidence:** {lead.source_url}")
            if lead.website:
                lines.append(f"- **Brand website:** {lead.website}")
            lines.append(
                f"- **Why it may matter to {prospect.company_name}:** {match.why_relevant}"
            )
            lines.append("")

    if result.shortfall_note:
        lines += ["> " + result.shortfall_note, ""]

    if result.excluded:
        lines += ["## What was excluded, and why", ""]
        for reason, number in sorted(result.excluded.items(), key=lambda kv: -kv[1]):
            lines.append(f"- {number} × {reason}")
        lines.append("")

    lines += [
        "---",
        "",
        "These are commercial signals derived from newly published UK trade mark "
        "activity and Companies House records. They indicate that a brand exists and "
        "looks early. They are not confirmed purchase intent, and no one here has been "
        "contacted.",
        "",
        "Sources: UK Intellectual Property Office and Companies House, "
        "Open Government Licence v3.0.",
    ]
    return "\n".join(lines)


def render_email_block(result: PreviewResult) -> str:
    """The lead bullets, ready to paste into Email 1.

    Plain text on purpose: it goes into an ordinary mailbox, written by a
    person, not into an HTML template.
    """
    if not result.matches:
        return "(No suitable opportunities this week — do not send.)"
    lines: list[str] = []
    for match in result.matches:
        lead = match.lead
        headline = lead.brand_name or lead.trademark_number
        details: list[str] = []
        if lead.display_category:
            details.append(lead.display_category.lower())
        if lead.display_company:
            details.append(lead.display_company)
        if lead.company_region:
            details.append(lead.company_region.title())
        age = _months_between(match)
        if age:
            details.append(age)
        lines.append(f"- {headline} — {', '.join(details)}.")
        lines.append(f"  {match.why_relevant}")
    return "\n".join(lines)


def write_preview_csv(result: PreviewResult, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PREVIEW_CSV_COLUMNS)
        writer.writeheader()
        for index, match in enumerate(result.matches, start=1):
            lead = match.lead
            writer.writerow(
                {
                    "rank": index,
                    "brand_name": lead.brand_name,
                    "product_category": lead.display_category,
                    "company_name": lead.display_company,
                    "company_number": lead.company_number,
                    "company_region": lead.company_region,
                    "company_incorporation_date": (
                        lead.company_incorporation_date.isoformat()
                        if lead.company_incorporation_date
                        else ""
                    ),
                    "launch_stage": lead.launch_stage,
                    "launchtrace_score": lead.score,
                    "score_band": lead.band,
                    "why_early_stage": match.why_early,
                    "primary_buying_intent": "; ".join(_top_intents(match)),
                    "why_relevant_to_this_supplier": match.why_relevant,
                    "fit_score": match.fit_score,
                    "source_url": lead.source_url,
                    "trademark_number": lead.trademark_number,
                }
            )
    return path


__all__ = [
    "PREVIEW_CSV_COLUMNS",
    "render_email_block",
    "render_markdown",
    "write_preview_csv",
]
