"""Customer feedback on delivered leads.

The point is to find out whether the feed is actually useful, in the customer's
own words, before anyone starts tuning weights on a hunch.

**Feedback never changes scoring automatically.** It is recorded as evidence.
Wiring customer opinion straight into the weights would make the score
unexplainable, untestable, and impossible to defend to the next customer who
asks why a brand scored 78. When there is enough of it, a human changes
``config/scoring.json`` deliberately and writes down why.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.db.tables import Customer, LeadFeedback, OpportunityRow
from src.logging_setup import get_logger

log = get_logger(__name__)


class FeedbackState(str, Enum):
    USEFUL = "USEFUL"
    NOT_RELEVANT = "NOT_RELEVANT"
    ALREADY_KNOWN = "ALREADY_KNOWN"
    TOO_ESTABLISHED = "TOO_ESTABLISHED"
    TOO_EARLY = "TOO_EARLY"
    CONTACTED = "CONTACTED"
    CONVERTED = "CONVERTED"

    @property
    def is_positive(self) -> bool:
        return self in {FeedbackState.USEFUL, FeedbackState.CONTACTED, FeedbackState.CONVERTED}


# What each state, seen repeatedly, is actually telling you. This is the whole
# reason for collecting it, so it lives next to the states themselves.
STATE_MEANING = {
    FeedbackState.USEFUL: "The feed is doing its job. Keep going.",
    FeedbackState.NOT_RELEVANT: (
        "Wrong categories for this customer. Look at their supplier profile before "
        "changing anything about the signal."
    ),
    FeedbackState.ALREADY_KNOWN: (
        "The signal is real but not early enough to be news. This is the most serious "
        "pattern: it means the product has no timing advantage for this customer."
    ),
    FeedbackState.TOO_ESTABLISHED: (
        "Maturity detection is letting through brands that are already trading. Web "
        "enrichment is the thing that fixes this."
    ),
    FeedbackState.TOO_EARLY: (
        "The brand exists only on paper. Consider whether this customer needs a later "
        "stage than the feed currently favours."
    ),
    FeedbackState.CONTACTED: "The customer acted on it. That is the metric that matters most.",
    FeedbackState.CONVERTED: "The customer won business from it. Record exactly what happened.",
}


@dataclass
class FeedbackSummary:
    total: int = 0
    by_state: dict[str, int] = field(default_factory=dict)
    by_customer: dict[str, dict[str, int]] = field(default_factory=dict)
    by_category: dict[str, dict[str, int]] = field(default_factory=dict)
    notes: list[tuple[str, str]] = field(default_factory=list)

    @property
    def positive(self) -> int:
        return sum(
            count for state, count in self.by_state.items() if FeedbackState(state).is_positive
        )

    @property
    def usefulness_rate(self) -> float | None:
        """Share of rated leads the customer called useful or acted on."""
        return round(self.positive / self.total, 3) if self.total else None

    def patterns(self) -> list[str]:
        """Recurring signals worth acting on, most common first.

        Only reports a state seen at least twice: one person's opinion about
        one lead is not a pattern, and treating it as one is how a product gets
        tuned into noise.
        """
        lines: list[str] = []
        for state, count in sorted(self.by_state.items(), key=lambda kv: -kv[1]):
            if count < 2:
                continue
            share = count / self.total if self.total else 0
            lines.append(f"{state} ×{count} ({share:.0%}) — {STATE_MEANING[FeedbackState(state)]}")
        return lines


def record_feedback(
    session: Session,
    state: FeedbackState | str,
    trademark_number: str | None = None,
    dedupe_key: str | None = None,
    customer_id: int | None = None,
    note: str | None = None,
    source: str = "operator",
) -> LeadFeedback:
    """Record one piece of feedback against one opportunity.

    Fills in brand and journal from the stored opportunity where it can, so a
    summary stays readable even after the CSV has been thrown away.
    """
    resolved = FeedbackState(state) if isinstance(state, str) else state

    row: OpportunityRow | None = None
    if dedupe_key:
        row = (
            session.execute(select(OpportunityRow).where(OpportunityRow.dedupe_key == dedupe_key))
            .scalars()
            .first()
        )
    elif trademark_number:
        row = (
            session.execute(
                select(OpportunityRow).where(OpportunityRow.trademark_number == trademark_number)
            )
            .scalars()
            .first()
        )

    feedback = LeadFeedback(
        customer_id=customer_id,
        dedupe_key=dedupe_key or (row.dedupe_key if row else None),
        trademark_number=trademark_number or (row.trademark_number if row else None),
        brand_name=row.brand_name if row else None,
        journal_number=row.journal_number if row else None,
        state=resolved.value,
        note=note,
        source=source,
    )
    session.add(feedback)
    session.flush()
    log.info(
        "feedback.recorded",
        state=resolved.value,
        customer_id=customer_id,
        trademark=feedback.trademark_number,
    )
    return feedback


def import_feedback_csv(session: Session, path: str | Path) -> tuple[int, list[str]]:
    """Bulk-import feedback a customer sent back as a spreadsheet.

    Expected columns: ``trademark_number`` (or ``dedupe_key``), ``state``, and
    optionally ``note`` and ``customer_id``. Unknown states are reported rather
    than guessed at.
    """
    import csv

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"No feedback CSV at {path}")

    imported = 0
    problems: list[str] = []
    with path.open(encoding="utf-8-sig", newline="") as fh:
        for line_number, row in enumerate(csv.DictReader(fh), start=2):
            raw_state = (row.get("state") or "").strip().upper()
            if not raw_state:
                continue
            try:
                state = FeedbackState(raw_state)
            except ValueError:
                problems.append(
                    f"line {line_number}: unknown state {raw_state!r}. "
                    f"Use one of: {', '.join(s.value for s in FeedbackState)}"
                )
                continue
            customer_raw = (row.get("customer_id") or "").strip()
            record_feedback(
                session,
                state=state,
                trademark_number=(row.get("trademark_number") or "").strip() or None,
                dedupe_key=(row.get("dedupe_key") or "").strip() or None,
                customer_id=int(customer_raw) if customer_raw.isdigit() else None,
                note=(row.get("note") or "").strip() or None,
                source="csv",
            )
            imported += 1
    return imported, problems


def summarise_feedback(session: Session, limit_notes: int = 12) -> FeedbackSummary:
    """Aggregate what customers have said, for the operator report."""
    rows = list(session.execute(select(LeadFeedback)).scalars())
    summary = FeedbackSummary(total=len(rows))
    if not rows:
        return summary

    summary.by_state = dict(Counter(row.state for row in rows))

    companies = {
        customer.id: customer.company for customer in session.execute(select(Customer)).scalars()
    }
    per_customer: dict[str, Counter] = {}
    for row in rows:
        name = companies.get(row.customer_id or -1, "unattributed")
        per_customer.setdefault(name, Counter())[row.state] += 1
    summary.by_customer = {name: dict(counter) for name, counter in per_customer.items()}

    categories = {
        opportunity.dedupe_key: opportunity.product_category or "uncategorised"
        for opportunity in session.execute(select(OpportunityRow)).scalars()
    }
    per_category: dict[str, Counter] = {}
    for row in rows:
        category = categories.get(row.dedupe_key or "", "unknown")
        per_category.setdefault(category, Counter())[row.state] += 1
    summary.by_category = {name: dict(counter) for name, counter in per_category.items()}

    summary.notes = [
        (row.brand_name or row.trademark_number or "—", row.note or "")
        for row in sorted(rows, key=lambda r: r.created_at, reverse=True)
        if row.note
    ][:limit_notes]

    return summary


__all__ = [
    "FeedbackState",
    "FeedbackSummary",
    "STATE_MEANING",
    "import_feedback_csv",
    "record_feedback",
    "summarise_feedback",
]
