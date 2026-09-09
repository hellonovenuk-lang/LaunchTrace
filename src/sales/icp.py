"""ICP scoring: which suppliers are most likely to value LaunchTrace.

Deliberately a keyword model over text an operator wrote, not a classifier.
That makes every score explainable in one line, and makes disagreeing with it a
matter of editing ``config/icp_scoring.json`` rather than retraining anything.

It is a prioritisation aid. It does not predict whether anyone will buy.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.sales.models import Priority, Prospect, ProspectStatus
from src.settings import load_config


@dataclass
class IcpSignal:
    key: str
    label: str
    weight: int
    matched: str = ""

    @property
    def positive(self) -> bool:
        return self.weight >= 0


@dataclass
class IcpResult:
    """A score, the band it lands in, and every reason for both."""

    score: int
    priority: Priority
    signals: list[IcpSignal] = field(default_factory=list)
    hard_suppress_reason: str | None = None

    @property
    def positives(self) -> list[IcpSignal]:
        return [s for s in self.signals if s.positive]

    @property
    def negatives(self) -> list[IcpSignal]:
        return [s for s in self.signals if not s.positive]

    def explain(self) -> list[str]:
        lines: list[str] = []
        if self.hard_suppress_reason:
            lines.append(f"SUPPRESSED: {self.hard_suppress_reason}")
        for signal in sorted(self.signals, key=lambda s: -abs(s.weight)):
            sign = "+" if signal.positive else ""
            detail = f" (matched “{signal.matched}”)" if signal.matched else ""
            lines.append(f"{sign}{signal.weight:>3}  {signal.label}{detail}")
        return lines


def _first_match(text: str, needles: list[str]) -> str | None:
    for needle in needles:
        if needle.lower() in text:
            return needle
    return None


def _hard_suppress_reason(prospect: Prospect, config: dict) -> str | None:
    rules = config.get("hard_suppress", {})
    if not prospect.company_name.strip():
        return str(rules.get("no_company_name", "no company name"))
    if prospect.opted_out:
        return str(rules.get("opted_out", "opted out"))
    if prospect.status == ProspectStatus.SUPPRESSED:
        return str(rules.get("status_suppressed", "suppressed"))
    haystack = f"{prospect.notes} {prospect.icp_reason} {prospect.company_size_hint}".lower()
    marker = _first_match(haystack, config.get("not_trading_markers", []))
    if marker:
        return f"{rules.get('not_trading', 'not trading')} ({marker})"
    return None


def score_prospect(prospect: Prospect, config: dict | None = None) -> IcpResult:
    """Score one prospect. Never mutates it — the caller decides what to store."""
    cfg = config or load_config("icp_scoring.json")

    suppress_reason = _hard_suppress_reason(prospect, cfg)
    if suppress_reason:
        return IcpResult(
            score=0,
            priority=Priority.SUPPRESS,
            signals=[],
            hard_suppress_reason=suppress_reason,
        )

    signals: list[IcpSignal] = []

    category_weight = int(cfg["category_weights"].get(prospect.supplier_category, 0))
    if category_weight:
        signals.append(
            IcpSignal(
                key="supplier_category",
                label=(
                    f"Sells {prospect.supplier_category.replace('_', ' ')}, "
                    "which an emerging brand buys early"
                ),
                weight=category_weight,
            )
        )

    for group, sign in (("positive_signals", 1), ("negative_signals", -1)):
        for rule in cfg.get(group, []):
            text = prospect.searchable_text(rule["fields"])
            matched = _first_match(text, rule["any_of"])
            if matched:
                signals.append(
                    IcpSignal(
                        key=rule["key"],
                        label=rule["label"],
                        weight=int(rule["weight"]),
                        matched=matched,
                    )
                )
        del sign  # weights already carry their own sign

    total = sum(s.weight for s in signals)
    score = max(0, min(100, total))
    bands = cfg["bands"]
    if score >= int(bands["A"]):
        priority = Priority.A
    elif score >= int(bands["B"]):
        priority = Priority.B
    elif score >= int(bands["C"]):
        priority = Priority.C
    else:
        priority = Priority.SUPPRESS

    return IcpResult(score=score, priority=priority, signals=signals)


def apply_scores(prospects: list[Prospect], config: dict | None = None) -> dict[str, IcpResult]:
    """Score every prospect and write the result onto each row.

    A prospect already marked OPTED_OUT or SUPPRESSED keeps that status: scoring
    reprioritises, it never reopens anyone.
    """
    cfg = config or load_config("icp_scoring.json")
    results: dict[str, IcpResult] = {}
    for prospect in prospects:
        result = score_prospect(prospect, cfg)
        prospect.icp_score = result.score
        prospect.priority = result.priority
        if (
            result.priority == Priority.SUPPRESS
            and prospect.status.contactable
            and not prospect.suppression_reason
        ):
            prospect.suppression_reason = (
                result.hard_suppress_reason or "ICP score below the contact threshold"
            )
        results[prospect.prospect_id] = result
    return results


__all__ = ["IcpResult", "IcpSignal", "apply_scores", "score_prospect"]
