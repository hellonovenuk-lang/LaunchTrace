"""Choosing which opportunities suit one specific supplier prospect.

The rule that matters most is the one about not filling a list. A preview
returns the leads that genuinely fit; if only one fits, it returns one. The
whole point of the first email is that the examples are relevant, and three
weak examples are worse than one good one.

Nothing here changes a LaunchTrace Score. Fit ranking reorders leads that have
already qualified on their own merit; it never promotes one that has not.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

from src.sales.leads import Lead
from src.sales.models import Prospect, today
from src.settings import load_config


@dataclass
class MatchReason:
    text: str
    points: float


@dataclass
class MatchedLead:
    """A lead, its fit score for one prospect, and why it fits."""

    lead: Lead
    fit_score: float
    reasons: list[MatchReason] = field(default_factory=list)
    supplier_line: str = ""

    @property
    def why_relevant(self) -> str:
        """The one line that goes in the email, specific to this supplier."""
        return self.supplier_line

    @property
    def why_early(self) -> str:
        return self.lead.early_stage_evidence()


@dataclass
class PreviewResult:
    """What the generator found, including what it refused to include."""

    prospect: Prospect
    matches: list[MatchedLead]
    source: str
    considered: int = 0
    profile_label: str = ""
    excluded: dict[str, int] = field(default_factory=dict)
    shortfall_note: str = ""
    newest_publication: date | None = None
    source_age_days: int = 0

    @property
    def count(self) -> int:
        return len(self.matches)

    @property
    def stale(self) -> bool:
        """True when the freshest lead is old enough that the operator must say so.

        A preview built from historical data is still useful for checking that
        the matching works. Sending it to a supplier as "this week" is not, so
        the rendered output carries the warning rather than hiding it.
        """
        return self.source_age_days > 21


_RELEVANCE_ORDER = ["NONE", "LOW", "MEDIUM", "HIGH"]


def _at_least(value: str, minimum: str) -> bool:
    try:
        return _RELEVANCE_ORDER.index(value.upper()) >= _RELEVANCE_ORDER.index(minimum.upper())
    except ValueError:
        return False


def profile_for(supplier_category: str, config: dict | None = None) -> dict:
    cfg = config or load_config("supplier_profiles.json")
    profiles = cfg["profiles"]
    return next(
        (p for p in profiles if p["key"] == supplier_category),
        next(p for p in profiles if p["key"] == "other"),
    )


def _primary_intent(profile: dict) -> str:
    return max(profile["primary_intents"].items(), key=lambda kv: kv[1])[0]


def score_fit(lead: Lead, profile: dict, config: dict) -> tuple[float, list[MatchReason]]:
    """How well one lead suits one supplier profile, and why."""
    points = config["relevance_points"]
    reasons: list[MatchReason] = []
    total = 0.0

    for intent_key, weight in profile["primary_intents"].items():
        band = lead.intent(intent_key)
        value = points.get(band, 0) * (weight / 10.0)
        if value:
            total += value
            if band in {"HIGH", "MEDIUM"} and weight >= 5:
                reasons.append(
                    MatchReason(
                        text=f"{intent_key.replace('_', ' ')} relevance: {band}",
                        points=round(value, 2),
                    )
                )

    if lead.product_category and lead.product_category in profile["preferred_product_groups"]:
        bonus = float(profile["product_group_bonus"])
        total += bonus
        reasons.append(
            MatchReason(
                text=f"{lead.display_category} is a category this supplier serves",
                points=bonus,
            )
        )

    stage_points = config["stage_points"].get(lead.launch_stage, 0)
    if stage_points:
        total += stage_points
        if lead.launch_stage in profile["preferred_stages"]:
            reasons.append(
                MatchReason(
                    text=f"Launch stage {lead.launch_stage.replace('_', ' ')}",
                    points=float(stage_points),
                )
            )

    score_component = lead.score * float(config["score_weight"]) / 10.0
    total += score_component
    reasons.append(
        MatchReason(text=f"LaunchTrace Score {lead.score}", points=round(score_component, 2))
    )

    return round(total, 2), reasons


def select_for_prospect(
    prospect: Prospect,
    leads: list[Lead],
    source: str = "",
    count: int | None = None,
    config: dict | None = None,
    reference_date: date | None = None,
) -> PreviewResult:
    """Pick the strongest genuinely relevant leads for this prospect.

    Returns fewer than ``count`` when fewer than ``count`` qualify. That is the
    intended behaviour, not a failure.
    """
    cfg = config or load_config("supplier_profiles.json")
    preview_cfg = cfg["preview"]
    wanted = count or int(preview_cfg["default_count"])
    profile = profile_for(prospect.supplier_category, cfg)
    primary = _primary_intent(profile)
    max_age = timedelta(days=int(preview_cfg["max_age_days"]))

    # The age limit exists to stop one stale week being mixed into a fresh
    # preview, so it is measured against the newest lead in the source rather
    # than against today. Anchoring it to today would silently empty every
    # preview built from historical validation data, which is the only data
    # that exists before the first live Friday.
    published = [lead.publication_date for lead in leads if lead.publication_date]
    reference = reference_date or (max(published) if published else today())

    excluded: dict[str, int] = {}

    def drop(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    qualified: list[MatchedLead] = []
    for lead in leads:
        if lead.suppressed:
            drop("suppressed")
            continue
        if lead.band not in {"HIGH", "MEDIUM"}:
            drop("below the deliverable band")
            continue
        if lead.score < int(preview_cfg["minimum_score"]):
            drop("below the minimum score")
            continue
        if preview_cfg["require_company_match"] and not lead.company_number:
            drop("no verified Companies House match")
            continue
        if not _at_least(lead.intent(primary), str(preview_cfg["require_intent_at_least"])):
            drop(f"not relevant to {primary.replace('_', ' ')}")
            continue
        if lead.publication_date and (reference - lead.publication_date) > max_age:
            drop("older than the preview age limit")
            continue

        fit, reasons = score_fit(lead, profile, cfg)
        qualified.append(
            MatchedLead(
                lead=lead,
                fit_score=fit,
                reasons=reasons,
                supplier_line=supplier_line_for(lead, profile),
            )
        )

    qualified.sort(key=lambda m: (m.fit_score, m.lead.score), reverse=True)
    selected = _diversify(qualified, wanted)

    stale_days = (today() - reference).days if reference else 0

    shortfall = ""
    if len(selected) < wanted:
        consolidated = len(qualified) - len(selected)
        consolidated_note = ""
        if consolidated > 0:
            consolidated_note = (
                f" {consolidated} further qualifying brand"
                f"{'s' if consolidated != 1 else ''} came from a company already listed, and the "
                "same applicant is never shown twice."
            )
        shortfall = (
            f"Only {len(selected)} of the {len(leads)} available opportunities genuinely suit "
            f"{prospect.company_name}.{consolidated_note} Sending a weaker example to reach "
            f"{wanted} would cost more credibility than the extra line is worth — send what is "
            "here, or wait for a fresher run."
        )

    return PreviewResult(
        prospect=prospect,
        matches=selected,
        source=source,
        considered=len(leads),
        profile_label=str(profile["label"]),
        excluded=excluded,
        shortfall_note=shortfall,
        newest_publication=reference,
        source_age_days=max(stale_days, 0),
    )


def _diversify(candidates: list[MatchedLead], wanted: int) -> list[MatchedLead]:
    """Take the best-fitting leads while avoiding obvious repetition.

    One company appears at most once. Three brands from the same applicant is
    one lead shown three times, and a supplier reading it will notice — so this
    is a hard rule, even when honouring it means returning two leads instead of
    three. A varied category is preferred but not required.

    This only ever reorders and deduplicates within the already-qualified set.
    Nothing that failed to qualify is admitted to fill a gap.
    """
    selected: list[MatchedLead] = []
    seen_companies: set[str] = set()
    seen_categories: set[str] = set()

    def company_key(match: MatchedLead) -> str:
        lead = match.lead
        return (lead.company_number or lead.display_company).strip().lower()

    # Pass 1 prefers a new category as well as a new company; pass 2 accepts a
    # repeated category. Neither pass ever repeats a company.
    for prefer_new_category in (True, False):
        for match in candidates:
            if len(selected) >= wanted:
                return selected
            if match in selected:
                continue
            company = company_key(match)
            category = match.lead.product_category
            if company in seen_companies:
                continue
            if prefer_new_category and category and category in seen_categories:
                continue
            selected.append(match)
            seen_companies.add(company)
            if category:
                seen_categories.add(category)
    return selected


def supplier_line_for(lead: Lead, profile: dict) -> str:
    """The concise 'why this may matter to you' line for the email.

    Built from the profile's own sentence plus what is actually known about the
    lead. It says what the brand looks like, never what they are buying.
    """
    base = str(profile["line"])
    if lead.product_category and lead.product_category in profile["preferred_product_groups"]:
        return f"{lead.display_category}. {base}"
    return base


__all__ = [
    "MatchedLead",
    "PreviewResult",
    "profile_for",
    "score_fit",
    "select_for_prospect",
]
