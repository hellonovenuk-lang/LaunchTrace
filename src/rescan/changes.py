"""Change detection for the rescan: has a brand moved up (or down) a stage?

The stage model lives in ``config/rescan.json`` -> ``transitions``. Each
dimension (web presence, company status) is an ordered ladder. A rescan derives
the dimension's previous value from the brand's observations *before* it writes
new ones, compares it with what it just saw, and when they differ appends a
``stage_changes`` row:

* ``from_stage`` / ``to_stage`` are namespaced, ``web:holding_page`` ->
  ``web:live_store``, so they never collide with the weekly pipeline's own
  launch-stage transitions (``pre_launch`` -> ``early_launch``) in the same table;
* ``evidence`` holds the dimension, the old and new values, the sources and
  the dates they were observed, whether the move is ``meaningful`` (alert-worthy,
  config rules) and whether it counts as ``launched``.

Nothing is recorded when nothing changed, when either side is not a stage
(``unknown``, or never observed), or when the brand's most recent change in the
dimension is already this exact transition (a re-run cannot duplicate one).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.brands import record_stage_change
from src.db.tables import Brand, Observation, StageChange
from src.settings import load_config

CONFIG_NAME = "rescan.json"


@cache
def rescan_config() -> dict[str, Any]:
    return load_config(CONFIG_NAME)


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MeaningfulRule:
    direction: str  # up | down
    to: frozenset[str]
    require_new_high: bool = False
    negative: bool = False


@dataclass(frozen=True)
class Dimension:
    key: str
    prefix: str
    signal: str
    ladder: tuple[str, ...]
    meaningful: tuple[MeaningfulRule, ...] = ()
    launched: frozenset[str] = frozenset()
    labels: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def rank(self, value: str | None) -> int | None:
        if value is None or value not in self.ladder:
            return None
        return self.ladder.index(value)

    def is_stage(self, value: str | None) -> bool:
        return self.rank(value) is not None

    def stage_name(self, value: str) -> str:
        return f"{self.prefix}:{value}"

    def label(self, value: str | None) -> str:
        if value is None:
            return "unknown"
        return self.labels.get(value, value.replace("_", " "))


def dimensions(config: dict[str, Any] | None = None) -> dict[str, Dimension]:
    cfg = (config or rescan_config())["transitions"]
    out: dict[str, Dimension] = {}
    for key, spec in cfg.items():
        if key.startswith("_") or not isinstance(spec, dict):
            continue
        out[key] = Dimension(
            key=key,
            prefix=str(spec["prefix"]),
            signal=str(spec["signal"]),
            ladder=tuple(spec["ladder"]),
            meaningful=tuple(
                MeaningfulRule(
                    direction=str(rule["direction"]),
                    to=frozenset(rule.get("to", [])),
                    require_new_high=bool(rule.get("require_new_high", False)),
                    negative=bool(rule.get("negative", False)),
                )
                for rule in spec.get("meaningful", [])
            ),
            launched=frozenset(spec.get("launched", [])),
            labels=dict(spec.get("labels", {})),
        )
    return out


# ---------------------------------------------------------------------------
# reading a dimension's value from observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StageReading:
    """One dimension's value, with where and when it was seen."""

    value: str
    source: str
    observed_at: datetime | None = None
    detail: dict[str, Any] = field(default_factory=dict, compare=False, hash=False)


def _aware(moment: datetime | None) -> datetime | None:
    if moment is None:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _stage_of(value: Any) -> str | None:
    if isinstance(value, dict):
        stage = value.get("stage")
        return str(stage) if stage else None
    return str(value) if isinstance(value, str) and value else None


def web_stage_from(latest: dict[str, Observation]) -> StageReading | None:
    """The web-presence stage as LaunchTrace last knew it.

    The newest ``web_presence_stage`` observation, unless a newer web search
    found no website at all (``website`` observed as null): that is
    ``no_domain``, which the domain layer itself never records.
    """
    stage_obs = latest.get("web_presence_stage")
    site_obs = latest.get("website")
    floor = datetime.min.replace(tzinfo=UTC)
    if site_obs is not None and site_obs.value in (None, ""):
        site_at = _aware(site_obs.observed_at) or floor
        if stage_obs is None or site_at > (_aware(stage_obs.observed_at) or floor):
            return StageReading("no_domain", site_obs.source, _aware(site_obs.observed_at))
    if stage_obs is not None:
        stage = _stage_of(stage_obs.value)
        if stage:
            detail = stage_obs.value if isinstance(stage_obs.value, dict) else {}
            return StageReading(stage, stage_obs.source, _aware(stage_obs.observed_at), detail)
    return None


def company_stage_from(latest: dict[str, Observation]) -> StageReading | None:
    """The company stage from the last rescan's ``company_stage`` observation.

    Only the rescan writes it, so successive values come from the same lookup
    by number. (The weekly match can come from a provider that does not report
    accounts at all; comparing against that would invent transitions.)
    """
    obs = latest.get("company_stage")
    if obs is None:
        return None
    stage = _stage_of(obs.value)
    if not stage:
        return None
    detail = obs.value if isinstance(obs.value, dict) else {}
    return StageReading(stage, obs.source, _aware(obs.observed_at), detail)


READERS = {"web_presence": web_stage_from, "company_status": company_stage_from}


def derive_company_stage(
    company_status: str | None,
    accounts_category: str | None,
    config: dict[str, Any] | None = None,
) -> str:
    """dissolved / in_insolvency / dormant / no_accounts / trading, or unknown."""
    rules = (config or rescan_config())["company_stage"]

    def norm(value: str | None) -> str:
        return " ".join((value or "").lower().replace("-", " ").replace("_", " ").split())

    def norm_all(values: list[str]) -> set[str]:
        return {norm(v) for v in values}

    status = norm(company_status)
    if status in norm_all(rules["dissolved_statuses"]):
        return "dissolved"
    if status in norm_all(rules["insolvency_statuses"]):
        return "in_insolvency"
    if status not in norm_all(rules["live_statuses"]):
        return "unknown"
    accounts = norm(accounts_category)
    if any(marker in accounts for marker in norm_all(rules["dormant_accounts"]) if marker):
        return "dormant"
    if accounts in norm_all(rules["no_accounts"]):
        return "no_accounts"
    return "trading"


# ---------------------------------------------------------------------------
# detecting and recording
# ---------------------------------------------------------------------------


@dataclass
class DetectedChange:
    brand_id: int
    dimension: str
    from_value: str
    to_value: str
    meaningful: bool
    negative: bool
    launched: bool
    stage_change_id: int | None = None


def _highest_rank_seen(session: Session, brand_id: int, dim: Dimension) -> int | None:
    """Highest ladder rank this brand has ever been observed at in the dimension."""
    best: int | None = None
    for (value,) in session.execute(
        select(Observation.value).where(
            Observation.brand_id == brand_id, Observation.signal == dim.signal
        )
    ):
        rank = dim.rank(_stage_of(value))
        if rank is not None and (best is None or rank > best):
            best = rank
    return best


def classify(
    dim: Dimension, old: str, new: str, highest_before: int | None
) -> tuple[bool, bool, bool]:
    """(meaningful, negative, launched) for a move from ``old`` to ``new``."""
    old_rank, new_rank = dim.rank(old), dim.rank(new)
    assert old_rank is not None and new_rank is not None
    direction = "up" if new_rank > old_rank else "down"
    meaningful = negative = False
    for rule in dim.meaningful:
        if rule.direction != direction or new not in rule.to:
            continue
        if rule.require_new_high and highest_before is not None and new_rank <= highest_before:
            continue
        meaningful = True
        negative = negative or rule.negative
    launched = new in dim.launched and direction == "up"
    return meaningful, negative, launched


def _last_change(session: Session, brand_id: int, prefix: str) -> StageChange | None:
    rows = session.execute(
        select(StageChange)
        .where(StageChange.brand_id == brand_id, StageChange.to_stage.like(f"{prefix}:%"))
        .order_by(StageChange.detected_at.desc(), StageChange.id.desc())
        .limit(1)
    ).scalars()
    return next(iter(rows), None)


def detect_and_record(
    session: Session,
    brand: Brand,
    dimension: Dimension,
    previous: StageReading | None,
    current: StageReading | None,
    *,
    run_id: str | None = None,
    highest_before: int | None = None,
) -> DetectedChange | None:
    """Record a stage change if the dimension moved; return it, or None.

    ``highest_before`` is the highest rank observed before this rescan's
    observations were written (computed by the caller, see
    :func:`highest_rank_before`).
    """
    if previous is None or current is None:
        return None
    old, new = previous.value, current.value
    if old == new or not dimension.is_stage(old) or not dimension.is_stage(new):
        return None
    from_stage, to_stage = dimension.stage_name(old), dimension.stage_name(new)
    last = _last_change(session, brand.id, dimension.prefix)
    if last is not None and last.from_stage == from_stage and last.to_stage == to_stage:
        return None  # already recorded; a re-run must not duplicate it
    meaningful, negative, launched = classify(dimension, old, new, highest_before)
    now = datetime.now(UTC)
    evidence = {
        "detected_by": "rescan",
        "dimension": dimension.key,
        "signal": dimension.signal,
        "old_value": old,
        "new_value": new,
        "old_source": previous.source,
        "new_source": current.source,
        "old_observed_at": previous.observed_at.isoformat() if previous.observed_at else None,
        "new_observed_at": (current.observed_at or now).isoformat(),
        "old_detail": _small(previous.detail),
        "new_detail": _small(current.detail),
        "meaningful": meaningful,
        "negative": negative,
        "launched": launched,
    }
    row = record_stage_change(session, brand.id, from_stage, to_stage, evidence, run_id=run_id)
    if launched and brand.launched_at is None:
        brand.launched_at = now
    return DetectedChange(
        brand_id=brand.id,
        dimension=dimension.key,
        from_value=old,
        to_value=new,
        meaningful=meaningful,
        negative=negative,
        launched=launched,
        stage_change_id=row.id,
    )


def highest_rank_before(session: Session, brand_id: int, dimension: Dimension) -> int | None:
    return _highest_rank_seen(session, brand_id, dimension)


def _small(detail: dict[str, Any]) -> dict[str, Any]:
    """Only scalar facts go into evidence, never page text."""
    return {
        k: v
        for k, v in (detail or {}).items()
        if isinstance(v, (str, int, float, bool)) or v is None
    }


__all__ = [
    "Dimension",
    "DetectedChange",
    "StageReading",
    "READERS",
    "classify",
    "company_stage_from",
    "derive_company_stage",
    "detect_and_record",
    "dimensions",
    "highest_rank_before",
    "rescan_config",
    "web_stage_from",
]
