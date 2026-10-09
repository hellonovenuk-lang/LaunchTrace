"""Link leads stored before the brand tables existed to brands (``backfill-brands``).

A database upgraded to ``0002_brands`` keeps its old opportunities, but only
runs after the upgrade call ``sync_brands``; older leads have ``brand_id`` NULL,
so the rescan, the public feed and the backtest cannot see them. This rebuilds
the brand rows and their observations from what is stored, journal by journal
in publication order (so ``first_seen_*`` and stage changes come out in the
right order), through the same ``sync_brands`` the weekly run uses.

* Only opportunities with ``brand_id`` NULL are touched, so running it twice
  does nothing the second time.
* Scores, bands and every other opportunity field are read, never written:
  the only column set on ``opportunities`` is ``brand_id``.
* What a stored row does not keep (the web search's raw facts, the domain
  layer's signals, the match confidence) is not invented: those observations
  are simply absent for back-filled leads.

DECISIONS.md D-707.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.brands import journal_sort_key, sync_brands
from src.db.tables import OpportunityRow, PipelineRun
from src.logging_setup import get_logger

log = get_logger(__name__)

BACKFILL_RUN_ID = "backfill-brands"


@dataclass
class BackfillReport:
    journals: int = 0
    opportunities: int = 0
    brands: int = 0


def _run_for(session: Session, journal_number: str) -> Any:
    """The journal's latest completed run (so score events link too), else a stand-in."""
    run = (
        session.execute(
            select(PipelineRun)
            .where(
                PipelineRun.journal_number == journal_number,
                PipelineRun.status == "completed",
            )
            .order_by(PipelineRun.started_at.desc())
        )
        .scalars()
        .first()
    )
    if run is not None:
        return run
    return SimpleNamespace(
        run_id=BACKFILL_RUN_ID,
        journal_number=journal_number,
        source_name="ukipo_journal_xml",
        status="completed",
        counts={},
        csv_path=None,
    )


def backfill_brands(session: Session) -> BackfillReport:
    """Give every stored opportunity without a brand one. Idempotent."""
    from src.commands import _rehydrate_result

    report = BackfillReport()
    pending = session.execute(
        select(OpportunityRow.journal_number, OpportunityRow.dedupe_key).where(
            OpportunityRow.brand_id.is_(None)
        )
    ).all()
    by_journal: dict[str, set[str]] = {}
    for journal_number, dedupe_key in pending:
        by_journal.setdefault(journal_number, set()).add(dedupe_key)
    for journal_number in sorted(by_journal, key=journal_sort_key):
        keys = by_journal[journal_number]
        result = _rehydrate_result(session, _run_for(session, journal_number))
        result.opportunities = [o for o in result.opportunities if o.dedupe_key in keys]
        if not result.opportunities:
            continue
        with session.begin_nested():
            report.brands += sync_brands(session, result)
        report.journals += 1
        report.opportunities += len(result.opportunities)
        log.info(
            "brands.backfilled",
            journal=journal_number,
            opportunities=len(result.opportunities),
        )
    return report


def cmd_backfill_brands(args) -> int:  # type: ignore[no-untyped-def]
    from src.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        report = backfill_brands(session)
    print(
        f"Linked {report.opportunities} stored opportunities in {report.journals} journal(s) "
        f"to brands ({report.brands} brand(s) touched)."
    )
    return 0
