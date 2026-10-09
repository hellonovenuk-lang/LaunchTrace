"""``python -m src.pipeline backtest {probe,ingest,label,report,run}``."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

from src.settings import REPO_ROOT


def add_backtest_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    backtest = sub.add_parser(
        "backtest",
        help="Backtest the score on past journals: probe, ingest, label, report, run",
    )
    actions = backtest.add_subparsers(dest="backtest_command", required=True)

    probe = actions.add_parser(
        "probe", help="Find which past journals ipo.gov.uk still serves (polite, capped)"
    )
    probe.add_argument("--from", dest="first", help="Oldest journal to consider, e.g. 2025-001")
    probe.add_argument("--to", dest="last", help="Newest journal to consider (default: latest)")
    probe.add_argument("--max-requests", type=int, help="Cap on journal requests")
    probe.add_argument("--out", help="Output file (default reports/backtest/availability.json)")
    probe.add_argument(
        "--no-open-data", action="store_true", help="Skip the IPO Open Data route check"
    )

    ingest = actions.add_parser(
        "ingest",
        help="Run past journals through the pipeline (no web search, no LLM, no live domain layer)",
    )
    ingest.add_argument("--from", dest="first", required=True, help="First journal, e.g. 2026-010")
    ingest.add_argument("--to", dest="last", required=True, help="Last journal, e.g. 2026-036")
    ingest.add_argument("--source", help="Journal source: ukipo_http | local | open_data | fixture")
    ingest.add_argument("--force", action="store_true", help="Reprocess processed journals")
    ingest.add_argument("--max-journals", type=int, help="Journals to process in this invocation")
    ingest.add_argument(
        "--with-domain-layer",
        action="store_true",
        help="Run the live domain layer too (describes the present: not point-in-time safe)",
    )

    label = actions.add_parser("label", help="Label brand outcomes at +3/+6 months")
    label.add_argument("--as-of", help="Label as of this date, YYYY-MM-DD (default today)")

    report = actions.add_parser("report", help="Precision/recall report by band and indicator")
    report.add_argument("--out", help="Output directory (default reports/backtest)")
    report.add_argument("--note", help="Free text shown at the top of the report")

    run = actions.add_parser("run", help="label + report on the current database")
    run.add_argument("--as-of", help="Label as of this date, YYYY-MM-DD (default today)")
    run.add_argument("--out", help="Output directory (default reports/backtest)")
    run.add_argument("--note", help="Free text shown at the top of the report")


def _database_label() -> str:
    from src.settings import get_settings

    url = get_settings().database_url
    if url.startswith("sqlite"):
        path = Path(url.split("///", 1)[-1])
        try:
            return f"sqlite:{path.resolve().relative_to(REPO_ROOT)}"
        except ValueError:
            return f"sqlite:{path.name}"
    return url.split("://", 1)[0] + "://(configured DATABASE_URL)"


def _label(as_of: str | None) -> int:
    from src.backtest.labeller import format_labels, label_brands
    from src.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        summary = label_brands(session, as_of=date.fromisoformat(as_of) if as_of else None)
        print(format_labels(summary))
    return 0


def _report(out: str | None, note: str | None = None) -> int:
    from src.backtest.report import generate_report
    from src.db import init_db, session_scope

    init_db()
    with session_scope() as session:
        md, js, payload = generate_report(
            session,
            out_dir=Path(out) if out else None,
            database_label=_database_label(),
            note=note,
        )
    for h, r in payload["results"]["horizons"].items():
        print(
            f"+{h} months: {r['labelled']} labelled ({r['launched']} launched), "
            f"{r['unknown']} unknown (excluded)" + ("  [SMALL SAMPLE]" if r["small_sample"] else "")
        )
    print(f"Report: {md}")
    print(f"JSON:   {js}")
    return 0


def cmd_backtest(args) -> int:  # type: ignore[no-untyped-def]
    action = args.backtest_command
    if action == "probe":
        from src.backtest.probe import format_probe, run_probe

        payload = run_probe(
            first=args.first,
            last=args.last,
            max_requests=args.max_requests,
            out_path=Path(args.out) if args.out else None,
            check_open_data_route=False if args.no_open_data else None,
        )
        print(format_probe(payload))
        return 0 if payload["earliest_available"] else 1
    if action == "ingest":
        from src.backtest.ingest import PROCESSED, SKIPPED, format_ingest, run_ingest

        outcomes = run_ingest(
            args.first,
            args.last,
            source=args.source,
            force=args.force,
            max_journals=args.max_journals,
            with_domain_layer=True if args.with_domain_layer else None,
        )
        print(format_ingest(outcomes))
        failed = [o for o in outcomes if o.status not in (PROCESSED, SKIPPED, "deferred_by_cap")]
        return 1 if failed and len(failed) == len(outcomes) else 0
    if action == "label":
        return _label(args.as_of)
    if action == "report":
        return _report(args.out, args.note)
    if action == "run":
        _label(args.as_of)
        return _report(args.out, args.note)
    raise ValueError(f"unknown backtest action {action!r}")  # pragma: no cover
