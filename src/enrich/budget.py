"""Search-call accounting and the cost guard.

Every call to a search provider goes through a ``SearchBudget``. It counts the
calls (``FunnelCounts.search_calls``, persisted with the run) and refuses to
start a record's searches once the allowance for the run is spent, so a run can
never cost more than ``config/costs.json`` -> ``search_guard`` allows.

The allowance for a run is the smaller of the per-run cap and what is left of
the rolling monthly budget. Running out is never a failure: the records that
were not searched are left ``attempted=False`` (exactly as when no search
provider is configured), a warning goes on the run, and the run completes.

What is counted is every *billed HTTP attempt*, retries included (D-704):
an HTTP provider asks ``take_attempt`` before each try, and a retry the budget
cannot cover is not made. ``on_call`` (optional) is told the running total
after every counted call, so the caller can persist spend as it happens and a
run killed half-way still counts against the rolling budget (D-703).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from src.logging_setup import get_logger
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)

PER_RUN_CAP = "per_run_cap"
MONTHLY_BUDGET = "monthly_budget"


@dataclass(frozen=True)
class SearchGuard:
    """The cost-guard rules: per-run cap, rolling budget and its window."""

    max_calls_per_run: int
    monthly_budget: int
    window_days: int

    @classmethod
    def load(cls, settings: Settings | None = None) -> SearchGuard:
        cfg = load_config("costs.json")["search_guard"]
        settings = settings or get_settings()
        per_run = int(cfg["max_calls_per_run"])
        if settings.search_max_calls_per_run is not None:
            per_run = int(settings.search_max_calls_per_run)
        return cls(
            max_calls_per_run=max(per_run, 0),
            monthly_budget=max(int(cfg["monthly_budget"]), 0),
            window_days=max(int(cfg["window_days"]), 1),
        )

    def window_start(self, now: datetime | None = None) -> datetime:
        return (now or datetime.now(UTC)) - timedelta(days=self.window_days)

    def allowance(self, used_in_window: int) -> tuple[int, str]:
        """Calls this run may make, and which limit sets that number."""
        monthly_left = max(self.monthly_budget - max(used_in_window, 0), 0)
        if monthly_left < self.max_calls_per_run:
            return monthly_left, MONTHLY_BUDGET
        return self.max_calls_per_run, PER_RUN_CAP


class SearchBudget:
    """Counts search calls for one run and stops them at the allowance.

    ``max_calls=None`` means unlimited (still counted).
    """

    def __init__(
        self,
        max_calls: int | None = None,
        limited_by: str = PER_RUN_CAP,
        on_call: Callable[[int], None] | None = None,
    ) -> None:
        self.max_calls = max_calls
        self.limited_by = limited_by
        self.calls = 0
        self.records_skipped = 0
        self.exhausted = False
        self.on_call = on_call

    @property
    def remaining(self) -> int | None:
        if self.max_calls is None:
            return None
        return max(self.max_calls - self.calls, 0)

    def reserve(self, needed: int) -> bool:
        """May a record that needs ``needed`` calls start? Once refused, always refused.

        A record is searched completely or not at all, so a record's evidence
        never depends on where in the run the money ran out, and once one record
        has been refused every later one is too: the cut-off is a single point
        in the run, not a scatter of whichever records happened to fit.
        """
        if self.exhausted:
            self.records_skipped += 1
            return False
        remaining = self.remaining
        if remaining is not None and needed > remaining:
            self.exhausted = True
            self.records_skipped += 1
            return False
        return True

    def count(self, n: int = 1) -> None:
        self.calls += n
        self._notify()

    def take_attempt(self) -> bool:
        """Count one billed HTTP attempt, or refuse it when nothing is left.

        Called before every attempt, retries included, so ``calls`` never
        exceeds ``max_calls``. A refusal also stops later records.
        """
        if self.max_calls is not None and self.calls >= self.max_calls:
            self.exhausted = True
            return False
        self.count()
        return True

    def _notify(self) -> None:
        if self.on_call is None:
            return
        try:
            self.on_call(self.calls)
        except Exception as exc:  # recording spend must never break a run
            log.warning("budget.on_call_failed", error=str(exc)[:200])

    def warning(self) -> str | None:
        if not self.exhausted:
            return None
        which = (
            "the rolling monthly search budget"
            if self.limited_by == MONTHLY_BUDGET
            else "the per-run search cap"
        )
        return (
            f"Search budget reached ({which}: {self.calls} of {self.max_calls} calls used); "
            f"{self.records_skipped} candidate(s) were not web-enriched this run."
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "max_calls": self.max_calls,
            "limited_by": self.limited_by,
            "calls": self.calls,
            "records_skipped": self.records_skipped,
            "exhausted": self.exhausted,
        }
