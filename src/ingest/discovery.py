"""Journal identity and scheduling maths.

The UKIPO Trade Marks Journal is published every Friday.  Journals are
identified as ``YYYY-NNN`` where ``NNN`` is the ordinal Friday of that calendar
year -- the same shape used by the IPO's own journal directory URLs
(``.../tm-journals/2025-052/``).
"""

from __future__ import annotations

from datetime import date, timedelta

FRIDAY = 4  # date.weekday(): Monday=0


def is_publication_day(day: date) -> bool:
    return day.weekday() == FRIDAY


def most_recent_friday(reference: date | None = None) -> date:
    """The most recent Friday on or before ``reference``."""
    reference = reference or date.today()
    delta = (reference.weekday() - FRIDAY) % 7
    return reference - timedelta(days=delta)


def friday_ordinal(day: date) -> int:
    """Which Friday of its calendar year ``day`` is (1-based)."""
    if day.weekday() != FRIDAY:
        raise ValueError(f"{day} is not a Friday")
    jan1 = date(day.year, 1, 1)
    first_friday = jan1 + timedelta(days=(FRIDAY - jan1.weekday()) % 7)
    return ((day - first_friday).days // 7) + 1


def journal_number_for_date(day: date) -> str:
    """``2018-01-26`` -> ``2018-004``."""
    friday = day if day.weekday() == FRIDAY else most_recent_friday(day)
    return f"{friday.year}-{friday_ordinal(friday):03d}"


def date_for_journal_number(journal_number: str) -> date:
    """``2018-004`` -> ``2018-01-26``."""
    try:
        year_s, ordinal_s = journal_number.split("-", 1)
        year, ordinal = int(year_s), int(ordinal_s)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ValueError(f"Unrecognised journal number: {journal_number!r}") from exc
    jan1 = date(year, 1, 1)
    first_friday = jan1 + timedelta(days=(FRIDAY - jan1.weekday()) % 7)
    return first_friday + timedelta(weeks=ordinal - 1)


def latest_expected_journal(reference: date | None = None, min_age_hours: int = 0) -> date:
    """The publication date of the journal we would expect to be available.

    ``min_age_hours`` lets a scheduled job avoid asking for a journal that was
    only notionally published minutes ago.
    """
    reference = reference or date.today()
    friday = most_recent_friday(reference)
    if min_age_hours and reference == friday:
        # Same-day: fall back a week rather than chase a journal mid-publication.
        friday = friday - timedelta(days=7)
    return friday


def previous_journal_dates(weeks: int, reference: date | None = None) -> list[date]:
    """The ``weeks`` most recent complete journal publication dates, oldest first."""
    latest = latest_expected_journal(reference)
    return [latest - timedelta(weeks=i) for i in range(weeks - 1, -1, -1)]
