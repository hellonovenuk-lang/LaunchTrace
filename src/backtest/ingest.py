"""Run past journals through the normal pipeline into the persistent database.

``backtest ingest --from 2026-010 --to 2026-036`` processes each journal in the
range exactly as ``weekly`` does (``src.commands._run_one``: journals,
trademark_records, opportunities, brands, observations), with three things
switched off:

* **web search** -- a paid API, never used by a backtest (``search_provider``
  ``none``; a run that somehow made a search call is reported);
* **the LLM classifier** -- also paid; the free rule-based filter decides;
* **the live domain layer** -- by default. RDAP/DNS/homepage describe the
  present, not the week of the journal, so they are not point-in-time safe.
  ``--with-domain-layer`` turns it on if wanted for other reasons.

Idempotent: a journal already ``processed`` for the same source is skipped
unless ``--force``. A cap per invocation (config/backtest.json ->
``ingest.max_journals_per_invocation``) and a pause between downloads from
ipo.gov.uk keep it polite; the journals left over are listed so the command
can simply be run again.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.backtest.probe import journal_numbers_between
from src.db import init_db, session_scope
from src.db.repository import journal_already_processed
from src.ingest.base import get_source
from src.logging_setup import get_logger
from src.models import RunStatus
from src.settings import Settings, get_settings, load_config

log = get_logger(__name__)

PROCESSED = "processed"
SKIPPED = "skipped_already_processed"
UNAVAILABLE = "unavailable"
DEFERRED = "deferred_by_cap"


@dataclass
class IngestOutcome:
    journal_number: str
    status: str
    detail: str = ""
    raw_records: int = 0
    opportunities: int = 0
    search_calls: int = 0
    run_id: str | None = None


def backtest_settings_overrides(source: str, with_domain_layer: bool) -> dict[str, Any]:
    """Settings a backtest run uses: nothing paid, nothing that describes the present."""
    return {
        "journal_source": source,
        "search_provider": "none",
        "search_api_key": "",
        "llm_provider": "none",
        "llm_api_key": "",
        "domain_layer_enabled": bool(with_domain_layer),
    }


def run_ingest(
    first: str,
    last: str,
    *,
    source: str | None = None,
    force: bool = False,
    max_journals: int | None = None,
    with_domain_layer: bool | None = None,
    settings: Settings | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[IngestOutcome]:
    """Ingest every journal from ``first`` to ``last`` (inclusive, oldest first)."""
    from src.commands import _resolve_ref, _run_one

    cfg = load_config("backtest.json")["ingest"]
    settings = settings or get_settings()
    source_name = source or str(cfg.get("default_source", "ukipo_http"))
    domain_on = (
        bool(cfg.get("domain_layer_default", False))
        if with_domain_layer is None
        else with_domain_layer
    )
    cap = int(max_journals or cfg.get("max_journals_per_invocation", 10))
    delay = float(cfg.get("delay_seconds_between_journals", 15))
    overrides = backtest_settings_overrides(source_name, domain_on)
    run_settings = settings.model_copy(update=overrides)
    output_dir = Path(settings.cache_dir) / str(cfg.get("output_subdir", "backtest_runs"))
    args = SimpleNamespace(command="backtest-ingest", source=source_name, max_records=None)

    init_db()
    journal_source = get_source(settings=run_settings)
    outcomes: list[IngestOutcome] = []
    processed = 0
    for number in journal_numbers_between(first, last):
        ref = _resolve_ref(journal_source, number, None)
        if ref is None:
            outcomes.append(
                IngestOutcome(number, UNAVAILABLE, f"source {source_name} has no journal {number}")
            )
            continue
        if not force:
            with session_scope() as session:
                done = journal_already_processed(session, ref.source_name, ref.journal_number)
            if done:
                outcomes.append(IngestOutcome(number, SKIPPED, ref.source_name))
                continue
        if processed >= cap:
            outcomes.append(IngestOutcome(number, DEFERRED, f"cap of {cap} journals reached"))
            continue
        if processed and source_name == "ukipo_http" and delay > 0:
            sleep(delay)
        processed += 1
        log.info("backtest.ingest.journal", journal=number, source=source_name)
        result = _run_one(
            args,
            journal_number=number,
            write_db=True,
            settings_overrides=overrides,
            output_dir=output_dir,
        )
        status = PROCESSED if result.status == RunStatus.COMPLETED else result.status.value
        detail = result.blocked_reason or ""
        if result.counts.search_calls:
            # Belt and braces: the provider is 'none', so this cannot happen.
            detail = f"WARNING: {result.counts.search_calls} search calls made. {detail}"
            log.error("backtest.ingest.search_used", calls=result.counts.search_calls)
        outcomes.append(
            IngestOutcome(
                number,
                status,
                detail.strip(),
                raw_records=result.counts.raw_records,
                opportunities=len(result.opportunities),
                search_calls=result.counts.search_calls,
                run_id=result.run_id,
            )
        )
    return outcomes


def format_ingest(outcomes: list[IngestOutcome]) -> str:
    lines = [f"{'journal':<10} {'status':<26} {'records':>8} {'opps':>6}  detail"]
    for o in outcomes:
        lines.append(
            f"{o.journal_number:<10} {o.status:<26} {o.raw_records:>8} {o.opportunities:>6}  "
            f"{o.detail[:120]}"
        )
    deferred = [o.journal_number for o in outcomes if o.status == DEFERRED]
    if deferred:
        lines.append(
            f"{len(deferred)} journal(s) deferred by the per-invocation cap "
            f"({deferred[0]} .. {deferred[-1]}): run the same command again to continue."
        )
    return "\n".join(lines)
