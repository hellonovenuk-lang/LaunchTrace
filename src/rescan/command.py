"""CLI: ``python -m src.pipeline rescan`` and ``python -m src.pipeline movers-digest``.

``rescan`` re-checks recently seen brands, records what moved, then prepares
this week's "brands that moved" digest (``--no-digest`` skips it; ``--dry-run``
lists what would be checked and makes no external call and no write). It always
ends by pruning old download caches, like ``weekly``. ``movers-digest`` prepares
the digest on its own. Both exit 0 unless the database itself is unusable: a
failed check is counted, not fatal.
"""

from __future__ import annotations

import json
from typing import Any

from src.logging_setup import get_logger

log = get_logger(__name__)


def _print_digest(summary: Any) -> None:
    if summary.disabled:
        print("Movers digest: disabled in config/rescan.json")
        return
    mode = "review (outbox only)" if summary.review_only else "send"
    print(
        f"Movers digest {summary.week} [{mode}]: recipients {summary.recipients}, "
        f"sent {summary.sent}, written to outbox {summary.rendered_not_sent}, "
        f"already done {summary.skipped_already_sent}, nothing to report "
        f"{summary.skipped_empty}, suppressed {summary.skipped_suppressed}, "
        f"failed {summary.failed}"
    )
    for detail in summary.details:
        print(f"  {detail}")


def _digest(settings: Any) -> Any:
    from src.db import session_scope
    from src.rescan.digest import send_movers_digests

    with session_scope() as session:
        return send_movers_digests(session, settings)


def cmd_rescan(args: Any) -> int:
    from src.commands import prune_caches
    from src.db import init_db, session_scope
    from src.rescan.job import build_job
    from src.settings import get_settings

    settings = get_settings()
    dry_run = bool(getattr(args, "dry_run", False))
    try:
        init_db()
        job = build_job(settings)
        with session_scope() as session:
            summary = job.run(
                session,
                dry_run=dry_run,
                limit=getattr(args, "limit", None),
                commit=not dry_run,
            )
        if getattr(args, "json", False):
            print(json.dumps({**summary.as_dict(), "plan": summary.plan}, indent=2, default=str))
        else:
            print(
                f"Rescan {summary.run_id}{' (dry run)' if dry_run else ''}: "
                f"eligible {summary.eligible}, selected {summary.selected}, "
                f"checked {summary.checked}, domain probes {summary.domain_probes}, "
                f"Companies House lookups {summary.ch_lookups} "
                f"(API calls {summary.ch_api_calls}), search calls {summary.search_calls}, "
                f"stage changes {summary.stage_changes} "
                f"(meaningful {summary.meaningful_changes}, launched {summary.launched}), "
                f"errors {summary.errors}"
            )
            for item in summary.plan:
                print(
                    f"  {item['brand_uid']}  {item['brand_name'][:40]:40}  {','.join(item['checks'])}"
                )
            for detail in summary.error_details[:20]:
                print(f"  error: {detail}")
        if not dry_run and not getattr(args, "no_digest", False):
            _print_digest(_digest(settings))
        return 0
    finally:
        prune_caches(settings)


def cmd_movers_digest(args: Any) -> int:
    from src.db import init_db
    from src.settings import get_settings

    init_db()
    summary = _digest(get_settings())
    if getattr(args, "json", False):
        print(json.dumps(summary.as_dict(), indent=2))
    else:
        _print_digest(summary)
    return 0


__all__ = ["cmd_movers_digest", "cmd_rescan"]
