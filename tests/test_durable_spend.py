"""Search spend is recorded as it happens, and the volume history only holds real runs (D-703)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

import src.commands as commands
from src.db import init_db, session_scope
from src.db.repository import journal_already_processed, recent_run_counts
from src.db.tables import PipelineRun
from src.settings import get_settings
from tests.test_weekly_command import _args, weekly_env  # noqa: F401  (fixture)


class Killed(BaseException):
    """Stands in for SIGKILL/SIGTERM: not an Exception, so nothing catches it."""


def _rows() -> list[PipelineRun]:
    with session_scope() as session:
        return list(session.execute(select(PipelineRun)).scalars())


def test_a_killed_run_still_counts_its_spend(weekly_env, monkeypatch):  # noqa: F811
    provider = weekly_env.provider
    real_search = type(provider).search

    def search_then_die(self, query, limit=8):  # type: ignore[no-untyped-def]
        if self.calls >= 3:
            raise Killed()
        return real_search(self, query, limit=limit)

    monkeypatch.setattr(type(provider), "search", search_then_die)
    with pytest.raises(Killed):
        commands._run_one(_args(), write_db=True)

    rows = _rows()
    assert len(rows) == 1
    row = rows[0]
    assert row.status == "running" and row.mode == "weekly"
    # three answered calls plus the one that was in flight when it died
    assert row.counts["search_calls"] == 4

    with session_scope() as session:
        budget = commands.search_budget_for_run(session, get_settings())
        assert not journal_already_processed(session, "fixture", "2025-050")
        assert recent_run_counts(session) == []
    guard = commands.SearchGuard.load(get_settings())
    assert budget.max_calls == min(guard.max_calls_per_run, guard.monthly_budget - 4)


def test_a_completed_run_is_one_row_with_its_final_spend(weekly_env):  # noqa: F811
    seen: list[tuple[str, int]] = []
    real = commands._search_spend_recorder

    def spy(run_id):  # type: ignore[no-untyped-def]
        record = real(run_id)

        def wrapped(calls: int) -> None:
            record(calls)
            with session_scope() as session:
                row = session.execute(
                    select(PipelineRun).where(PipelineRun.run_id == run_id)
                ).scalar_one()
                seen.append((row.status, row.counts["search_calls"]))

        return wrapped

    commands._search_spend_recorder = spy  # type: ignore[assignment]
    try:
        result = commands._run_one(_args(), write_db=True)
    finally:
        commands._search_spend_recorder = real  # type: ignore[assignment]

    rows = _rows()
    assert len(rows) == 1 and rows[0].run_id == result.run_id
    assert rows[0].status == "completed"
    assert rows[0].counts["search_calls"] == result.counts.search_calls > 0
    # spend was durable while the run was still going
    assert seen and seen[0] == ("running", 1)
    assert [n for _, n in seen] == list(range(1, result.counts.search_calls + 1))


def test_volume_history_ignores_runs_that_are_not_comparable(weekly_env):  # noqa: F811
    init_db()
    now = datetime.now(UTC)
    rows = [
        ("weekly-ok", "weekly", "completed", 1, {"raw_records": 100}),
        ("backfill-ok", "backfill", "completed", 2, {"raw_records": 90}),
        ("weekly-running", "weekly", "running", 3, {"search_calls": 7}),
        ("weekly-blocked", "weekly", "blocked", 4, {"raw_records": 0}),
        ("rescan", "rescan", "completed", 5, {"search_calls": 3, "rescan": {}}),
        ("backtest", "backtest-ingest", "completed", 6, {"raw_records": 5000}),
    ]
    with session_scope() as session:
        for run_id, mode, status, age, counts in rows:
            session.add(
                PipelineRun(
                    run_id=run_id,
                    mode=mode,
                    status=status,
                    started_at=now - timedelta(hours=age),
                    counts=counts,
                )
            )
    with session_scope() as session:
        assert recent_run_counts(session) == [{"raw_records": 100}, {"raw_records": 90}]
