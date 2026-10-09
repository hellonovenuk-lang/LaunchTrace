"""``backfill-brands``: stored leads without a brand get one; nothing else moves (D-707)."""

from __future__ import annotations

from sqlalchemy import delete, func, select, update

import src.commands as commands
from src.brands_backfill import backfill_brands
from src.db import session_scope
from src.db.tables import Brand, Observation, OpportunityRow, ScoreEvent, StageChange
from tests.test_weekly_command import _args, weekly_env  # noqa: F401  (fixture)


def _scores() -> dict[str, tuple]:
    with session_scope() as session:
        return {
            r.dedupe_key: (r.launchtrace_score, r.score_band, r.suppressed, r.score_reasons)
            for r in session.execute(select(OpportunityRow)).scalars()
        }


def _count(model) -> int:  # type: ignore[no-untyped-def]
    with session_scope() as session:
        return session.execute(select(func.count()).select_from(model)).scalar_one()


def _forget_brands() -> None:
    """The state of a database whose leads predate the brand tables."""
    with session_scope() as session:
        session.execute(update(OpportunityRow).values(brand_id=None))
        session.execute(update(ScoreEvent).values(brand_id=None))
        session.execute(delete(Observation))
        session.execute(delete(StageChange))
        session.execute(delete(Brand))


def test_backfill_links_every_stored_lead_without_moving_a_score(weekly_env):  # noqa: F811
    commands._run_one(_args(), write_db=True)
    brands_before = _count(Brand)
    assert brands_before > 0
    _forget_brands()
    scores = _scores()

    with session_scope() as session:
        report = backfill_brands(session)

    assert report.opportunities == len(scores) and report.journals == 1
    assert _count(Brand) == brands_before
    assert _count(Observation) > 0
    with session_scope() as session:
        assert not session.execute(
            select(OpportunityRow).where(OpportunityRow.brand_id.is_(None))
        ).first()
    assert _scores() == scores


def test_backfill_is_idempotent(weekly_env):  # noqa: F811
    commands._run_one(_args(), write_db=True)
    _forget_brands()
    with session_scope() as session:
        backfill_brands(session)
    counts = (_count(Brand), _count(Observation), _count(StageChange))
    with session_scope() as session:
        again = backfill_brands(session)
    assert again.opportunities == 0 and again.journals == 0
    assert (_count(Brand), _count(Observation), _count(StageChange)) == counts


def test_an_already_linked_database_is_left_alone(weekly_env):  # noqa: F811
    commands._run_one(_args(), write_db=True)
    counts = (_count(Brand), _count(Observation))
    with session_scope() as session:
        assert backfill_brands(session).opportunities == 0
    assert (_count(Brand), _count(Observation)) == counts


def test_cli(weekly_env, capsys):  # noqa: F811
    from src.pipeline import main

    commands._run_one(_args(), write_db=True)
    _forget_brands()
    assert main(["backfill-brands"]) == 0
    assert "Linked" in capsys.readouterr().out
    with session_scope() as session:
        assert not session.execute(
            select(OpportunityRow).where(OpportunityRow.brand_id.is_(None))
        ).first()
