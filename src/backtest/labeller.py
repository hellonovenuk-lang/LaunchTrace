"""Outcome labels: had a brand launched N months after its trade mark filing?

For each brand and each horizon in config/backtest.json -> ``outcomes``:

* the **anchor** is the brand's first filing date (``brands.first_filing_date``,
  else the earliest filing date of its opportunities);
* the **horizon end** is ``anchor + horizon_months``;
* ``unknown`` if the horizon has not elapsed on the as-of date;
* ``launched`` if ANY available criterion has positive evidence: a matching
  observation *observed* on or before the horizon end;
* ``not_launched`` only if no criterion is positive AND some criterion has a
  negative check observed from the horizon end up to
  ``negative_check_tolerance_days`` after it (a check made then says the brand
  had not launched by the horizon, assuming a launch is not undone);
* otherwise ``unknown`` -- absence of evidence is never ``not_launched``.

Only observations already in the database are read. The labeller never runs a
web search (a paid API), a homepage fetch or any other lookup; labels mature
as the weekly run and the rescan job add observations over time.

Rows go to ``outcomes`` (one per brand, horizon and labeller version; a re-run
replaces them).
"""

from __future__ import annotations

import calendar
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from src.db.tables import Brand, Observation, OpportunityRow, Outcome
from src.logging_setup import get_logger
from src.settings import load_config

log = get_logger(__name__)

LAUNCHED = "launched"
NOT_LAUNCHED = "not_launched"
UNKNOWN = "unknown"


def add_months(day: date, months: int) -> date:
    """``day`` plus ``months`` calendar months, clipped to the end of the month."""
    month_index = day.month - 1 + months
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _observed_on(row: Observation) -> date:
    moment = row.observed_at
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).date()


def matches(spec: dict[str, Any], row: Observation) -> bool:
    """Does an observation satisfy one positive/negative spec of a criterion?"""
    if row.signal != spec.get("signal"):
        return False
    value = row.value
    if "field" in spec:
        value = value.get(spec["field"]) if isinstance(value, dict) else None
    if "equals" in spec:
        return bool(value == spec["equals"]) and value is not None
    if "in" in spec:
        return value in spec["in"]
    return False


@dataclass
class Label:
    horizon_months: int
    label: str
    horizon_end: date | None
    criteria_met: dict[str, str] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ""


def _evidence(criterion: str, polarity: str, row: Observation) -> dict[str, Any]:
    return {
        "criterion": criterion,
        "polarity": polarity,
        "observation_id": row.id,
        "signal": row.signal,
        "value": row.value,
        "observed_at": row.observed_at.isoformat() if row.observed_at else None,
    }


def label_one(
    anchor: date | None,
    horizon_months: int,
    observations: list[Observation],
    as_of: date,
    cfg: dict[str, Any],
) -> Label:
    """The label for one brand at one horizon. Pure: reads only what it is given."""
    criteria = [c for c in cfg.get("criteria", []) if isinstance(c, dict)]
    if anchor is None:
        return Label(
            horizon_months,
            UNKNOWN,
            None,
            {c["key"]: "not_checked" for c in criteria},
            reason="no filing date",
        )
    end = add_months(anchor, horizon_months)
    result = Label(horizon_months, UNKNOWN, end)
    for c in criteria:
        if not c.get("available", True):
            result.criteria_met[c["key"]] = "unavailable"
    if as_of < end:
        for c in criteria:
            result.criteria_met.setdefault(c["key"], "not_checked")
        result.reason = f"horizon not elapsed (ends {end.isoformat()})"
        return result

    tolerance = timedelta(days=int(cfg.get("negative_check_tolerance_days", 30)))
    positive_found = negative_found = False
    for c in criteria:
        key = c["key"]
        if not c.get("available", True):
            continue
        status = "no_evidence"
        for row in observations:
            seen = _observed_on(row)
            if seen > as_of:
                continue
            if seen <= end and any(matches(s, row) for s in c.get("positive", [])):
                status = "positive"
                result.evidence.append(_evidence(key, "positive", row))
        if status != "positive":
            for row in observations:
                seen = _observed_on(row)
                if seen > as_of or not (end <= seen <= end + tolerance):
                    continue
                if any(matches(s, row) for s in c.get("negative", [])):
                    status = "negative"
                    result.evidence.append(_evidence(key, "negative", row))
        result.criteria_met[key] = status
        positive_found |= status == "positive"
        negative_found |= status == "negative"

    if positive_found:
        result.label = LAUNCHED
        result.reason = "positive evidence on or before the horizon end"
    elif negative_found:
        result.label = NOT_LAUNCHED
        result.reason = "a check at the horizon end found no launch, and nothing positive"
    else:
        result.reason = "no evidence"
    return result


def anchor_date(session: Session, brand: Brand) -> date | None:
    if brand.first_filing_date is not None:
        return brand.first_filing_date
    earliest: date | None = session.execute(
        select(func.min(OpportunityRow.filing_date)).where(OpportunityRow.brand_id == brand.id)
    ).scalar_one_or_none()
    return earliest


@dataclass
class LabelSummary:
    as_of: date
    labeller_version: str
    brands: int = 0
    counts: dict[int, Counter[str]] = field(default_factory=dict)
    reasons: dict[int, Counter[str]] = field(default_factory=dict)


def label_brands(
    session: Session, as_of: date | None = None, config: dict[str, Any] | None = None
) -> LabelSummary:
    """Label every brand at every configured horizon, replacing earlier rows."""
    cfg = config if config is not None else load_config("backtest.json")["outcomes"]
    as_of = as_of or date.today()
    version = str(cfg.get("labeller_version", "1"))
    horizons = [int(h) for h in cfg.get("horizons_months", [3, 6])]
    summary = LabelSummary(as_of=as_of, labeller_version=version)
    summary.counts = {h: Counter() for h in horizons}
    summary.reasons = {h: Counter() for h in horizons}
    now = datetime.now(UTC)

    for brand in session.execute(select(Brand).order_by(Brand.id)).scalars():
        summary.brands += 1
        anchor = anchor_date(session, brand)
        observations = list(
            session.execute(
                select(Observation)
                .where(Observation.brand_id == brand.id)
                .order_by(Observation.observed_at, Observation.id)
            ).scalars()
        )
        for horizon in horizons:
            lab = label_one(anchor, horizon, observations, as_of, cfg)
            summary.counts[horizon][lab.label] += 1
            if lab.label == UNKNOWN:
                summary.reasons[horizon][lab.reason.split(" (")[0]] += 1
            row = session.execute(
                select(Outcome).where(
                    Outcome.brand_id == brand.id,
                    Outcome.horizon_months == horizon,
                    Outcome.labeller_version == version,
                )
            ).scalar_one_or_none()
            if row is None:
                row = Outcome(brand_id=brand.id, horizon_months=horizon, labeller_version=version)
                session.add(row)
            row.label = lab.label
            row.criteria_met = {**lab.criteria_met, "_reason": lab.reason}
            row.evidence = lab.evidence
            row.labelled_at = now
            row.as_of_date = as_of
    session.flush()
    log.info(
        "backtest.labelled",
        brands=summary.brands,
        as_of=as_of.isoformat(),
        counts={h: dict(c) for h, c in summary.counts.items()},
    )
    return summary


def format_labels(summary: LabelSummary) -> str:
    lines = [
        f"Labelled {summary.brands} brand(s) as of {summary.as_of.isoformat()} "
        f"(labeller v{summary.labeller_version})."
    ]
    for horizon, counts in summary.counts.items():
        lines.append(
            f"  +{horizon} months: launched {counts.get(LAUNCHED, 0)}, "
            f"not launched {counts.get(NOT_LAUNCHED, 0)}, unknown {counts.get(UNKNOWN, 0)}"
            + (
                " (" + ", ".join(f"{k}: {v}" for k, v in summary.reasons[horizon].items()) + ")"
                if summary.reasons[horizon]
                else ""
            )
        )
    return "\n".join(lines)
